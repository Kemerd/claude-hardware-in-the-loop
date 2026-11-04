<#
=======================================================================================================================
  twin.ps1 - CI group "twin": the native digital twin (sim/twin) built out of tree, its host suites, the scenario
             matrix over seeds, the determinism check and the C ABI check from Python.

  Interpreter: PowerShell 7 (pwsh). Started by windows\ci.ps1 as its own process; can also run alone:
      pwsh -File windows\ci\twin.ps1 -Repo <repo> -Out <dir> [-Only twin_scenarios]

  Suites
    twin_build        cmake configure + build into <Out>\twin_build (never into the source tree). Compiler: HIL_CC /
                      HIL_CXX when set, else clang from PATH or the LLVM default folder, else what CMake finds.
                      Generator: Ninja (HIL_NINJA, PATH, or the copy Visual Studio ships), else CMake's default.
    twin_tests        test_twin_models, test_hil_core, test_twin_api; each must print its "[hil] <suite>: P/T checks
                      passed, F failed" line (exit 0 without it is a failure).
    twin_scenarios    every row of sim\twin\scenarios\MATRIX.txt over seeds 1..N; one "[twin] ... PASS|FAIL" line per run.
    twin_determinism  the first matrix row twice at seed 1: the two trace files must have the same SHA-256.
    twin_abi          python -I sim\twin\tests\test_twin_ctypes.py: the C ABI from another language (the way the Isaac
                      harness and notebooks load it) plus host parity: the first matrix row played from Python must
                      give the native runner's trace hash, and the Python judge must agree with its verdict.
  CI is CPU only (Initialize-CiEnvironment hides the GPU); the CUDA backend is exercised by its own gated script.
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

$group = 'twin'
$results = @()
$twin = Join-Path $Repo 'sim\twin'
$build = Join-Path $Out 'twin_build'
$runs = Join-Path $Out 'twin_runs'
New-Item -ItemType Directory -Force $build, $runs | Out-Null

# ---------------------------------------------------------------------------------------------------------------------
#  Tool discovery (each returns a path or $null; a missing tool makes the suite an "error" with the reason)
# ---------------------------------------------------------------------------------------------------------------------
function Find-Exe([string]$EnvName, [string]$PathName, [string[]]$Fallbacks) {
  $v = [Environment]::GetEnvironmentVariable($EnvName)
  if ($v -and (Test-Path $v)) { return $v }
  $c = Get-Command $PathName -ErrorAction SilentlyContinue
  if ($c) { return $c.Source }
  foreach ($f in $Fallbacks) { if ($f -and (Test-Path $f)) { return $f } }
  return $null
}
$cmake = Find-Exe 'HIL_CMAKE' 'cmake' @((Join-Path $env:ProgramFiles 'CMake\bin\cmake.exe'))
$llvm = Join-Path $env:ProgramFiles 'LLVM\bin'
$cc = Find-Exe 'HIL_CC' 'clang' @((Join-Path $llvm 'clang.exe'))
$cxx = Find-Exe 'HIL_CXX' 'clang++' @((Join-Path $llvm 'clang++.exe'))
$vsNinja = @(Get-ChildItem -Path (Join-Path ${env:ProgramFiles} 'Microsoft Visual Studio') -Recurse -Filter ninja.exe `
             -ErrorAction SilentlyContinue -Depth 9 | Select-Object -First 1 | ForEach-Object { $_.FullName })
$ninja = Find-Exe 'HIL_NINJA' 'ninja' $vsNinja
$py = Get-CiTool 'python'

# ---- an executable from the build tree (single-config generators put it at the top, multi-config in Release\)
function Find-Built([string]$Name) {
  foreach ($p in @((Join-Path $build "$Name.exe"), (Join-Path $build "Release\$Name.exe"), (Join-Path $build $Name),
                   (Join-Path $build "$Name.dll"), (Join-Path $build "Release\$Name.dll"), (Join-Path $build "$Name.so"))) {
    if (Test-Path $p) { return $p }
  }
  return $null
}

# ---------------------------------------------------------------------------------------------------------------------
#  twin_build
# ---------------------------------------------------------------------------------------------------------------------
$built = $false
if (Test-CiSelected 'twin_build' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\twin_build.log'
  $r = New-CiResult 'twin_build' $group $log
  if (-not $cmake) { $r.summary = 'cmake not found (set HIL_CMAKE)' }
  elseif (-not (Test-Path (Join-Path $twin 'CMakeLists.txt'))) { $r.summary = "missing $twin\CMakeLists.txt" }
  else {
    $cfg = @('-S', $twin, '-B', $build, '-DCMAKE_BUILD_TYPE=Release')
    # ---- CMake reads these back as CMake strings: forward slashes, or a "\P" in "C:\Program Files" is an escape error
    function To-CMakePath([string]$p) { return $p.Replace([char]92, [char]47) }
    if ($ninja) { $cfg += @('-G', 'Ninja', "-DCMAKE_MAKE_PROGRAM=$(To-CMakePath $ninja)") }
    if ($cc) { $cfg += "-DCMAKE_C_COMPILER=$(To-CMakePath $cc)" }
    if ($cxx) { $cfg += "-DCMAKE_CXX_COMPILER=$(To-CMakePath $cxx)" }
    if ($cc -and $cc -like '*LLVM*') { $cfg += "-DCMAKE_RC_COMPILER=$(To-CMakePath (Join-Path (Split-Path $cc) 'llvm-rc.exe'))" }
    $run = Invoke-CiCommand -Exe $cmake -Arguments $cfg -Cwd $Repo -Log $log -TimeoutMin $TimeoutMin
    if ($run.exit -eq 0) {
      $run2 = Invoke-CiCommand -Exe $cmake -Arguments @('--build', $build, '--config', 'Release') -Cwd $Repo -Log $log -TimeoutMin $TimeoutMin
      $warn = @($run2.lines | Where-Object { $_ -match '\bwarning\b' }).Count
      $r.extra.warnings = $warn
      Complete-CiResult -Result $r -Run $run2 -Passed $(if ($run2.exit -eq 0) { 1 } else { 0 }) -Total 1 `
        -SummaryFound $true -Summary $(if ($run2.exit -eq 0) { "built, $warn warning lines" } else { 'build failed' })
      $built = ($run2.exit -eq 0)
    } else {
      Complete-CiResult -Result $r -Run $run -Passed 0 -Total 1 -SummaryFound $true -Summary 'cmake configure failed'
    }
  }
  $results += $r
} else {
  $built = [bool](Find-Built 'hil_twin')
}

# ---------------------------------------------------------------------------------------------------------------------
#  twin_tests
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'twin_tests' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\twin_tests.log'
  $r = New-CiResult 'twin_tests' $group $log
  if (-not $built) { $r.summary = 'twin_build did not produce binaries' }
  else {
    $params = (Resolve-Path (Join-Path $twin 'params')).Path
    $suites = @(@{ n = 'test_twin_models'; a = @($runs) }, @{ n = 'test_hil_core'; a = @() },
                @{ n = 'test_twin_api'; a = @($params, $runs) })
    $p = 0; $t = 0; $allFound = $true; $lastRun = $null; $parts = @()
    foreach ($s in $suites) {
      $exe = Find-Built $s.n
      if (-not $exe) { $allFound = $false; $parts += "$($s.n) missing"; continue }
      $lastRun = Invoke-CiCommand -Exe $exe -Arguments $s.a -Cwd $runs -Log $log -TimeoutMin $TimeoutMin
      $m = Find-CiMatch $lastRun.lines '^\[hil\] (\S+): (\d+)/(\d+) checks passed, (\d+) failed'
      if (-not $m -or $lastRun.exit -ne 0) { $allFound = $allFound -and [bool]$m; $parts += "$($s.n) exit $($lastRun.exit)" }
      if ($m) { $p += [int]$m.Groups[2].Value; $t += [int]$m.Groups[3].Value; $parts += $m.Value }
      if ($lastRun.exit -ne 0) { $r.exit = $lastRun.exit }
    }
    $agg = [pscustomobject]@{ exit = $(if ($r.exit) { $r.exit } else { 0 }); duration_s = 0; lines = @(); command = 'twin suites' }
    Complete-CiResult -Result $r -Run $agg -Passed $p -Total $t -SummaryFound ($allFound -and $t -gt 0) -Summary ($parts -join ' | ')
  }
  $results += $r
}

# ---------------------------------------------------------------------------------------------------------------------
#  Matrix rows (params, scenario, seeds) from MATRIX.txt
# ---------------------------------------------------------------------------------------------------------------------
$matrix = @()
$mf = Join-Path $twin 'scenarios\MATRIX.txt'
if (Test-Path $mf) {
  foreach ($line in (Get-Content $mf -Encoding utf8)) {
    $l = ($line -replace '#.*$', '').Trim()
    if (-not $l) { continue }
    $f = $l -split '\s+'
    if ($f.Count -ne 3) { continue }
    $matrix += [pscustomobject]@{ params = (Join-Path $twin "params\$($f[0])"); scenario = (Join-Path $twin "scenarios\$($f[1])"); seeds = [int]$f[2] }
  }
}

# ---------------------------------------------------------------------------------------------------------------------
#  twin_scenarios
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'twin_scenarios' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\twin_scenarios.log'
  $r = New-CiResult 'twin_scenarios' $group $log
  $exe = Find-Built 'hil_twin'
  if (-not $exe) { $r.summary = 'hil_twin not built' }
  elseif ($matrix.Count -eq 0) { $r.summary = "no rows in $mf (an empty matrix is a failure)" }
  else {
    $pass = 0; $total = 0; $bad = @(); $worst = 0
    foreach ($row in $matrix) {
      for ($seed = 1; $seed -le $row.seeds; $seed++) {
        $run = Invoke-CiCommand -Exe $exe -Arguments @($row.params, $row.scenario, '--seed', "$seed", '--out', $runs) `
          -Cwd $runs -Log $log -TimeoutMin $TimeoutMin
        $total++
        $m = Find-CiMatch $run.lines '^\[twin\] (\S+) seed (\d+): (PASS|FAIL)'
        if ($m -and $m.Groups[3].Value -eq 'PASS' -and $run.exit -eq 0) { $pass++ }
        else { $bad += "$([IO.Path]::GetFileName($row.params)):$([IO.Path]::GetFileName($row.scenario)) seed $seed"; $worst = 1 }
      }
    }
    $agg = [pscustomobject]@{ exit = $worst; duration_s = 0; lines = @(); command = "hil_twin x $total" }
    Complete-CiResult -Result $r -Run $agg -Passed $pass -Total $total -SummaryFound $true `
      -Summary ("{0}/{1} scenario runs pass{2}" -f $pass, $total, $(if ($bad) { '; failing: ' + ($bad -join ', ') } else { '' }))
  }
  $results += $r
}

# ---------------------------------------------------------------------------------------------------------------------
#  twin_determinism
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'twin_determinism' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\twin_determinism.log'
  $r = New-CiResult 'twin_determinism' $group $log
  $exe = Find-Built 'hil_twin'
  if (-not $exe -or $matrix.Count -eq 0) { $r.summary = 'hil_twin not built or empty matrix' }
  else {
    $row = $matrix[0]; $hashes = @(); $lastRun = $null
    foreach ($k in 'a', 'b') {
      $d = Join-Path $runs "det_$k"
      New-Item -ItemType Directory -Force $d | Out-Null
      $lastRun = Invoke-CiCommand -Exe $exe -Arguments @($row.params, $row.scenario, '--seed', '1', '--out', $d) -Cwd $d -Log $log -TimeoutMin $TimeoutMin
      $csv = @(Get-ChildItem $d -Filter '*_s1.csv' -ErrorAction SilentlyContinue | Select-Object -First 1)
      if ($csv.Count -eq 1) { $hashes += (Get-FileHash -Algorithm SHA256 $csv[0].FullName).Hash } else { $hashes += 'missing' }
    }
    $same = ($hashes.Count -eq 2 -and $hashes[0] -eq $hashes[1] -and $hashes[0] -ne 'missing')
    $r.extra.sha256 = $hashes
    $agg = [pscustomobject]@{ exit = $(if ($same) { 0 } else { 1 }); duration_s = $lastRun.duration_s; lines = @(); command = $lastRun.command }
    Complete-CiResult -Result $r -Run $agg -Passed $(if ($same) { 1 } else { 0 }) -Total 1 -SummaryFound $true `
      -Summary $(if ($same) { "trace SHA-256 equal: $($hashes[0].Substring(0, 16))" } else { "trace SHA-256 differ: $($hashes -join ' vs ')" })
  }
  $results += $r
}

# ---------------------------------------------------------------------------------------------------------------------
#  twin_abi (Python ctypes)
# ---------------------------------------------------------------------------------------------------------------------
if (Test-CiSelected 'twin_abi' $group $Only $Skip) {
  $log = Join-Path $Out 'logs\twin_abi.log'
  $r = New-CiResult 'twin_abi' $group $log
  $dll = Find-Built 'hil_twin_api'
  $test = Join-Path $twin 'tests\test_twin_ctypes.py'
  if (-not $py) { $r.summary = 'python not found (set HIL_PYTHON)' }
  elseif (-not $dll) { $r.summary = 'hil_twin_api shared library not built' }
  elseif (-not (Test-Path $test)) { $r.summary = "missing $test" }
  else {
    # ---- the native runner's hash of the first matrix row at seed 1: the Python host must reproduce it
    $pyArgs = @('-I', $test, '--dll', $dll, '--params', (Join-Path $twin 'params\example_rig.txt'))
    $exe = Find-Built 'hil_twin'
    if ($exe -and $matrix.Count -gt 0) {
      $nat = Invoke-CiCommand -Exe $exe -Arguments @($matrix[0].params, $matrix[0].scenario, '--seed', '1', '--no-trace') -Cwd $runs -Log $log -TimeoutMin $TimeoutMin
      $hm = Find-CiMatch $nat.lines 'trace fnv1a64=([0-9a-f]{16})'
      if ($hm) { $pyArgs = @('-I', $test, '--dll', $dll, '--params', $matrix[0].params, '--scenario', $matrix[0].scenario, '--expect-hash', $hm.Groups[1].Value) }
    }
    $run = Invoke-CiCommand -Exe $py -Arguments $pyArgs -Cwd $runs -Log $log -TimeoutMin $TimeoutMin
    $m = Find-CiMatch $run.lines '^\[hil\] twin_ctypes: (\d+)/(\d+) checks passed, (\d+) failed'
    Complete-CiResult -Result $r -Run $run -Passed $(if ($m) { [int]$m.Groups[1].Value } else { $null }) `
      -Total $(if ($m) { [int]$m.Groups[2].Value } else { $null }) -SummaryFound ([bool]$m) -Summary $(if ($m) { $m.Value } else { 'no summary line' })
  }
  $results += $r
}

Write-CiResults $Out $group $results
foreach ($x in $results) { Write-Output ("[{0}] {1} {2}" -f $x.name, $x.status, $x.summary) }
