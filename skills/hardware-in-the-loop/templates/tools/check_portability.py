#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 check_portability.py - keeps every helper script in the repository runnable on any machine and any console.

 Interpreter: any Python >= 3.9 (stdlib only). Run it with `python -I` so nothing from the working directory is imported.

 Scope: every *.py, *.sh, *.ps1 and *.psm1 file under --root, except .git/, .tmp/, harness/ (live coordination, not
 shipped), node_modules/, virtual environments (any folder holding pyvenv.cfg) and directories named in --exclude.

 Rules (each failure names the file and line):
   R1 no machine-specific absolute paths: a Windows user-profile path, /home/<user>/..., /Users/<user>/..., a tilde
      home path, the shared Unix temp root (a font fallback inside a try: block is allowed; it never breaks a run).
      A line that must name such a path as data (a pattern table, a refusal check) carries the marker
      "portability:allow" and is skipped by R1; a reviewer sees every such line with one grep.
   R2 repo files are found relative to the script, never relative to the caller's working directory:
        Python      the script uses __file__ when it reads or writes files. Exempt: an import-only helper (no
                    `if __name__ == "__main__":` guard and no file I/O at module level: its caller hands it the paths),
                    and a tool whose header says "Paths: command line" (every path it touches is an argument).
        bash        the script anchors on $0 or BASH_SOURCE (dirname "$0", readlink -f "$0", ...).
        PowerShell  the script anchors on $PSScriptRoot or $MyInvocation.MyCommand.Path.
   R3 the interpreter is stated in the header: an "Interpreter: ..." line or a "Run: <interpreter> ..." line within the
      first 60 lines (a shebang alone does not count: on Windows it says nothing about which python runs the file)
   R4 Windows-console-safe output: a Python script that prints reconfigures stdout to UTF-8 (errors="replace"), so a
      part name with a Greek letter or a CJK brand never kills a cp1252 console. Blender scripts are exempt.

 Usage:  python -I tools/check_portability.py [--root DIR] [--json OUT] [--fail-on-any] [--quiet] [--exclude NAME ...]
 Exit:   0 = report written (and with --fail-on-any: every script portable), 1 = a script failed with --fail-on-any,
         2 = bad arguments.
=======================================================================================================================
"""
import argparse
import ast
import json
import os
import re
import sys
import time

# ---------------------------------------------------------------------------------------------------------------------
#  Console safety first: this tool prints file paths and source lines that may hold any Unicode character
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------------------------------------------------
#  Patterns. Kept at module level so a reviewer can read every rule in one place.
# ---------------------------------------------------------------------------------------------------------------------
# R1: user-profile and machine-local absolute paths. The look-behind guards keep "a/tmp/b" relative paths and
#     "${VAR}~/" style text legal. The pattern is assembled from pieces so this file does not match itself.
_PROFILE = "[A-Za-z]:[\\\\/]+" + "Users" + "[\\\\/]+[^\\\\/\\s\"']+"
ABS_RE = re.compile("(" + _PROFILE + r"|/home/[A-Za-z]|/Users/[A-Za-z]|(?<![\w.$}])~/|(?<![\w.])/tmp/)")  # portability:allow
# the reviewed per-line exemption from R1
ALLOW_MARK = "portability:" + "allow"
# fonts are the one place where a fallback absolute path is tolerated (inside try:)
FONT_RE = re.compile(r"/usr/share/fonts/|[A-Za-z]:[\\/]+Windows[\\/]+Fonts", re.IGNORECASE)
# R2 (Python): calls that read or write files
FILE_IO_RE = re.compile(r"\b(open|glob\.glob|os\.listdir|os\.makedirs|os\.walk|json\.load|json\.dump|savefig|trimesh\.load|"
                        r"exporters\.export|exportStl|exportStep|importStep|Image\.open|shutil\.copy\w*|runpy\.run_path)\b")
# R3: the interpreter line, or a "Run: <python / pwsh / bash / blender ...>" line
INTERP_RE = re.compile(r"Interpreter\s*:", re.IGNORECASE)
RUN_INTERP_RE = re.compile(r"\bRun\s*(\([^)]*\))?\s*:.*\b(python(\.exe|3)?|pwsh|powershell|bash|blender)\b", re.IGNORECASE)
# R2 escape hatch: every path comes from the command line
PATHS_ARGV_RE = re.compile(r"\bPaths\s*:\s*command line\b", re.IGNORECASE)
# R4: stdout reconfigured to UTF-8
UTF8_RE = re.compile(r"reconfigure\(\s*encoding\s*=\s*[\"']utf-?8[\"']", re.IGNORECASE)
# R2 for shells
SH_ANCHOR_RE = re.compile(r"(dirname\s+\"?\$\{?0|readlink\s+-f\s+\"?\$\{?0|BASH_SOURCE)")
PS_ANCHOR_RE = re.compile(r"(\$PSScriptRoot|\$MyInvocation\.MyCommand\.Path|\$PSCommandPath)")

# directories that are never in scope (scratch, live coordination, vendored environments, local agent state)
SKIP_DIRS = {".git", ".tmp", "harness", "node_modules", "__pycache__", ".venv", "venv", ".claude"}


def is_main_guard(node):
    """True for a module-level `if __name__ == "__main__":` in either operand order."""
    test = getattr(node, "test", None)
    if not isinstance(node, ast.If) or not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    sides = [test.left] + list(test.comparators)
    names = [s.id for s in sides if isinstance(s, ast.Name)]
    consts = [s.value for s in sides if isinstance(s, ast.Constant)]
    return names == ["__name__"] and consts == ["__main__"]


def is_library(src):
    """
    True for an import-only helper module: no __main__ guard and no file I/O in module-level statements.
    Such a module receives every path from its caller, so anchoring on __file__ is the caller's job.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False                                   # unparsable: treat as a script so every rule applies
    for st in tree.body:
        if is_main_guard(st):
            return False                               # runnable on its own: a script
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)):
            continue                                   # definitions and imports do no I/O when executed
        if isinstance(st, ast.Expr) and isinstance(getattr(st, "value", None), ast.Constant):
            continue                                   # the module docstring
        seg = ast.get_source_segment(src, st) or ""
        if FILE_IO_RE.search(seg):
            return False                               # file I/O at import time
    return True


def in_try_block(lines, idx):
    """True when line idx (0-based) is a `try:` line or sits in the body of the nearest enclosing try at lower indent."""
    if idx < 0 or idx >= len(lines):
        return False
    line = lines[idx]
    if re.match(r"\s*try\s*:", line):
        return True
    indent = len(line) - len(line.lstrip())
    for k in range(idx - 1, max(-1, idx - 40), -1):
        prev = lines[k]
        if not prev.strip():
            continue
        pind = len(prev) - len(prev.lstrip())
        if pind < indent:
            return bool(re.match(r"\s*try\s*:", prev))
    return False


def scope_files(root, extra_skip):
    """Repo-relative paths (forward slashes) of every script in scope, sorted for a stable report."""
    out = []
    skip = SKIP_DIRS | set(extra_skip or ())
    for dirpath, dirnames, filenames in os.walk(root):
        # ---- prune skipped names and anything that is a virtual environment (pyvenv.cfg inside)
        keep = []
        for d in dirnames:
            if d in skip:
                continue
            if os.path.isfile(os.path.join(dirpath, d, "pyvenv.cfg")):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in filenames:
            if name.endswith((".py", ".sh", ".ps1", ".psm1")):
                rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
                out.append(rel)
    return sorted(out)


def check_file(root, rel):
    """List of problems [(rule, line, text)] for one script; an unreadable file is one problem, never a crash."""
    path = os.path.join(root, rel)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as exc:
        return [("read", 0, str(exc))]
    lines = src.splitlines()
    probs = []
    is_py = rel.endswith(".py")
    is_blender = is_py and re.search(r"^\s*import\s+bpy\b", src, re.MULTILINE) is not None

    # ---- R1: absolute machine paths
    for i, line in enumerate(lines):
        if ALLOW_MARK in line:
            continue                                   # reviewed data line (pattern table, refusal check)
        if ABS_RE.search(line):
            probs.append(("R1", i + 1, line.strip()[:160]))
        elif FONT_RE.search(line) and not in_try_block(lines, i) and "FONT_RE" not in line:
            probs.append(("R1", i + 1, "font path outside try: " + line.strip()[:140]))

    # ---- R2: anchoring on the script's own location
    header = lines[:60]
    argv_paths = any(PATHS_ARGV_RE.search(l) for l in header)
    if is_py and FILE_IO_RE.search(src) and "__file__" not in src and not is_blender and not is_library(src) and not argv_paths:
        probs.append(("R2", 0, "reads or writes files but never resolves paths from __file__"))
    if rel.endswith(".sh") and not SH_ANCHOR_RE.search(src) and not argv_paths:
        probs.append(("R2", 0, "shell script does not anchor on $0 / BASH_SOURCE"))
    if rel.endswith((".ps1", ".psm1")) and not PS_ANCHOR_RE.search(src) and not argv_paths:
        probs.append(("R2", 0, "PowerShell script does not anchor on $PSScriptRoot / $MyInvocation"))

    # ---- R3: interpreter documented in the header
    if not any(INTERP_RE.search(l) or RUN_INTERP_RE.search(l) for l in header):
        probs.append(("R3", 0, "no 'Interpreter:' or 'Run: <interpreter> ...' line in the first 60 lines"))

    # ---- R4: UTF-8 console for Python scripts that print
    if is_py and not is_blender and re.search(r"\bprint\s*\(", src) and not UTF8_RE.search(src):
        probs.append(("R4", 0, "prints but does not reconfigure stdout to UTF-8 (errors='replace')"))
    return probs


def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="Script portability check (rules R1-R4, see the module docstring)")
    ap.add_argument("--root", default=os.path.abspath(os.path.join(here, "..")), help="tree to scan (default: this repo)")
    ap.add_argument("--json", default=None, help="write the machine-readable report here")
    ap.add_argument("--fail-on-any", action="store_true", help="exit 1 when any script fails a rule")
    ap.add_argument("--quiet", action="store_true", help="print only the summary line")
    ap.add_argument("--exclude", nargs="*", default=[], help="extra directory names to skip")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"[portability] ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2

    # ---- check every script in scope
    t0 = time.time()
    files = []
    for rel in scope_files(root, args.exclude):
        probs = check_file(root, rel)
        files.append({"file": rel, "ok": not probs,
                      "problems": [{"rule": r, "line": ln, "text": tx} for r, ln, tx in probs]})
    ok = sum(1 for f in files if f["ok"])
    report = {
        "schema": "hil-portability/1",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": root,
        "elapsed_s": round(time.time() - t0, 2),
        "totals": {"checked": len(files), "ok": ok, "failed": len(files) - ok},
        "files": files,
    }

    # ---- report file (UTF-8, LF) when asked
    if args.json:
        out_dir = os.path.dirname(os.path.abspath(args.json))
        os.makedirs(out_dir, exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, indent=1, ensure_ascii=False)

    # ---- console: one line per problem, then the summary line the CI runner parses
    if not args.quiet:
        for f in files:
            for p in f["problems"]:
                print(f"{f['file']}:{p['line']}  {p['rule']}  {p['text']}")
    print(f"[portability] {ok}/{len(files)} scripts portable")
    return 1 if (args.fail_on_any and ok != len(files)) else 0


if __name__ == "__main__":
    sys.exit(main())
