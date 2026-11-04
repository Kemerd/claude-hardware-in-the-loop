<#
=======================================================================================================================
  audit.ps1 - CI group "audit": tools/audit_placeholders.py (unfinished work shown as finished) and the portability check.

  Interpreter: PowerShell 7 (pwsh). Started by windows\ci.ps1 as its own process; can also run alone:
      pwsh -File windows\ci\audit.ps1 -Repo <repo> -Out <dir> [-Only audit_placeholders] [-AuditBaseline <old audit json>]

  Suites
    audit_placeholders  python -I tools\audit_placeholders.py --json <Out>\audit_placeholders.json --fail-on-new
                        (against tools\audit_baseline.json, or -AuditBaseline when ci.ps1 -Compare hands one over).
                        Fails on a NEW unwaived high / medium finding. extra.new_gate carries the count for -Compare.
    portability         python -I tools\check_portability.py --fail-on-any (rules R1-R4; every script, every console)
    citations           python -I tools\check_citations.py: every [REF-nnn] tag in code has a verified CITATION.md row
=======================================================================================================================
#>
param(
  [Parameter(Mandatory)][string]$Repo,
  [Parameter(Mandatory)][string]$Out,
  [string[]]$Only = @(),
  [string[]]$Skip = @(),
  [string]$AuditBaseline = '',
  [int]$TimeoutMin = 60
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
Import-Module (Join-Path $PSScriptRoot 'ci_common.psm1') -Force
Initialize-CiEnvironment $Out

$group = 'audit'
$results = @()
$py = Get-CiTool 'python'

# ---------------------------------------------------------------------------------------------------------------------
#  audit_placeholders
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'audit_placeholders' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\audit_placeholders.log'
  $r = New-CiResult 'audit_placeholders' $group $log
  $tool = Join-Path $Repo 'tools\audit_placeholders.py'
  if (-not $py) { $r.summary = 'python not found (set HIL_PYTHON)' }
  elseif (-not (Test-Path $tool)) { $r.summary = "missing $tool" }
  else {
    $json = Join-Path $Out 'audit_placeholders.json'
    $a = @('-I', $tool, '--root', $Repo, '--json', $json, '--fail-on-new', '--quiet')
    if ($AuditBaseline) { $a += @('--baseline', $AuditBaseline) }
    $run = Invoke-CiCommand -Exe $py -Arguments $a -Cwd $Repo -Log $log -TimeoutMin $TimeoutMin
    # summary: "[audit] N files, F findings (W waived), high H medium M low L, new X, gating G"
    $m = Find-CiMatch $run.lines '^\[audit\] (\d+) files, (\d+) findings .* new (\d+), gating (\d+)'
    $gating = if ($m) { [int]$m.Groups[4].Value } else { $null }
    Complete-CiResult -Result $r -Run $run -Passed $(if ($m) { [int]$m.Groups[1].Value - $gating } else { $null }) `
      -Total $(if ($m) { [int]$m.Groups[1].Value } else { $null }) -Failed $gating -SummaryFound ([bool]$m) `
      -Summary $(if ($m) { $m.Value } else { 'no summary line' })
    # the numbers -Compare reads, plus the report path so the next run can use it as its baseline
    if (Test-Path $json) {
      try {
        $rep = Get-Content $json -Raw -Encoding utf8 | ConvertFrom-Json
        $r.extra.new_gate = [int]$rep.totals.new_gate
        $r.extra.by_severity = $rep.totals.by_severity
        $r.extra.report = $json
      } catch { $r.extra.parse_error = "$($_.Exception.Message)" }
    }
  }
  $results += $r
}

# ---------------------------------------------------------------------------------------------------------------------
#  portability
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'portability' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\portability.log'
  $r = New-CiResult 'portability' $group $log
  $tool = Join-Path $Repo 'tools\check_portability.py'
  if (-not $py) { $r.summary = 'python not found (set HIL_PYTHON)' }
  elseif (-not (Test-Path $tool)) { $r.summary = "missing $tool" }
  else {
    $run = Invoke-CiCommand -Exe $py -Arguments @('-I', $tool, '--root', $Repo, '--fail-on-any', '--json', (Join-Path $Out 'portability.json')) `
      -Cwd $Repo -Log $log -TimeoutMin $TimeoutMin
    # summary: "[portability] OK/N scripts portable"
    $m = Find-CiMatch $run.lines '^\[portability\] (\d+)/(\d+) scripts portable'
    Complete-CiResult -Result $r -Run $run -Passed $(if ($m) { [int]$m.Groups[1].Value } else { $null }) `
      -Total $(if ($m) { [int]$m.Groups[2].Value } else { $null }) -SummaryFound ([bool]$m) `
      -Summary $(if ($m) { $m.Value } else { 'no summary line' })
  }
  $results += $r
}

# ---------------------------------------------------------------------------------------------------------------------
#  citations
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'citations' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\citations.log'
  $r = New-CiResult 'citations' $group $log
  $tool = Join-Path $Repo 'tools\check_citations.py'
  if (-not $py) { $r.summary = 'python not found (set HIL_PYTHON)' }
  elseif (-not (Test-Path $tool)) { $r.summary = "missing $tool" }
  else {
    $run = Invoke-CiCommand -Exe $py -Arguments @('-I', $tool, '--root', $Repo, '--json', (Join-Path $Out 'citations.json')) `
      -Cwd $Repo -Log $log -TimeoutMin $TimeoutMin
    # summary: "[citations] N tags in F files, R rows, missing M, unverified U, malformed X, unused K"
    $m = Find-CiMatch $run.lines '^\[citations\] (\d+) tags in \d+ files, (\d+) rows, missing (\d+), unverified (\d+), malformed (\d+)'
    $bad = if ($m) { [int]$m.Groups[3].Value + [int]$m.Groups[4].Value + [int]$m.Groups[5].Value } else { $null }
    Complete-CiResult -Result $r -Run $run -Passed $(if ($m) { [int]$m.Groups[1].Value - [Math]::Min($bad, [int]$m.Groups[1].Value) } else { $null }) `
      -Total $(if ($m) { [int]$m.Groups[1].Value } else { $null }) -Failed $bad -SummaryFound ([bool]$m) `
      -Summary $(if ($m) { $m.Value } else { 'no summary line' })
  }
  $results += $r
}

Write-CiResults $Out $group $results
foreach ($x in $results) { Write-Output ("[{0}] {1} {2}" -f $x.name, $x.status, $x.summary) }
