<#
=======================================================================================================================
  ci.ps1 - one command for the whole test matrix on Windows; writes ci_report.json.

  Interpreter: PowerShell 7 (pwsh). A Windows PowerShell 5.1 launch re-executes itself under pwsh.

  USAGE
    pwsh -File windows\ci.ps1                                         # every default group
    pwsh -File windows\ci.ps1 -Only audit                             # one group, or single suites by name
    pwsh -File windows\ci.ps1 -Skip pcb
    pwsh -File windows\ci.ps1 -Repo <worktree>                        # test another checkout (an agent's worktree)
    pwsh -File windows\ci.ps1 -Compare <old ci_report.json> -FailOn regressions    # the merge / promotion gate

  GROUPS -> SUITES (the $groups registry below is the single source; add a group = add a row + a runner script)
    audit   windows\ci\audit.ps1   audit_placeholders portability
    pcb     windows\ci\pcb.ps1     pcb_erc pcb_drc                     (every pcb\<board>\kicad\<board>.kicad_pro)

  Each group is its own pwsh process; groups run in parallel, "late" groups start after the rest have finished (use it
  for suites that consume what earlier groups build). "default = $false" groups run only when -Only names them.

  OUTPUT  <Out>\ci_report.json, <Out>\logs\*.log, <Out>\results\<group>.json, and a copy of the report at
          <repo>\.tmp\ci\ci_report.json. Default <Out> = <repo>\.tmp\ci\<yyyyMMdd_HHmmss> (gitignored).
          Nothing is written under the user profile: Initialize-CiEnvironment points TEMP, caches and KiCad's user
          folders at <Out>. CI never touches the GPU.

  -Compare  A suite regresses when its status goes from pass to anything else, or its passed count drops. The earlier
            run's audit JSON is handed to this run's audit as --baseline, so an audit regression is "a NEW unwaived
            high / medium finding", not "the count grew".

  EXIT CODE  0 green (no fail / build_fail / error; -Strict also counts "unavailable") or, with -FailOn regressions,
             nothing regressed against -Compare. 1 otherwise. 2 = bad arguments.
=======================================================================================================================
#>
param(
  [string]$Repo = '',
  [string]$Out = '',
  [string[]]$Only = @(),
  [string[]]$Skip = @(),
  [string]$Compare = '',
  [ValidateSet('any', 'regressions')][string]$FailOn = 'any',
  [switch]$Strict,
  [int]$TimeoutMin = 60
)

# ---------------------------------------------------------------------------------------------------------------------
#  Windows PowerShell 5.1 -> re-run under pwsh 7 (the runners use PS7 APIs and encodings)
# ---------------------------------------------------------------------------------------------------------------------
if ($PSVersionTable.PSVersion.Major -lt 7) {
  $pw = Get-Command pwsh -ErrorAction SilentlyContinue
  if (-not $pw) { Write-Output 'ci.ps1 needs PowerShell 7 (pwsh): winget install Microsoft.PowerShell'; exit 2 }
  $fwd = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
  foreach ($k in $PSBoundParameters.Keys) {
    $v = $PSBoundParameters[$k]
    if ($v -is [switch]) { if ($v.IsPresent) { $fwd += "-$k" } }
    elseif ($v -is [array]) { $fwd += "-$k"; $fwd += ($v -join ',') }
    else { $fwd += "-$k"; $fwd += "$v" }
  }
  & $pw.Source @fwd
  exit $LASTEXITCODE
}

# the orchestrator itself must never half-run: any PowerShell error stops it (runner failures live in their jobs)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
Import-Module (Join-Path $PSScriptRoot 'ci\ci_common.psm1') -Force

# ---- "a,b" strings (from pwsh -File) become arrays; @() keeps empty lists as arrays
function Split-List($v) { @($v | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
$Only = @(Split-List $Only); $Skip = @(Split-List $Skip)

# ---- repo and output folder
if (-not $Repo) { $Repo = Split-Path -Parent $PSScriptRoot }
if (-not (Test-Path (Join-Path $Repo 'windows\ci.ps1'))) { Write-Output "ci.ps1: -Repo '$Repo' has no windows\ci.ps1"; exit 2 }
$Repo = (Resolve-Path $Repo).Path
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
if (-not $Out) { $Out = Join-Path $Repo ".tmp\ci\$stamp" }
$userRoot = [Environment]::GetFolderPath('UserProfile')
if ($userRoot -and $Out.StartsWith($userRoot, [StringComparison]::OrdinalIgnoreCase)) {
  Write-Output "ci.ps1: refusing -Out under the user profile: $Out"; exit 2
}
New-Item -ItemType Directory -Force $Out, (Join-Path $Out 'logs'), (Join-Path $Out 'results') | Out-Null
$Out = (Resolve-Path $Out).Path
Initialize-CiEnvironment $Out

# ---------------------------------------------------------------------------------------------------------------------
#  Group registry: group -> runner script, suites, late (start after the others), default (part of a plain run)
# ---------------------------------------------------------------------------------------------------------------------
$groups = [ordered]@{
  audit = @{ script = 'audit.ps1'; suites = @('audit_placeholders', 'portability'); late = $false; default = $true }
  pcb   = @{ script = 'pcb.ps1';   suites = @('pcb_erc', 'pcb_drc');                late = $false; default = $true }
}
$known = @($groups.Keys) + @($groups.Values | ForEach-Object { $_.suites })
$bad = @(@($Only) + @($Skip) | Where-Object { $known -notcontains $_ })
if ($bad.Count -gt 0) { Write-Output "ci.ps1: unknown group/suite: $($bad -join ', '). Known: $($known -join ' ')"; exit 2 }

# ---- which suites run: the default groups, or exactly what -Only names, minus -Skip
$plan = [ordered]@{}
foreach ($g in $groups.Keys) {
  $def = $groups[$g]; $on = @()
  foreach ($s in $def.suites) {
    $chosen = if ($Only.Count -gt 0) { ($Only -contains $s) -or ($Only -contains $g) } else { $def.default }
    if ($chosen -and -not (($Skip -contains $s) -or ($Skip -contains $g))) { $on += $s }
  }
  $plan[$g] = $on
}

# ---------------------------------------------------------------------------------------------------------------------
#  -Compare is read up front: a bad path is an argument error before anything runs; the earlier audit report becomes
#  this run's audit baseline
# ---------------------------------------------------------------------------------------------------------------------
function Get-Prop($Obj, [string]$Name) {
  # property of a JSON object or dictionary, or $null (StrictMode-safe)
  if ($null -eq $Obj) { return $null }
  if ($Obj -is [System.Collections.IDictionary]) { if ($Obj.Contains($Name)) { return $Obj[$Name] } else { return $null } }
  $p = $Obj.PSObject.Properties[$Name]
  if ($p) { return $p.Value } else { return $null }
}
$old = $null; $auditBaseline = ''
if ($Compare) {
  if (-not (Test-Path $Compare)) { Write-Output "ci.ps1: -Compare file not found: $Compare"; exit 2 }
  try { $old = Get-Content $Compare -Raw -Encoding utf8 | ConvertFrom-Json } catch { Write-Output "ci.ps1: -Compare is not JSON: $Compare"; exit 2 }
  $rep = Get-Prop (Get-Prop (Get-Prop (Get-Prop $old 'suites') 'audit_placeholders') 'extra') 'report'
  if ($rep -and (Test-Path "$rep")) { $auditBaseline = "$rep" }
}

# ---------------------------------------------------------------------------------------------------------------------
#  Launch: one background job (one pwsh process) per active group; late groups after the early ones finish
# ---------------------------------------------------------------------------------------------------------------------
$t0 = Get-Date
Write-Output ("ci.ps1  repo {0}`n        out  {1}" -f $Repo, $Out)
function Start-Group([string]$g) {
  $def = $groups[$g]
  $p = @{ Repo = $Repo; Out = $Out; Only = @($plan[$g]); TimeoutMin = $TimeoutMin }
  if ($g -eq 'audit' -and $auditBaseline) { $p.AuditBaseline = $auditBaseline }
  $script = Join-Path $PSScriptRoot "ci\$($def.script)"
  Write-Host ("  start {0,-10} {1}" -f $g, ($plan[$g] -join ' '))
  return Start-Job -Name "ci_$g" -WorkingDirectory $Repo -ArgumentList $script, $p -ScriptBlock { param($s, $h) & $s @h }
}
function Wait-Groups($jobs) {
  foreach ($j in $jobs) {
    $null = Wait-Job $j
    # runner errors are data here (kept in the group log), never a reason to stop the orchestrator
    $o = @(Receive-Job $j -ErrorAction Continue 2>&1 | ForEach-Object { "$_" })
    $g = $j.Name -replace '^ci_', ''
    $o | Out-File -Encoding utf8 (Join-Path $Out "logs\group_$g.out")
    $o | Where-Object { $_ -match '^\[' } | ForEach-Object { Write-Output "  $_" }
    Remove-Job $j -Force
  }
}
$early = @($plan.Keys | Where-Object { $plan[$_].Count -gt 0 -and -not $groups[$_].late })
$late  = @($plan.Keys | Where-Object { $plan[$_].Count -gt 0 -and $groups[$_].late })
Wait-Groups @($early | ForEach-Object { Start-Group $_ })
foreach ($g in $late) { Wait-Groups @(Start-Group $g) }

# ---------------------------------------------------------------------------------------------------------------------
#  Aggregate: a selected suite whose runner wrote nothing is an error, never silently green
# ---------------------------------------------------------------------------------------------------------------------
$suites = [ordered]@{}
foreach ($g in $groups.Keys) {
  $file = Join-Path $Out "results\$g.json"
  $got = @()
  if (Test-Path $file) { try { $got = @(Get-Content $file -Raw -Encoding utf8 | ConvertFrom-Json) } catch { $got = @() } }
  foreach ($s in $groups[$g].suites) {
    $hit = $got | Where-Object { $_.name -eq $s } | Select-Object -First 1
    if ($hit) { $suites[$s] = $hit }
    elseif ($plan[$g] -contains $s) {
      $suites[$s] = [pscustomobject]@{ name = $s; group = $g; status = 'error'; exit = $null; duration_s = 0; passed = $null; failed = $null
                                       total = $null; summary = "runner wrote no result (see logs\group_$g.out)"; command = ''; log = ''; extra = @{} }
    } else {
      $suites[$s] = [pscustomobject]@{ name = $s; group = $g; status = 'skipped'; exit = $null; duration_s = 0; passed = $null; failed = $null
                                       total = $null; summary = 'not selected'; command = ''; log = ''; extra = @{} }
    }
  }
}
$count = [ordered]@{}
foreach ($st in 'pass', 'fail', 'build_fail', 'error', 'unavailable', 'skipped') { $count[$st] = @($suites.Values | Where-Object { $_.status -eq $st }).Count }
$redSt = @('fail', 'build_fail', 'error') + $(if ($Strict) { @('unavailable') } else { @() })
$red = @($suites.Values | Where-Object { $redSt -contains $_.status })
$ran = @($suites.Values | Where-Object { $_.status -ne 'skipped' }).Count
$verdict = if ($ran -gt 0 -and $red.Count -eq 0) { 'green' } else { 'red' }     # nothing ran is never green

# ---- git facts for the merge log (all output read before taking the first line, so git is never cut off mid-write)
function Q([string[]]$a) { try { $o = @(& git -C $Repo @a 2>$null); if ($LASTEXITCODE -eq 0 -and $o.Count -gt 0) { "$($o[0])".Trim() } else { '' } } catch { '' } }
$git = [ordered]@{ head = Q @('rev-parse', 'HEAD'); branch = Q @('branch', '--show-current'); subject = Q @('log', '-1', '--format=%s')
                   dirty = @(& git -C $Repo status --porcelain 2>$null).Count }

# ---------------------------------------------------------------------------------------------------------------------
#  Compare against an earlier report
# ---------------------------------------------------------------------------------------------------------------------
$cmp = $null
if ($Compare) {
  $reg = @(); $fix = @()
  foreach ($s in $suites.Keys) {
    $n = $suites[$s]; $o = Get-Prop (Get-Prop $old 'suites') $s
    if ($null -eq $o) { continue }
    $ost = Get-Prop $o 'status'; $nst = Get-Prop $n 'status'
    if ($nst -eq 'skipped' -or $ost -eq 'skipped') { continue }
    $why = @()
    if ($ost -eq 'pass' -and $nst -ne 'pass') { $why += "status $ost -> $nst" }
    $op = Get-Prop $o 'passed'; $np = Get-Prop $n 'passed'
    if ($null -ne $op -and $null -ne $np -and [int]$np -lt [int]$op) { $why += "passed $op -> $np" }
    # the audit: a NEW unwaived high / medium finding is a regression even when the suite itself is report-only
    $ng = Get-Prop (Get-Prop $n 'extra') 'new_gate'
    if ($s -eq 'audit_placeholders' -and $null -ne $ng -and [int]$ng -gt 0) { $why += "$ng new unwaived high/medium finding(s)" }
    if ($why.Count -gt 0) { $reg += [pscustomobject]@{ suite = $s; why = ($why -join '; ') } }
    elseif ($ost -ne 'pass' -and $nst -eq 'pass') { $fix += [pscustomobject]@{ suite = $s; why = "status $ost -> pass" } }
  }
  $cmp = [ordered]@{ against = (Resolve-Path $Compare).Path; against_head = (Get-Prop (Get-Prop $old 'git') 'head'); regressions = $reg; fixed = $fix }
}

# ---------------------------------------------------------------------------------------------------------------------
#  Report
# ---------------------------------------------------------------------------------------------------------------------
$report = [ordered]@{
  schema = 'hil-ci/1'; started = $t0.ToString('o'); finished = (Get-Date).ToString('o')
  duration_s = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1); repo = $Repo; out = $Out; git = $git
  host = [ordered]@{ machine = $env:COMPUTERNAME; cpus = [Environment]::ProcessorCount; pwsh = $PSVersionTable.PSVersion.ToString() }
  args = [ordered]@{ only = $Only; skip = $Skip; strict = [bool]$Strict; fail_on = $FailOn }
  verdict = $verdict; totals = $count; red = @($red | ForEach-Object { $_.name }); compare = $cmp; suites = $suites
}
$json = ConvertTo-Json -InputObject $report -Depth 10
[System.IO.File]::WriteAllText((Join-Path $Out 'ci_report.json'), $json, [System.Text.UTF8Encoding]::new($false))
$latest = Join-Path $Repo '.tmp\ci'
New-Item -ItemType Directory -Force $latest | Out-Null
[System.IO.File]::WriteAllText((Join-Path $latest 'ci_report.json'), $json, [System.Text.UTF8Encoding]::new($false))

# ---- console table
Write-Output ''
Write-Output ('{0,-20} {1,-11} {2,-10} {3,7}  {4}' -f 'suite', 'status', 'count', 'time_s', 'summary')
foreach ($s in $suites.Values) {
  if ($s.status -eq 'skipped') { continue }
  $c = if ($null -ne $s.total) { "$($s.passed)/$($s.total)" } elseif ($null -ne $s.passed) { "$($s.passed)" } else { '' }
  $sum = "$($s.summary)"; if ($sum.Length -gt 100) { $sum = $sum.Substring(0, 97) + '...' }
  Write-Output ('{0,-20} {1,-11} {2,-10} {3,7}  {4}' -f $s.name, $s.status, $c, $s.duration_s, $sum)
}
Write-Output ''
Write-Output ("verdict {0}   pass {1}  fail {2}  build_fail {3}  error {4}  unavailable {5}  skipped {6}   ({7:N0} s)" -f $verdict.ToUpper(),
  $count.pass, $count.fail, $count.build_fail, $count.error, $count.unavailable, $count.skipped, $report.duration_s)
if ($cmp) {
  Write-Output ("compare vs {0}: {1} regressions, {2} fixed" -f $cmp.against_head, @($cmp.regressions).Count, @($cmp.fixed).Count)
  foreach ($x in $cmp.regressions) { Write-Output "  REGRESSION $($x.suite): $($x.why)" }
  foreach ($x in $cmp.fixed) { Write-Output "  fixed      $($x.suite): $($x.why)" }
}
Write-Output "report  $(Join-Path $Out 'ci_report.json')"

if ($FailOn -eq 'regressions') {
  if (-not $cmp) { Write-Output 'ci.ps1: -FailOn regressions needs -Compare <ci_report.json>'; exit 2 }
  exit $(if (@($cmp.regressions).Count -eq 0) { 0 } else { 1 })
}
exit $(if ($verdict -eq 'green') { 0 } else { 1 })
