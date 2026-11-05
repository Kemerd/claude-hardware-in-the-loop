<#
=======================================================================================================================
  cad_viewer.ps1 - the CAD viewer as a LOCAL tool: pack a build, serve it, open the browser.

  Interpreter: PowerShell 7 (pwsh) or Windows PowerShell 5.1. Paths: resolved from $PSScriptRoot (the repo root is its parent).

    pwsh -File windows\cad_viewer.ps1 start [-Pack <dir>] [-Port 8765] [-NoBrowser]
        Serve the newest pack (or the one given with -Pack) at http://127.0.0.1:<port>/ and open it in the default browser.
        The server is tools\cad_viewer\serve.py: the page plus a notes API, so a note pinned on a part lands in
        docs\cad_feedback
otes.jsonl (tracked, append-only) where the CAD work reads it. Idempotent: if it already runs, it
        only opens the browser.
    pwsh -File windows\cad_viewer.ps1 stop        stop the server this script started (that PID only, after checking its command line)
    pwsh -File windows\cad_viewer.ps1 status      running or not, URL, which pack and build label is served, pid
    pwsh -File windows\cad_viewer.ps1 pack <build dir> "<label>" [more pack_mesh.py flags, e.g. --boards <render dir> --screens <json> --about "..."]
        Pack a build (parts.json + STLs, see tools\cad_viewer\README.md) with tools\cad_viewer\pack_mesh.py into
        .tmp\cad_viewer\packs\<slug of label>\ and make it the one `start` serves.

  Packs live in .tmp\cad_viewer\packs\ (gitignored); .tmp\cad_viewer\latest.txt names the pack `start` serves. Old packs
  go to the archive (toolsrchive_tmp.py). The state folder is the MAIN checkout's .tmp even when this script runs from a
  git worktree, so every worktree sees one list of packs and one server; $env:HIL_VIEWER_HOME overrides it.
  Python: $env:HIL_PY, else $env:HIL_CQ_PYTHON (the CadQuery venv from setup_tools), else `python` on PATH.
  serve.py needs only the standard library; pack_mesh.py needs trimesh + numpy (the CadQuery venv has them), and Pillow +
  scipy only for --boards.
=======================================================================================================================
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)][ValidateSet('start', 'stop', 'status', 'pack', 'help')][string]$Command = 'start',
  [Parameter(Position = 1, ValueFromRemainingArguments)][string[]]$Rest = @(),
  [string]$Pack = '',
  [int]$Port = 8765,
  [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8   # part names and labels may carry any Unicode
$env:PYTHONUTF8 = '1'

# ---------------------------------------------------------------------------------------------------------------------
#  Paths. The repo root is the parent of windows\; the state dir is gitignored (.tmp is in .gitignore).
# ---------------------------------------------------------------------------------------------------------------------
$Repo = Split-Path -Parent $PSScriptRoot
$Serve = Join-Path $Repo 'tools\cad_viewer\serve.py'
$Packer = Join-Path $Repo 'tools\cad_viewer\pack_mesh.py'

# ---- the state (packs, pid, logs) lives in the MAIN checkout's .tmp even when this script runs from a git worktree , so the
#      main checkout and every agent worktree see one list of packs and one server.
#      $env:HIL_VIEWER_HOME overrides it.
function Get-MainRoot {
  $git = Join-Path $Repo '.git'
  if (Test-Path $git -PathType Leaf) {                       # a worktree: .git is a file "gitdir: <main>/.git/worktrees/<name>"
    $line = (Get-Content $git -TotalCount 1)
    if ($line -match '^gitdir:\s*(.+)$') {
      $gd = $Matches[1].Trim()
      $i = $gd.Replace('\', '/').IndexOf('/.git/worktrees/')
      if ($i -gt 0) { return $gd.Substring(0, $i).Replace('/', '\') }
    }
  }
  return $Repo
}
$State = if ($env:HIL_VIEWER_HOME) { $env:HIL_VIEWER_HOME } else { Join-Path (Get-MainRoot) '.tmp\cad_viewer' }
$PacksDir = Join-Path $State 'packs'
$LatestFile = Join-Path $State 'latest.txt'
$PidFile = Join-Path $State 'serve.pid'
$OutLog = Join-Path $State 'serve.log'
$ErrLog = Join-Path $State 'serve.err.log'
$Url = "http://127.0.0.1:$Port/"
New-Item -ItemType Directory -Force -Path $PacksDir | Out-Null

if (-not (Test-Path $Serve)) { throw "cad_viewer.ps1: $Serve is missing" }
if ($Port -lt 1 -or $Port -gt 65535) { throw "cad_viewer.ps1: bad port $Port" }

# ---- which python: HIL_PY, then HIL_CQ_PYTHON (the CadQuery venv written by setup_tools), else PATH
function Get-Python {
  $cands = @()
  if ($env:HIL_PY) { $cands += $env:HIL_PY }
  if ($env:HIL_CQ_PYTHON) { $cands += $env:HIL_CQ_PYTHON }
  foreach ($c in $cands) { if (Test-Path $c) { return $c } }
  $onPath = Get-Command python -ErrorAction SilentlyContinue
  if ($onPath) { return $onPath.Source }
  throw 'cad_viewer.ps1: no python found (set $env:HIL_PY)'
}

# ---- the server process this script started, verified by its command line (never trust a pid file alone)
function Get-ServerProc {
  if (-not (Test-Path $PidFile)) { return $null }
  $raw = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $raw -or -not ($raw -match '^\d+$')) { return $null }
  $p = Get-CimInstance Win32_Process -Filter "ProcessId = $raw" -ErrorAction SilentlyContinue
  if (-not $p) { return $null }
  if ($p.CommandLine -notmatch 'cad_viewer[/\\]serve\.py') { return $null }   # pid reused by something else
  return $p
}

# ---- does anything answer the notes API on the port right now?
function Test-Viewer {
  try { $r = Invoke-WebRequest -UseBasicParsing -Uri ($Url + 'api/notes') -TimeoutSec 2; return ($r.StatusCode -eq 200) }
  catch { return $false }
}

# ---- the pack dir to serve: -Pack, else latest.txt
function Get-PackDir {
  if ($Pack) {
    $d = (Resolve-Path $Pack -ErrorAction Stop).Path
  } elseif (Test-Path $LatestFile) {
    $d = (Get-Content $LatestFile | Select-Object -First 1).Trim()
  } else {
    throw "cad_viewer.ps1: no pack yet. Run: .\windows\cad_viewer.ps1 pack <build dir> `"<label>`"  (or pass -Pack <dir>)"
  }
  if (-not (Test-Path (Join-Path $d 'mesh.json'))) { throw "cad_viewer.ps1: $d has no mesh.json (pack_mesh.py output expected)" }
  return $d
}

# ---- build label + blurb from the pack's manifest header (mesh.json carries build / about / chunks / bytes)
function Get-PackInfo([string]$dir) {
  try {
    $m = Get-Content (Join-Path $dir 'mesh.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $about = if ($m.about) { [string]$m.about } else { '' }
    if ($about.Length -gt 140) { $about = $about.Substring(0, 140) + '...' }
    return [pscustomobject]@{ build = [string]$m.build; about = $about; chunks = $m.chunks; bytes = $m.bytes }
  } catch { return [pscustomobject]@{ build = '?'; about = ''; chunks = '?'; bytes = '?' } }
}

function Open-Browser { if (-not $NoBrowser) { Start-Process $Url | Out-Null } }

switch ($Command) {
  'help' { Get-Help $PSCommandPath -Detailed | Out-String | Write-Host; break }

  'status' {
    $p = Get-ServerProc
    $dir = if (Test-Path $LatestFile) { (Get-Content $LatestFile | Select-Object -First 1).Trim() } else { '(none packed yet)' }
    $info = if (Test-Path (Join-Path $dir 'mesh.json')) { Get-PackInfo $dir } else { $null }
    if ($p) {
      $served = if ($p.CommandLine -match '--mesh\s+"?([^"]+?)"?\s+--port') { $Matches[1] } else { '?' }
      Write-Host "[viewer] RUNNING pid $($p.ProcessId) since $($p.CreationDate.ToString('HH:mm'))  $Url"
      Write-Host "[viewer] serving $served"
    } else {
      Write-Host "[viewer] not running (answers on port: $(Test-Viewer))"
    }
    Write-Host "[viewer] newest pack $dir"
    if ($info) { Write-Host "[viewer]   build: $($info.build)  ($($info.chunks) chunks, $($info.bytes) bytes)"; if ($info.about) { Write-Host "[viewer]   about: $($info.about)" } }
    break
  }

  'start' {
    $p = Get-ServerProc
    if ($p) { Write-Host "[viewer] already running, pid $($p.ProcessId): $Url"; Open-Browser; break }
    if (Test-Viewer) {
      # Something not started by this script answers on the port (an older hand-started serve.py): use it, never touch it.
      Write-Host "[viewer] a viewer already answers on $Url (not started by this script; use another -Port to run a second one)"
      Open-Browser; break
    }
    $dir = Get-PackDir
    $py = Get-Python
    $info = Get-PackInfo $dir
    # Detached, hidden, BelowNormal: the server outlives this shell and never competes with builds for the CPU.
    $argv = @('-X', 'utf8', $Serve, '--mesh', $dir, '--port', "$Port", '--host', '127.0.0.1')
    $proc = Start-Process -FilePath $py -ArgumentList $argv -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
              -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
    try { $proc.PriorityClass = 'BelowNormal' } catch { }
    Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii
    $ready = $false
    for ($i = 0; $i -lt 40 -and -not $ready; $i++) {   # up to 10 s: python start + the mesh pieces being opened
      if ($proc.HasExited) { break }
      Start-Sleep -Milliseconds 250
      $ready = Test-Viewer
    }
    if (-not $ready) {
      Write-Host "[viewer] FAILED to start (pid $($proc.Id), exited=$($proc.HasExited)). Last log lines:"
      foreach ($f in @($ErrLog, $OutLog)) { if (Test-Path $f) { Get-Content $f -Tail 8 | ForEach-Object { Write-Host "    $_" } } }
      exit 1
    }
    Write-Host "[viewer] $Url  pid $($proc.Id)  (stop: .\windows\cad_viewer.ps1 stop)"
    Write-Host "[viewer] pack $dir"
    Write-Host "[viewer] build: $($info.build)"
    Write-Host "[viewer] notes -> docs\cad_feedback\notes.jsonl (tap a part, Add note)"
    Open-Browser
    break
  }

  'stop' {
    $p = Get-ServerProc
    if (-not $p) { Write-Host '[viewer] not running (nothing started by this script)'; if (Test-Path $PidFile) { Remove-Item $PidFile -Force }; break }
    Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "[viewer] stopped pid $($p.ProcessId)"
    break
  }

  'pack' {
    if ($Rest.Count -lt 2) { throw 'cad_viewer.ps1 pack <build dir> "<label>" [pack_mesh.py flags]' }
    $build = (Resolve-Path $Rest[0] -ErrorAction Stop).Path
    $label = $Rest[1]
    $flags = @(); if ($Rest.Count -gt 2) { $flags = $Rest[2..($Rest.Count - 1)] }
    if (-not $label.Trim()) { throw 'cad_viewer.ps1: empty label' }
    $slug = ($label.ToLowerInvariant() -replace '[^a-z0-9]+', '_').Trim('_')
    if (-not $slug) { throw "cad_viewer.ps1: label '$label' gives an empty folder name" }
    $out = Join-Path $PacksDir $slug
    $py = Get-Python
    Write-Host "[viewer] packing $build -> $out  (label '$label')"
    & $py -X utf8 $Packer $build $out '--label' $label @flags
    if ($LASTEXITCODE -ne 0) { throw "cad_viewer.ps1: pack_mesh.py failed rc=$LASTEXITCODE" }
    if (-not (Test-Path (Join-Path $out 'mesh.json'))) { throw "cad_viewer.ps1: pack_mesh.py left no mesh.json in $out" }
    Set-Content -Path $LatestFile -Value $out -Encoding UTF8
    $info = Get-PackInfo $out
    $mb = [math]::Round(((Get-ChildItem $out -File | Measure-Object Length -Sum).Sum / 1MB), 1)
    Write-Host "[viewer] packed: $($info.build)  $mb MB on disk; now the newest (start serves it)"
    $p = Get-ServerProc
    if ($p) { Write-Host "[viewer] note: the running server (pid $($p.ProcessId)) still serves the old pack; stop + start to switch" }
    break
  }
}
