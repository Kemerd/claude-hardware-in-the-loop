<#
=======================================================================================================================
  check_twin_tools.ps1 - finds (never installs) everything the digital twin tiers need and prints what is missing.

  Interpreter: PowerShell 7 (pwsh). Run: pwsh -File <skill>/scripts/check_twin_tools.ps1 [-Json <out.json>]

  Tier            needs                                                                 found through
  native twin     CMake >= 3.20, a C11 / C++17 compiler (clang, MSVC or gcc), Ninja     PATH, HIL_CMAKE / HIL_CC / HIL_CXX /
                  (optional), Python >= 3.9 for the tools                               HIL_NINJA, LLVM and VS default folders
  Unreal          Unreal Engine 5.8.4 or newer (launcher or source build), Visual        the Epic launcher's LauncherInstalled.dat,
                  Studio 2022 17.x / 2026 with "Game development with C++"             HIL_UE_ROOT, vswhere
  Isaac Sim       an Isaac Sim Python (pip "isaacsim" in its own venv, or the           HIL_ISAAC_PYTHON
                  launcher's python.bat), an RTX GPU and driver
  CUDA            the NVIDIA driver (nvidia-smi); the CUDA toolkit only to regenerate   PATH, CUDA_PATH, the default toolkit folder
                  the embedded PTX
  Output: one row per tool (found path or MISSING with the install hint) and a tier summary. Exit 0 always (it is a
  report); the agent shows it to the owner and asks before installing anything machine-wide.
=======================================================================================================================
#>
param([string]$Json = '')
$ErrorActionPreference = 'Stop'
$rows = [System.Collections.Generic.List[object]]::new()

function Add-Row([string]$Tier, [string]$Tool, $Path, [string]$Version, [string]$Hint) {
  $rows.Add([pscustomobject]@{ tier = $Tier; tool = $Tool; found = [bool]$Path; path = "$Path"; version = $Version; hint = $Hint })
}
function First-Path([string[]]$Candidates) {
  foreach ($c in $Candidates) { if ($c -and (Test-Path $c)) { return (Resolve-Path $c).Path } }
  return $null
}
function From-Path([string]$Name) { $c = Get-Command $Name -ErrorAction SilentlyContinue; if ($c) { return $c.Source } else { return $null } }
function Version-Of([string]$Exe, [string[]]$VArgs = @('--version')) {
  if (-not $Exe) { return '' }
  try { return ((& $Exe @VArgs 2>&1 | Select-Object -First 1) -as [string]).Trim() } catch { return '' }
}

# ---------------------------------------------------------------------------------------------------------------------
#  Native twin
# ---------------------------------------------------------------------------------------------------------------------
$pf = $env:ProgramFiles
$cmake = First-Path @($env:HIL_CMAKE, (From-Path 'cmake'), (Join-Path $pf 'CMake\bin\cmake.exe'))
Add-Row 'native' 'cmake' $cmake (Version-Of $cmake) 'winget install Kitware.CMake  (Linux: apt install cmake)'
$clang = First-Path @($env:HIL_CC, (From-Path 'clang'), (Join-Path $pf 'LLVM\bin\clang.exe'))
Add-Row 'native' 'clang (C11)' $clang (Version-Of $clang) 'winget install LLVM.LLVM  (or use MSVC / gcc: set HIL_CC and HIL_CXX)'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = $null
if (Test-Path $vswhere) { $vs = (& $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null | Select-Object -First 1) }
Add-Row 'native' 'Visual Studio C++ tools' $vs '' 'Visual Studio 2022 / 2026 with the "Desktop development with C++" workload (needed by Unreal and nvcc on Windows)'
$ninja = First-Path @($env:HIL_NINJA, (From-Path 'ninja'), $(if ($vs) { Join-Path $vs 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe' }))
Add-Row 'native' 'ninja (optional)' $ninja (Version-Of $ninja) 'winget install Ninja-build.Ninja, or the copy Visual Studio ships'
$py = First-Path @($env:HIL_PYTHON, (From-Path 'python'), (From-Path 'python3'))
Add-Row 'native' 'python >= 3.9' $py (Version-Of $py) 'winget install Python.Python.3.12'

# ---------------------------------------------------------------------------------------------------------------------
#  Unreal Engine (5.8.4 or newer)
# ---------------------------------------------------------------------------------------------------------------------
$ue = @()
if ($env:HIL_UE_ROOT -and (Test-Path (Join-Path $env:HIL_UE_ROOT 'Engine\Build\BatchFiles\Build.bat'))) { $ue += [pscustomobject]@{ root = $env:HIL_UE_ROOT; ver = 'HIL_UE_ROOT' } }
$dat = Join-Path $env:ProgramData 'Epic\UnrealEngineLauncher\LauncherInstalled.dat'
if (Test-Path $dat) {
  try {
    $j = Get-Content $dat -Raw -Encoding utf8 | ConvertFrom-Json
    foreach ($i in @($j.InstallationList)) {
      if ($i.AppName -like 'UE_*' -and (Test-Path (Join-Path $i.InstallLocation 'Engine\Build\BatchFiles\Build.bat'))) {
        $ue += [pscustomobject]@{ root = $i.InstallLocation; ver = "$($i.AppVersion)" }
      }
    }
  } catch { }
}
$best = $null
foreach ($u in $ue) {
  $m = [regex]::Match($u.ver, '^(\d+)\.(\d+)\.(\d+)')
  $ok = $u.ver -eq 'HIL_UE_ROOT' -or ($m.Success -and ([version]"$($m.Groups[1].Value).$($m.Groups[2].Value).$($m.Groups[3].Value)" -ge [version]'5.8.4'))
  Add-Row 'unreal' 'Unreal Engine' $u.root $u.ver $(if ($ok) { '' } else { 'older than 5.8.4: install 5.8.4 or newer next to it' })
  if ($ok -and -not $best) { $best = $u }
}
if ($ue.Count -eq 0) { Add-Row 'unreal' 'Unreal Engine' $null '' 'Epic Games Launcher > Unreal Engine > Library > + > 5.8.x (5.8.4 or newer); or a source build from GitHub (link the Epic account first); set HIL_UE_ROOT for a custom location' }

# ---------------------------------------------------------------------------------------------------------------------
#  Isaac Sim
# ---------------------------------------------------------------------------------------------------------------------
$isaac = First-Path @($env:HIL_ISAAC_PYTHON)
$iv = ''
if ($isaac) { try { $iv = (& $isaac -c "import importlib.metadata as m; print(m.version('isaacsim'))" 2>$null | Select-Object -First 1) } catch { } }
Add-Row 'isaac' 'Isaac Sim python' $isaac $iv 'a venv on a tools drive with the Python version the Isaac Sim release requires, then: pip install "isaacsim[all,extscache]==<version>" --extra-index-url https://pypi.nvidia.com ; set HIL_ISAAC_PYTHON to its python'

# ---------------------------------------------------------------------------------------------------------------------
#  GPU and CUDA
# ---------------------------------------------------------------------------------------------------------------------
$smi = First-Path @((From-Path 'nvidia-smi'), (Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'))
$gpu = ''
if ($smi) { try { $gpu = (& $smi --query-gpu=name,driver_version,memory.total --format=csv,noheader 2>$null | Select-Object -First 1) } catch { } }
Add-Row 'gpu' 'NVIDIA driver (nvidia-smi)' $smi $gpu 'the NVIDIA driver for the card; without it every GPU path falls back to the CPU'
$nvcc = First-Path @((From-Path 'nvcc'), $(if ($env:CUDA_PATH) { Join-Path $env:CUDA_PATH 'bin\nvcc.exe' }))
$nv = ''
if ($nvcc) { try { $nv = "$((& $nvcc --version 2>&1 | Select-String 'release' | Select-Object -First 1))".Trim() } catch { } }
Add-Row 'gpu' 'CUDA toolkit nvcc (only to regenerate PTX)' $nvcc $nv 'https://developer.nvidia.com/cuda-downloads (12.x or newer); not needed to build or run the twin'

# ---------------------------------------------------------------------------------------------------------------------
#  Report
# ---------------------------------------------------------------------------------------------------------------------
foreach ($r in $rows) {
  $state = if ($r.found) { 'found  ' } else { 'MISSING' }
  Write-Output ("{0,-7} {1,-42} {2} {3}" -f $r.tier, $r.tool, $state, $(if ($r.found) { "$($r.path)  $($r.version)" } else { "-> $($r.hint)" }))
}
$native = -not @($rows | Where-Object { $_.tier -eq 'native' -and -not $_.found -and $_.tool -notlike '*optional*' -and $_.tool -notlike 'Visual Studio*' })
Write-Output ''
Write-Output ("[twin-tools] native {0}; unreal {1}; isaac {2}; gpu {3}" -f
  $(if ($native) { 'ready' } else { 'missing tools' }),
  $(if ($best) { "ready ($($best.ver) at $($best.root))" } else { 'not ready' }),
  $(if ($isaac) { "ready ($iv)" } else { 'not ready' }),
  $(if ($smi) { 'driver present' } else { 'none' }))
if ($Json) { $rows | ConvertTo-Json -Depth 3 | Out-File -Encoding utf8 $Json }
exit 0
