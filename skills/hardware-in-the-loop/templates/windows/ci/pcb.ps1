<#
=======================================================================================================================
  pcb.ps1 - CI group "pcb": re-checks every committed board with kicad-cli at KiCad's DEFAULT severities.

  Interpreter: PowerShell 7 (pwsh). Started by windows\ci.ps1 as its own process; can also run alone:
      pwsh -File windows\ci\pcb.ps1 -Repo <repo> -Out <dir> [-Only pcb_erc]

  Boards: every pcb\<board>\kicad\<board>.kicad_sch / .kicad_pcb pair (the layout the generator pipeline writes).
  Suites
    pcb_erc   kicad-cli sch erc --severity-all --format json   -> pass only with 0 errors AND 0 warnings on every board
    pcb_drc   kicad-cli pcb drc --severity-all --schematic-parity --format json
              -> pass only with 0 violations, 0 unconnected items and 0 parity items on every board
  The project's rule severities are never edited here or anywhere: a rule is fixed in copper, not demoted.
  No board in the tree yet = "unavailable" (red only under -Strict). No kicad-cli = "error" with the place searched.
=======================================================================================================================
#>
param(
  [Parameter(Mandatory)][string]$Repo,
  [Parameter(Mandatory)][string]$Out,
  [string[]]$Only = @(),
  [string[]]$Skip = @(),
  [int]$TimeoutMin = 60
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
Import-Module (Join-Path $PSScriptRoot 'ci_common.psm1') -Force
Initialize-CiEnvironment $Out

$group = 'pcb'
$results = @()
$cli = Get-CiTool 'kicadcli'

# ---- boards: pcb\<board>\kicad\<board>.kicad_pcb with its schematic beside it (Get-ChildItem over a known parent only)
$boards = @()
$pcbRoot = Join-Path $Repo 'pcb'
if (Test-Path $pcbRoot) {
  foreach ($d in Get-ChildItem -Path $pcbRoot -Directory -ErrorAction SilentlyContinue) {
    $k = Join-Path $d.FullName 'kicad'
    if (-not (Test-Path $k)) { continue }
    foreach ($p in Get-ChildItem -Path $k -Filter '*.kicad_pcb' -File -ErrorAction SilentlyContinue) {
      $sch = [IO.Path]::ChangeExtension($p.FullName, '.kicad_sch')
      $boards += [pscustomobject]@{ name = $p.BaseName; pcb = $p.FullName; sch = $(if (Test-Path $sch) { $sch } else { '' }) }
    }
  }
}

function Read-Json([string]$Path) {
  # parsed JSON or $null (a report kicad-cli did not write is a failure of the step, handled by the caller)
  if (-not (Test-Path $Path)) { return $null }
  try { return Get-Content $Path -Raw -Encoding utf8 | ConvertFrom-Json } catch { return $null }
}

# ---------------------------------------------------------------------------------------------------------------------
#  One suite = one kicad-cli check over every board; per-board numbers go to extra.boards
# ---------------------------------------------------------------------------------------------------------------------
foreach ($suite in 'pcb_erc', 'pcb_drc') {
  if (-not (Test-CiSelected $suite $group $Only $Skip)) { continue }
  $log = Join-Path $Out "logs\$suite.log"
  $r = New-CiResult $suite $group $log
  if (-not $cli) { $r.summary = 'kicad-cli not found (install KiCad 10 or set HIL_KICAD_CLI)'; $results += $r; continue }
  if ($boards.Count -eq 0) { $r.status = 'unavailable'; $r.summary = 'no pcb\<board>\kicad\*.kicad_pcb in this tree'; $results += $r; continue }
  $clean = 0; $per = [ordered]@{}; $lastRun = $null; $dur = 0.0
  foreach ($b in $boards) {
    $json = Join-Path $Out "$suite`_$($b.name).json"
    if ($suite -eq 'pcb_erc') {
      if (-not $b.sch) { $per[$b.name] = 'no schematic'; continue }
      $lastRun = Invoke-CiCommand -Exe $cli -Arguments @('sch', 'erc', '--format', 'json', '--severity-all', '--units', 'mm', '-o', $json, $b.sch) `
        -Log $log -TimeoutMin $TimeoutMin
      $dur += $lastRun.duration_s
      $j = Read-Json $json
      if ($null -eq $j) { $per[$b.name] = 'no report written'; continue }
      $v = @($j.sheets | ForEach-Object { $_.violations } | Where-Object { $_ })
      $e = @($v | Where-Object { $_.severity -eq 'error' }).Count; $w = @($v | Where-Object { $_.severity -eq 'warning' }).Count
      $per[$b.name] = "errors $e warnings $w"
      if ($e -eq 0 -and $w -eq 0) { $clean++ }
    } else {
      $lastRun = Invoke-CiCommand -Exe $cli -Arguments @('pcb', 'drc', '--format', 'json', '--severity-all', '--schematic-parity', '--units', 'mm', '-o', $json, $b.pcb) `
        -Log $log -TimeoutMin $TimeoutMin
      $dur += $lastRun.duration_s
      $j = Read-Json $json
      if ($null -eq $j) { $per[$b.name] = 'no report written'; continue }
      $v = @($j.violations).Count; $u = @($j.unconnected_items).Count; $pa = @($j.schematic_parity).Count
      $per[$b.name] = "violations $v unconnected $u parity $pa"
      if ($v -eq 0 -and $u -eq 0 -and $pa -eq 0) { $clean++ }
    }
  }
  # kicad-cli exits 5 when it found violations; the JSON is the verdict, so the record uses a synthetic exit code
  $run = [pscustomobject]@{ exit = $(if ($clean -eq $boards.Count) { 0 } else { 1 }); duration_s = $dur
                            lines = @(); command = $(if ($lastRun) { $lastRun.command } else { '' }) }
  Complete-CiResult -Result $r -Run $run -Passed $clean -Total $boards.Count -SummaryFound $true `
    -Summary (($per.Keys | ForEach-Object { "$_ : $($per[$_])" }) -join '; ')
  $r.extra.boards = $per
  $results += $r
}

Write-CiResults $Out $group $results
foreach ($x in $results) { Write-Output ("[{0}] {1} {2}" -f $x.name, $x.status, $x.summary) }
