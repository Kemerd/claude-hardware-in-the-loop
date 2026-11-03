<#
=======================================================================================================================
  ci_common.psm1 - shared helpers for windows\ci.ps1 and the per-group runners in windows\ci\*.ps1

  Interpreter: PowerShell 7 (pwsh). Imported by every runner with:
      Import-Module (Join-Path $PSScriptRoot 'ci_common.psm1') -Force

  Every group runner is a separate pwsh process, so groups run in parallel without sharing a working directory or
  environment. A runner:
    1. calls Initialize-CiEnvironment <Out>   (TEMP, caches and tool homes pointed at the CI output folder, never the
                                                user profile; UTF-8 console; GPU hidden from every child)
    2. builds one result record per suite      (New-CiResult)
    3. runs native tools through Invoke-CiCommand (every line streams into a per-suite log as it is printed)
    4. records the outcome with Complete-CiResult (a run is "pass" only with exit 0 AND a summary line AND 0 failures)
    5. writes its records with Write-CiResults to <Out>\results\<group>.json, which ci.ps1 aggregates.

  Status vocabulary (ci_report.json "status"):
    pass         built, ran, and its own summary line reported zero failures
    fail         ran but reported failures, or its summary line was missing / disagreed with the exit code
    build_fail   a configure / compile / link step failed; nothing ran
    error        a required tool or input is missing on this machine (the path is in "summary")
    unavailable  the thing under test does not exist in this tree yet; counted red only under -Strict
    skipped      not selected by -Only / -Skip
=======================================================================================================================
#>

Set-StrictMode -Version 3.0

# ---------------------------------------------------------------------------------------------------------------------
#  Process runner (C#). PowerShell event handlers only run when the engine is idle, so a pipeline blocked in
#  WaitForExit would not stream its log; real .NET threads do. Compiled once per runner process (about a second).
# ---------------------------------------------------------------------------------------------------------------------
if (-not ('HilCi.Runner' -as [type])) {
  Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
namespace HilCi {
  public sealed class RunResult { public int ExitCode; public string[] Lines; }
  public static class Runner {
    // Runs exe with args in cwd; appends every stdout / stderr line to log as it arrives; stops the child (and its
    // tree) after timeoutMs when timeoutMs > 0. Returns exit code (-2 on timeout) and all lines in arrival order.
    public static RunResult Run(string exe, string[] args, string cwd, string log, int timeoutMs) {
      var psi = new ProcessStartInfo(exe) {
        WorkingDirectory = cwd, UseShellExecute = false, RedirectStandardInput = true,
        RedirectStandardOutput = true, RedirectStandardError = true,
        StandardOutputEncoding = new UTF8Encoding(false), StandardErrorEncoding = new UTF8Encoding(false) };
      foreach (var a in args ?? new string[0]) psi.ArgumentList.Add(a);
      var lines = new List<string>();
      var gate = new object();
      using (var w = new StreamWriter(log, true, new UTF8Encoding(false)) { AutoFlush = true })
      using (var p = new Process { StartInfo = psi }) {
        DataReceivedEventHandler h = (s, e) => { if (e.Data != null) lock (gate) { lines.Add(e.Data); w.WriteLine(e.Data); } };
        p.OutputDataReceived += h; p.ErrorDataReceived += h;
        p.Start();
        // stdin is closed at once: a child that inherits a background job's protocol pipe can block at start-up
        // (seen with python inside Start-Job), and no CI child may ever wait for keyboard input anyway
        try { p.StandardInput.Close(); } catch { }
        try { p.PriorityClass = ProcessPriorityClass.BelowNormal; } catch { }
        p.BeginOutputReadLine(); p.BeginErrorReadLine();
        int rc;
        if (!p.WaitForExit(timeoutMs > 0 ? timeoutMs : -1)) {
          try { p.Kill(true); } catch { }
          p.WaitForExit();
          lock (gate) { lines.Add("CI: timed out after " + (timeoutMs / 60000) + " min, process " + p.Id + " stopped"); }
          rc = -2;
        } else { p.WaitForExit(); rc = p.ExitCode; }
        lock (gate) { return new RunResult { ExitCode = rc, Lines = lines.ToArray() }; }
      }
    }
  }
}
'@
}

# ---------------------------------------------------------------------------------------------------------------------
#  Tool locations. Each entry is resolved once; an environment variable wins over the default, and every runner checks
#  existence before use (a missing tool is an "error" result with the path, never a silent skip).
# ---------------------------------------------------------------------------------------------------------------------
function Get-CiTool {
  <#
  .SYNOPSIS  Full path of a named tool (python, kicadcli, kicadpy, java, cqpy), or $null when it cannot be found.
  .PARAMETER Name  One of: python, kicadcli, kicadpy, java, cqpy.
  #>
  param([Parameter(Mandatory)][string]$Name)
  switch ($Name) {
    'python' {
      if ($env:HIL_PYTHON -and (Test-Path $env:HIL_PYTHON)) { return $env:HIL_PYTHON }
      $c = Get-Command python -ErrorAction SilentlyContinue
      if ($c) { return $c.Source }
      return $null
    }
    'kicadcli' {
      if ($env:HIL_KICAD_CLI -and (Test-Path $env:HIL_KICAD_CLI)) { return $env:HIL_KICAD_CLI }
      # newest KiCad install first (10.0 sorts above 9.0 numerically, not as text)
      $roots = @(Get-ChildItem -Path (Join-Path $env:ProgramFiles 'KiCad') -Directory -ErrorAction SilentlyContinue |
                 Sort-Object { [version]($_.Name + '.0') } -Descending -ErrorAction SilentlyContinue)
      foreach ($r in $roots) { $p = Join-Path $r.FullName 'bin\kicad-cli.exe'; if (Test-Path $p) { return $p } }
      $c = Get-Command kicad-cli -ErrorAction SilentlyContinue
      if ($c) { return $c.Source }
      return $null
    }
    'kicadpy' {
      $cli = Get-CiTool 'kicadcli'
      if (-not $cli) { return $null }
      $p = Join-Path (Split-Path -Parent $cli) 'python.exe'
      if (Test-Path $p) { return $p } else { return $null }
    }
    'java' {
      if ($env:HIL_JAVA21 -and (Test-Path $env:HIL_JAVA21)) { return $env:HIL_JAVA21 }
      $c = Get-Command java -ErrorAction SilentlyContinue
      if ($c) { return $c.Source }
      return $null
    }
    'cqpy' {
      if ($env:HIL_CQ_PYTHON -and (Test-Path $env:HIL_CQ_PYTHON)) { return $env:HIL_CQ_PYTHON }
      return $null
    }
    default { return $null }
  }
}

function Initialize-CiEnvironment {
  <#
  .SYNOPSIS  Points every temp / cache / tool-home location of the child processes at the CI output folder.
  .DESCRIPTION
    TEMP / TMP / TMPDIR      -> <Out>\tmp
    pip, npm, matplotlib, HF, torch, XDG caches -> <Out>\cache\*   (a CI run never fills the user profile)
    KiCad per-user folders   -> <Out>\kicad\{config,documents,cache}
    Python                   -> UTF-8 stdio, no bytecode written into the repo
    GPU                      -> hidden (CUDA_VISIBLE_DEVICES = '', HIL_GPU = 0): CI never touches the GPU
  .PARAMETER Out  The run's output folder (created when missing).
  #>
  param([Parameter(Mandatory)][string]$Out)
  if (-not $Out) { throw 'Initialize-CiEnvironment: -Out is required' }
  $tmp = Join-Path $Out 'tmp'
  New-Item -ItemType Directory -Force $tmp | Out-Null
  $env:TEMP = $tmp; $env:TMP = $tmp; $env:TMPDIR = $tmp

  # ---- caches: one folder per tool under the run output
  $cache = Join-Path $Out 'cache'
  foreach ($sub in 'pip', 'npm', 'matplotlib', 'hf', 'torch', 'xdg', 'pycache') {
    New-Item -ItemType Directory -Force (Join-Path $cache $sub) | Out-Null
  }
  $env:PIP_CACHE_DIR       = Join-Path $cache 'pip'
  $env:npm_config_cache    = Join-Path $cache 'npm'
  $env:MPLCONFIGDIR        = Join-Path $cache 'matplotlib'
  $env:HF_HOME             = Join-Path $cache 'hf'
  $env:TORCH_HOME          = Join-Path $cache 'torch'
  $env:XDG_CACHE_HOME      = Join-Path $cache 'xdg'
  $env:PYTHONPYCACHEPREFIX = Join-Path $cache 'pycache'

  # ---- KiCad: kicad-cli writes its user settings on every start; keep them in the run folder
  $kc = Join-Path $Out 'kicad'
  foreach ($sub in 'config', 'documents', 'cache') { New-Item -ItemType Directory -Force (Join-Path $kc $sub) | Out-Null }
  $env:KICAD_CONFIG_HOME    = Join-Path $kc 'config'
  $env:KICAD_DOCUMENTS_HOME = Join-Path $kc 'documents'
  $env:KICAD_CACHE_HOME     = Join-Path $kc 'cache'

  # ---- Python console + bytecode
  $env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONDONTWRITEBYTECODE = '1'

  # ---- CPU only: the GPU belongs to interactive work and the one gated GPU job, never to CI
  $env:CUDA_VISIBLE_DEVICES = ''; $env:HIL_GPU = '0'

  # ---- this process decodes child output as UTF-8
  try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false) } catch { }
  $global:OutputEncoding = [System.Text.UTF8Encoding]::new($false)
}

function New-CiResult {
  <#
  .SYNOPSIS  One suite result record (ordered, so the JSON keeps a readable field order). Starts as 'error' so a runner
             that dies half-way can never leave a green record behind.
  #>
  param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string]$Group, [string]$Log = '')
  return [ordered]@{
    name = $Name; group = $Group; status = 'error'; exit = $null; duration_s = 0.0
    passed = $null; failed = $null; total = $null; summary = ''; command = ''; log = $Log; extra = [ordered]@{}
  }
}

function Invoke-CiCommand {
  <#
  .SYNOPSIS  Runs a native command, streams every output line (stdout + stderr) into a UTF-8 log, returns the exit code
             and the lines.
  .DESCRIPTION
    Lines are written the moment the child prints them, so a long suite never looks hung to a log-growth watcher.
    With -TimeoutMin the child is stopped (by its own PID only) when it runs longer; the result then has exit -2.
  .PARAMETER Exe        Program path or PATH name.
  .PARAMETER Arguments  Argument array, passed verbatim (no shell parsing).
  .PARAMETER Cwd        Working directory for the child (restored afterwards).
  .PARAMETER Log        Log file (appended, so one suite can log several steps).
  .PARAMETER TimeoutMin Minutes before the child is stopped; 0 = no limit.
  .OUTPUTS   [pscustomobject] @{ exit; duration_s; lines; command }
  #>
  param([Parameter(Mandatory)][string]$Exe, [string[]]$Arguments = @(), [string]$Cwd = '',
        [Parameter(Mandatory)][string]$Log, [int]$TimeoutMin = 0)
  $cmdLine = ('"{0}" {1}' -f $Exe, (($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '))
  $logDir = Split-Path -Parent $Log
  if ($logDir) { New-Item -ItemType Directory -Force $logDir | Out-Null }
  ("`n### {0}`n### cwd: {1}`n### start: {2}" -f $cmdLine, $Cwd, (Get-Date -Format o)) | Out-File -Append -Encoding utf8 $Log
  $sw = [Diagnostics.Stopwatch]::StartNew()
  $lines = @(); $rc = $null
  try {
    # ---- the C# runner below owns the child: real reader threads (no PowerShell event queue), BelowNormal priority,
    #      timeout by the child's own PID, every line appended to the log as it arrives
    $res = [HilCi.Runner]::Run($Exe, [string[]]$Arguments, $(if ($Cwd) { $Cwd } else { (Get-Location).Path }), $Log, $TimeoutMin * 60000)
    $rc = $res.ExitCode
    $lines = @($res.Lines)
  } catch {
    $lines = @("CI: failed to start: $($_.Exception.Message)")
    $rc = -1
  }
  $sw.Stop()
  ("### exit: {0}  elapsed_s: {1:N1}" -f $rc, $sw.Elapsed.TotalSeconds) | Out-File -Append -Encoding utf8 $Log
  return [pscustomobject]@{ exit = $rc; duration_s = [math]::Round($sw.Elapsed.TotalSeconds, 1); lines = $lines; command = $cmdLine }
}

function Find-CiMatch {
  <#
  .SYNOPSIS  The LAST line of $Lines matching regex $Pattern, as a Match object, or $null.
  #>
  param([string[]]$Lines, [Parameter(Mandatory)][string]$Pattern)
  if (-not $Lines) { return $null }
  $hit = $null
  foreach ($l in $Lines) { $m = [regex]::Match("$l", $Pattern); if ($m.Success) { $hit = $m } }
  return $hit
}

function Complete-CiResult {
  <#
  .SYNOPSIS  Records a run into a result, applying the "the summary line must agree with the exit code" rule.
  .DESCRIPTION
    A run is "pass" only when the exit code is 0 AND a summary line was found AND it reports no failures. Exit 0 without
    a recognisable summary is "fail": a crashed or truncated run must never look green.
  #>
  param([Parameter(Mandatory)]$Result, [Parameter(Mandatory)]$Run, [Nullable[int]]$Passed = $null,
        [Nullable[int]]$Total = $null, [Nullable[int]]$Failed = $null, [bool]$SummaryFound = $false, [string]$Summary = '')
  $Result.exit = $Run.exit
  $Result.duration_s = [math]::Round([double]$Result.duration_s + [double]$Run.duration_s, 1)
  if (-not $Result.command) { $Result.command = $Run.command } else { $Result.command += ' ; ' + $Run.command }
  $Result.passed = $Passed; $Result.total = $Total
  if ($null -eq $Failed -and $null -ne $Passed -and $null -ne $Total) { $Failed = $Total - $Passed }
  $Result.failed = $Failed
  $Result.summary = $Summary
  if ($Run.exit -ne 0) { $Result.status = 'fail' }
  elseif (-not $SummaryFound) { $Result.status = 'fail'; $Result.summary = "exit 0 but no summary line found. $Summary" }
  elseif ($null -ne $Failed -and $Failed -gt 0) { $Result.status = 'fail' }
  else { $Result.status = 'pass' }
}

function Test-CiSelected {
  <#
  .SYNOPSIS  True when suite $Name of group $Group is selected by -Only / -Skip (empty -Only = everything).
  #>
  param([string]$Name, [string]$Group, [string[]]$Only = @(), [string[]]$Skip = @())
  $Only = @($Only | ForEach-Object { "$_" -split ',' } | Where-Object { $_ })
  $Skip = @($Skip | ForEach-Object { "$_" -split ',' } | Where-Object { $_ })
  if ($Skip -and (($Skip -contains $Name) -or ($Skip -contains $Group))) { return $false }
  if (-not $Only -or $Only.Count -eq 0) { return $true }
  return (($Only -contains $Name) -or ($Only -contains $Group))
}

function Write-CiResults {
  <#
  .SYNOPSIS  Writes a group's result records to <Out>\results\<Group>.json (UTF-8, no BOM).
  #>
  param([Parameter(Mandatory)][string]$Out, [Parameter(Mandatory)][string]$Group, $Results)
  $dir = Join-Path $Out 'results'
  New-Item -ItemType Directory -Force $dir | Out-Null
  $json = ConvertTo-Json -InputObject @($Results) -Depth 8
  [System.IO.File]::WriteAllText((Join-Path $dir "$Group.json"), $json, [System.Text.UTF8Encoding]::new($false))
}

Export-ModuleMember -Function Get-CiTool, Initialize-CiEnvironment, New-CiResult, Invoke-CiCommand, Find-CiMatch,
                              Complete-CiResult, Test-CiSelected, Write-CiResults
