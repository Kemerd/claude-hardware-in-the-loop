<#
=======================================================================================================================
  link_plugin.ps1 - makes the HilTwin plugin visible to a host project: <host>\Plugins\HilTwin -> sim\unreal\HilTwin.

  Interpreter: PowerShell 7 (pwsh). Run: pwsh -File sim\unreal\link_plugin.ps1 [-HostDir <folder holding a .uproject>]

  A directory junction (no admin rights needed on NTFS), so the plugin source stays in one place under sim\unreal and
  every host project builds the same files. On Linux: ln -s <repo>/sim/unreal/HilTwin <host>/Plugins/HilTwin
  An existing Plugins\HilTwin is left alone (it is reported, never replaced).
=======================================================================================================================
#>
param([string]$HostDir = '')
$ErrorActionPreference = 'Stop'
if (-not $HostDir) { $HostDir = Join-Path $PSScriptRoot 'host' }
$plugin = Join-Path $PSScriptRoot 'HilTwin'
if (-not (Test-Path (Join-Path $plugin 'HilTwin.uplugin'))) { Write-Output "link_plugin: no HilTwin.uplugin under $plugin"; exit 2 }
if (-not (Get-ChildItem -Path $HostDir -Filter *.uproject -ErrorAction SilentlyContinue)) {
  Write-Output "link_plugin: no .uproject in $HostDir"; exit 2
}
$dst = Join-Path $HostDir 'Plugins\HilTwin'
if (Test-Path $dst) { Write-Output "link_plugin: $dst already exists, left as it is"; exit 0 }
New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
New-Item -ItemType Junction -Path $dst -Target $plugin | Out-Null
Write-Output "link_plugin: $dst -> $plugin"
