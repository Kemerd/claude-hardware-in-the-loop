<#
=======================================================================================================================
  setup_tools.ps1 - installs or verifies the hardware-in-the-loop toolchain on Windows, with pinned versions and
                    checksums, never under the user profile, safe to run again (idempotent).

  Interpreter: PowerShell 7 (pwsh).
  Run:  pwsh -File scripts\setup_tools.ps1 -ToolsRoot D:\hiltools [-VerifyOnly] [-Skip kicad,java,...] [-WithEspIdf]
        -ToolsRoot   folder for everything this script downloads (jar, OSS CAD Suite, CadQuery venv, caches). It must
                     not be inside the user profile; a path there is refused.
        -VerifyOnly  install nothing; report what is present, its version, and what is missing (exit 1 if anything is)
        -Skip        any of: kicad, java, freerouting, osscad, cadquery
        -WithEspIdf  also print the ESP-IDF install steps (the installer is interactive and large, so it is not
                     automated here)

  What it sets up
    KiCad 10            winget KiCad.KiCad (machine-wide under Program Files); gives kicad-cli and the bundled python
                        with pcbnew that the board pipeline runs under
    Java 21             winget EclipseAdoptium.Temurin.21.JRE; Freerouting 2.x needs Java 21
    Freerouting 2.1.0   <ToolsRoot>\freerouting\freerouting-2.1.0.jar from the GitHub release. Upstream publishes no
                        checksum: the pin below is the SHA-256 of the release asset (66,978,394 bytes) computed on
                        2026-11-03; a different file is deleted and the run fails
    OSS CAD Suite       <ToolsRoot>\oss-cad-suite-20261024\ (yosys, nextpnr, iverilog, verilator, openFPGALoader) from
                        the 2026-10-24 Windows x64 release: 604,518,009 bytes, SHA-256 checked before unpacking
    CadQuery venv       <ToolsRoot>\cqv\ (python -m venv, then cadquery, trimesh, matplotlib, numpy, pillow, scipy, shapely) using a
                        pip cache under <ToolsRoot>\cache\pip
  At the end it writes <ToolsRoot>\hil_tools.env with the HIL_* variables the pipeline reads (HIL_FREEROUTING_JAR,
  HIL_JAVA, HIL_KICAD_CLI, HIL_CQ_PYTHON, HIL_OSSCAD) and prints how to load them.
  Exit: 0 everything present (or installed), 1 something missing or a checksum failed, 2 bad arguments.
=======================================================================================================================
#>
param(
  [Parameter(Mandatory)][string]$ToolsRoot,
  [switch]$VerifyOnly,
  [string[]]$Skip = @(),
  [switch]$WithEspIdf
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
$here = $PSScriptRoot                                                     # skills\hardware-in-the-loop\scripts

# ---------------------------------------------------------------------------------------------------------------------
#  Pins
# ---------------------------------------------------------------------------------------------------------------------
$Pin = @{
  FrUrl     = 'https://github.com/freerouting/freerouting/releases/download/v2.1.0/freerouting-2.1.0.jar'
  FrSize    = 66978394
  FrSha     = '2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def'
  OscUrl    = 'https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-10-24/oss-cad-suite-windows-x64-20261024.tgz'
  OscSize   = 604518009
  OscSha    = '6b38c1a57a17d8432b3d58f63beaa4e7919dcb6c19f9fcb6ca043fe8cb1b2cf7'
  CqPkgs    = @('cadquery', 'trimesh', 'matplotlib', 'numpy', 'pillow', 'scipy', 'shapely')
}

# ---------------------------------------------------------------------------------------------------------------------
#  Arguments
# ---------------------------------------------------------------------------------------------------------------------
$Skip = @($Skip | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
$known = @('kicad', 'java', 'freerouting', 'osscad', 'cadquery')
$bad = @($Skip | Where-Object { $known -notcontains $_ })
if ($bad.Count) { Write-Output "setup_tools: unknown -Skip item(s): $($bad -join ', ') (known: $($known -join ', '))"; exit 2 }
$ToolsRoot = [IO.Path]::GetFullPath($ToolsRoot)
$profileRoot = [Environment]::GetFolderPath('UserProfile')
if ($profileRoot -and $ToolsRoot.StartsWith($profileRoot, [StringComparison]::OrdinalIgnoreCase)) {
  Write-Output "setup_tools: refusing -ToolsRoot inside the user profile ($ToolsRoot). Use a folder like D:\hiltools."
  exit 2
}
if (-not $VerifyOnly) { New-Item -ItemType Directory -Force $ToolsRoot, (Join-Path $ToolsRoot 'cache\pip') | Out-Null }

$status = [ordered]@{}      # tool -> "ok <detail>" / "MISSING <why>"
$envOut = [ordered]@{}      # HIL_* variables for hil_tools.env

function Get-Sha256([string]$Path) { (Get-FileHash -Algorithm SHA256 -Path $Path).Hash.ToLowerInvariant() }

function Get-PinnedDownload {
  <#
  .SYNOPSIS  Downloads $Url to $Dest unless a file with the pinned size and SHA-256 is already there; verifies after.
  .OUTPUTS   $true when $Dest holds the pinned file, $false otherwise (a wrong file is deleted).
  #>
  param([string]$Url, [string]$Dest, [long]$Size, [string]$Sha)
  if (Test-Path $Dest) {
    if ((Get-Item $Dest).Length -eq $Size -and (Get-Sha256 $Dest) -eq $Sha) { return $true }
    if ($VerifyOnly) { return $false }
    Remove-Item $Dest -Force                                            # a partial or different file: fetch again
  }
  if ($VerifyOnly) { return $false }
  New-Item -ItemType Directory -Force (Split-Path -Parent $Dest) | Out-Null
  Write-Output "  downloading $Url"
  $tmp = "$Dest.part"
  Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $tmp
  $got = Get-Sha256 $tmp
  if ((Get-Item $tmp).Length -ne $Size -or $got -ne $Sha) {
    Remove-Item $tmp -Force
    Write-Output "  CHECKSUM MISMATCH for $Url (got $got, expected $Sha): file deleted"
    return $false
  }
  Move-Item $tmp $Dest -Force
  return $true
}

function Find-KiCadCli {
  # newest KiCad under Program Files first (10.0 above 9.0), then PATH
  $base = Join-Path $env:ProgramFiles 'KiCad'
  if (Test-Path $base) {
    $dirs = Get-ChildItem -Path $base -Directory | Sort-Object { try { [version]($_.Name + '.0') } catch { [version]'0.0' } } -Descending
    foreach ($d in $dirs) { $p = Join-Path $d.FullName 'bin\kicad-cli.exe'; if (Test-Path $p) { return $p } }
  }
  $c = Get-Command kicad-cli -ErrorAction SilentlyContinue
  if ($c) { return $c.Source } else { return $null }
}

function Find-Java21 {
  # a java whose -version says 21 or newer (Freerouting 2.x needs 21)
  $cands = @()
  if ($env:HIL_JAVA) { $cands += $env:HIL_JAVA }
  if ($env:JAVA_HOME) { $cands += (Join-Path $env:JAVA_HOME 'bin\java.exe') }
  foreach ($root in @((Join-Path $env:ProgramFiles 'Eclipse Adoptium'), (Join-Path $env:ProgramFiles 'Java'))) {
    if (Test-Path $root) { $cands += @(Get-ChildItem -Path $root -Directory | Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName 'bin\java.exe' }) }
  }
  $c = Get-Command java -ErrorAction SilentlyContinue; if ($c) { $cands += $c.Source }
  foreach ($j in $cands) {
    if (-not (Test-Path $j)) { continue }
    $v = (& $j -version 2>&1 | Select-Object -First 1) -join ''
    $m = [regex]::Match("$v", 'version "(\d+)')
    if ($m.Success -and [int]$m.Groups[1].Value -ge 21) { return @($j, "$v") }
  }
  return $null
}

function Install-Winget([string]$Id) {
  # machine-wide install through winget; returns $true on success (winget prints its own progress)
  $w = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $w) { Write-Output "  winget not found: install $Id by hand"; return $false }
  & $w.Source install --id $Id --exact --silent --accept-package-agreements --accept-source-agreements --scope machine
  return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------------------------------------------------------------
#  KiCad 10
# ---------------------------------------------------------------------------------------------------------------------
if ($Skip -notcontains 'kicad') {
  $cli = Find-KiCadCli
  if (-not $cli -and -not $VerifyOnly) { Write-Output 'KiCad: installing (winget KiCad.KiCad)'; [void](Install-Winget 'KiCad.KiCad'); $cli = Find-KiCadCli }
  if ($cli) {
    $ver = (& $cli version 2>$null | Select-Object -First 1)
    $py = Join-Path (Split-Path -Parent $cli) 'python.exe'
    $status.kicad = "ok kicad-cli $ver ($cli); bundled python: $(if (Test-Path $py) { $py } else { 'MISSING' })"
    $envOut.HIL_KICAD_CLI = $cli
    if (-not "$ver".StartsWith('10')) { $status.kicad += '  NOTE: the pipeline is written for KiCad 10' }
  } else { $status.kicad = 'MISSING kicad-cli (winget install KiCad.KiCad)' }
}

# ---------------------------------------------------------------------------------------------------------------------
#  Java 21
# ---------------------------------------------------------------------------------------------------------------------
if ($Skip -notcontains 'java') {
  $j = Find-Java21
  if (-not $j -and -not $VerifyOnly) { Write-Output 'Java 21: installing (winget EclipseAdoptium.Temurin.21.JRE)'; [void](Install-Winget 'EclipseAdoptium.Temurin.21.JRE'); $j = Find-Java21 }
  if ($j) { $status.java = "ok $($j[1]) ($($j[0]))"; $envOut.HIL_JAVA = $j[0] }
  else { $status.java = 'MISSING Java >= 21 (winget install EclipseAdoptium.Temurin.21.JRE)' }
}

# ---------------------------------------------------------------------------------------------------------------------
#  Freerouting 2.1.0
# ---------------------------------------------------------------------------------------------------------------------
if ($Skip -notcontains 'freerouting') {
  $jar = Join-Path $ToolsRoot 'freerouting\freerouting-2.1.0.jar'
  if (Get-PinnedDownload -Url $Pin.FrUrl -Dest $jar -Size $Pin.FrSize -Sha $Pin.FrSha) {
    $status.freerouting = "ok 2.1.0 sha256 $($Pin.FrSha.Substring(0, 12)) ($jar)"; $envOut.HIL_FREEROUTING_JAR = $jar
  } else { $status.freerouting = "MISSING $jar (pinned sha256 $($Pin.FrSha))" }
}

# ---------------------------------------------------------------------------------------------------------------------
#  OSS CAD Suite 2026-10-24 (FPGA flow: yosys, nextpnr, iverilog, verilator, openFPGALoader)
# ---------------------------------------------------------------------------------------------------------------------
if ($Skip -notcontains 'osscad') {
  $dest = Join-Path $ToolsRoot 'oss-cad-suite-20261024'
  $marker = Join-Path $dest 'oss-cad-suite\bin\yosys.exe'
  if (Test-Path $marker) { $status.osscad = "ok 2026-10-24 ($dest)"; $envOut.HIL_OSSCAD = (Join-Path $dest 'oss-cad-suite') }
  else {
    $tgz = Join-Path $ToolsRoot 'downloads\oss-cad-suite-windows-x64-20261024.tgz'
    if (Get-PinnedDownload -Url $Pin.OscUrl -Dest $tgz -Size $Pin.OscSize -Sha $Pin.OscSha) {
      if (-not $VerifyOnly) {
        New-Item -ItemType Directory -Force $dest | Out-Null
        & tar.exe -xzf $tgz -C $dest                                        # Windows tar (bsdtar) reads .tgz
      }
      if (Test-Path $marker) { $status.osscad = "ok 2026-10-24 sha256 $($Pin.OscSha.Substring(0, 12)) ($dest)"; $envOut.HIL_OSSCAD = (Join-Path $dest 'oss-cad-suite') }
      else { $status.osscad = "MISSING unpack failed or not run ($dest)" }
    } else { $status.osscad = "MISSING $tgz (pinned sha256 $($Pin.OscSha))" }
  }
}

# ---------------------------------------------------------------------------------------------------------------------
#  CadQuery virtual environment
# ---------------------------------------------------------------------------------------------------------------------
if ($Skip -notcontains 'cadquery') {
  $venv = Join-Path $ToolsRoot 'cqv'
  $vpy = Join-Path $venv 'Scripts\python.exe'
  if (-not (Test-Path $vpy) -and -not $VerifyOnly) {
    $base = Get-Command python -ErrorAction SilentlyContinue
    if ($base) {
      Write-Output "CadQuery: creating $venv"
      & $base.Source -m venv $venv
      $env:PIP_CACHE_DIR = Join-Path $ToolsRoot 'cache\pip'                # pip cache off the user profile
      & $vpy -m pip install --upgrade pip
      & $vpy -m pip install @($Pin.CqPkgs)
    }
  }
  if (Test-Path $vpy) {
    $cqv = (& $vpy -I -c "import cadquery, trimesh; print(cadquery.__version__, trimesh.__version__)" 2>$null)
    if ($LASTEXITCODE -eq 0) { $status.cadquery = "ok cadquery/trimesh $cqv ($vpy)"; $envOut.HIL_CQ_PYTHON = $vpy }
    else { $status.cadquery = "MISSING packages in $vpy (pip install $($Pin.CqPkgs -join ' '))" }
  } else { $status.cadquery = "MISSING venv $venv" }
}

# ---------------------------------------------------------------------------------------------------------------------
#  ESP-IDF (pointer only)
# ---------------------------------------------------------------------------------------------------------------------
if ($WithEspIdf) {
  Write-Output ''
  Write-Output 'ESP-IDF: install it with the Espressif installer or `git clone --recursive` of the release you pin, into'
  Write-Output "  $(Join-Path $ToolsRoot 'esp-idf') with IDF_TOOLS_PATH=$(Join-Path $ToolsRoot 'espressif') (keeps the tools off the user profile),"
  Write-Output '  then build out of tree: idf.py -B <scratch build dir> -D SDKCONFIG=<scratch>\sdkconfig build'
}

# ---------------------------------------------------------------------------------------------------------------------
#  Report + environment file
# ---------------------------------------------------------------------------------------------------------------------
Write-Output ''
foreach ($k in $status.Keys) { Write-Output ('{0,-12} {1}' -f $k, $status[$k]) }
if (-not $VerifyOnly -and $envOut.Count) {
  $envFile = Join-Path $ToolsRoot 'hil_tools.env'
  $lines = @("# written by setup_tools.ps1 $(Get-Date -Format s); load in pwsh with:")
  $lines += "#   Get-Content '$envFile' | Where-Object { `$_ -match '^HIL_' } | ForEach-Object { `$k, `$v = `$_ -split '=', 2; Set-Item env:`$k `$v }"
  foreach ($k in $envOut.Keys) { $lines += "$k=$($envOut[$k])" }
  [IO.File]::WriteAllLines($envFile, $lines, [Text.UTF8Encoding]::new($false))
  Write-Output "environment  $envFile"
}
$missing = @($status.Values | Where-Object { "$_".StartsWith('MISSING') }).Count
exit $(if ($missing -eq 0) { 0 } else { 1 })
