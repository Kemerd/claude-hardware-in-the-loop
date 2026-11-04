<#
=======================================================================================================================
  run_gpu_check.ps1 - the gated GPU check of the thermal sweep: CUDA result vs the CPU reference on the same cases.

  Interpreter: PowerShell 7 (pwsh). Run (one job, opted in for this shell only):
      $env:HIL_GPU = '1'; pwsh -File sim\twin\gpu\run_gpu_check.ps1 -Build <twin build dir>; Remove-Item Env:HIL_GPU

  Never part of CI (CI hides the GPU). Steps: the opt-in must already be set by the caller; sim\isaac\gpu_gate.py
  must say open (exit 3 = closed: report "blocked" with its reason, do CPU work, retry later); then
  hil_twin_sweep --check must print "[hil] gpu_sweep: 2/2 checks passed".
=======================================================================================================================
#>
param([Parameter(Mandatory)][string]$Build, [int]$MinFreeMb = 2000)
$ErrorActionPreference = 'Stop'
if ($env:HIL_GPU -ne '1') { Write-Output 'run_gpu_check: HIL_GPU is not 1; opt in for this job only and run again'; exit 3 }
$repo = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
$py = if ($env:HIL_PYTHON) { $env:HIL_PYTHON } else { 'python' }
& $py -I (Join-Path $repo 'sim\isaac\gpu_gate.py') --min-free-mb $MinFreeMb
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$exe = @((Join-Path $Build 'hil_twin_sweep.exe'), (Join-Path $Build 'Release\hil_twin_sweep.exe'), (Join-Path $Build 'hil_twin_sweep')) |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $exe) { Write-Output "run_gpu_check: hil_twin_sweep not found under $Build"; exit 2 }
$out = & $exe --check
$out | ForEach-Object { Write-Output $_ }
if (($LASTEXITCODE -eq 0) -and ($out -match '^\[hil\] gpu_sweep: (\d+)/\1 checks passed')) { exit 0 }
exit 1
