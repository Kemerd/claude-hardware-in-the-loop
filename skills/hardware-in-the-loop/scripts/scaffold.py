#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 scaffold.py - writes the project skeleton of the hardware-in-the-loop method into a repository, from the answers of
               the onboarding interview.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python -I scripts/scaffold.py --answers answers.json --dest <repo>
 Paths: command line (the answers file and the destination); the templates resolve from __file__

 What it does
   1. validates answers.json against the interview schema (references/interview.md section "answers.json"): every
      required key present and non-empty, numbers in range;
   2. copies skills/hardware-in-the-loop/templates/ into --dest:
        gitignore.template                 -> .gitignore
        gitattributes.template             -> .gitattributes
        claude/settings.local.json.example -> .claude/settings.local.json
        pcb/example_board/ and cad/examples/ only with --with-examples
      an existing file is NEVER overwritten (it is listed as kept; compare by hand);
   3. fills the {{KEY}} template variables in Markdown files from the answers; any variable left unfilled is an
      error (exit 1) and the file is not written;
   4. writes harness/swarm/machine.env (CPU cap, RAM reserve, disk floor, handoff threshold, project name), creates
      .tmp/, docs/cad_decisions/, harness/swarm/resume_<today>/, and harness/swarm/PLAN.md from the plan template
      of the chosen mode;
   5. prints what was written, kept and skipped, and the next commands (git init / branches, setup_tools, first CI).
 It never runs git, never touches anything outside --dest, and with --dry-run writes nothing at all.
=======================================================================================================================
"""
import argparse
import datetime
import json
import os
import re
import shutil
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SKILL = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TEMPLATES = os.path.join(SKILL, "templates")
REFERENCES = os.path.join(SKILL, "references")

# renamed on copy (template name -> destination path, forward slashes)
RENAMES = {
    "gitignore.template": ".gitignore",
    "gitattributes.template": ".gitattributes",
    "claude/settings.local.json.example": ".claude/settings.local.json",
}
# copied only with --with-examples
EXAMPLE_DIRS = ("pcb/example_board", "cad/examples")
# never copied (caches a test run may have left in the template tree)
SKIP_NAMES = {"__pycache__", ".tmp", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".kicad_prl")

# answers.json: key -> (required, kind); kinds: text, int, list
SCHEMA = {
    "project_name": (True, "text"),
    "project_summary": (True, "text"),
    "goals": (True, "list"),
    "acceptance": (True, "list"),
    "budget": (True, "text"),
    "quantity": (True, "text"),
    "material_default": (True, "text"),
    "fastening": (True, "text"),
    "enclosure": (True, "text"),
    "fab_vendor": (True, "text"),
    "mcu_family": (True, "text"),
    "sim_tiers": (True, "text"),
    "gpu": (False, "text"),
    "mode": (True, "text"),
    "archive_path": (True, "text"),
    "cpu_max_pct": (True, "int"),
    "ram_reserve_gb": (True, "int"),
    "disk_free_min_gb": (True, "int"),
    "handoff_pct": (True, "int"),
    "decisions": (False, "list"),
}
RANGES = {"cpu_max_pct": (10, 100), "ram_reserve_gb": (1, 512), "disk_free_min_gb": (1, 100000), "handoff_pct": (50, 101)}
VAR_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


def fail(msg, code=2):
    """Print an error and exit."""
    print(f"[scaffold] ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def load_answers(path):
    """Validated answers dict; every problem is listed before exiting, so one run shows them all."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            a = json.load(fh)
    except (OSError, ValueError) as exc:
        fail(f"cannot read answers {path}: {exc}")
    if not isinstance(a, dict):
        fail("answers.json must be a JSON object")
    problems = []
    for key, (required, kind) in SCHEMA.items():
        v = a.get(key)
        if v is None or (isinstance(v, (str, list)) and len(v) == 0):
            if required:
                problems.append(f"{key}: missing or empty")
            continue
        if kind == "text" and not isinstance(v, str):
            problems.append(f"{key}: must be text")
        elif kind == "int" and (isinstance(v, bool) or not isinstance(v, int)):
            problems.append(f"{key}: must be a whole number")
        elif kind == "list" and not (isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v)):
            problems.append(f"{key}: must be a list of non-empty strings")
    for key, (lo, hi) in RANGES.items():
        v = a.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and not (lo <= v <= hi):
            problems.append(f"{key}: {v} outside {lo}..{hi}")
    if a.get("mode") not in ("swarm", "single"):
        problems.append("mode: must be 'swarm' or 'single'")
    if problems:
        fail("answers.json problems:\n  " + "\n  ".join(problems))
    return a


def variables(a, today):
    """The {{KEY}} values used in the Markdown templates."""
    bullets = lambda xs: "\n".join(f"- {x.strip()}" for x in xs)
    decisions = a.get("decisions") or []
    rows = "\n".join(f"| {i + 1} | {today} | {d.strip()} |" for i, d in enumerate(decisions))
    return {
        "PROJECT_NAME": a["project_name"].strip(),
        "PROJECT_SUMMARY": a["project_summary"].strip(),
        "GOALS": bullets(a["goals"]),
        "ACCEPTANCE": "\n".join(f"- [ ] {x.strip()}" for x in a["acceptance"]),
        "BUDGET": a["budget"].strip(),
        "QUANTITY": a["quantity"].strip(),
        "MATERIAL_DEFAULT": a["material_default"].strip(),
        "FASTENING": a["fastening"].strip(),
        "ENCLOSURE": a["enclosure"].strip(),
        "FAB_VENDOR": a["fab_vendor"].strip(),
        "MCU_FAMILY": a["mcu_family"].strip(),
        "SIM_TIERS": a["sim_tiers"].strip() + (f" (GPU: {a['gpu'].strip()})" if a.get("gpu") else ""),
        "ARCHIVE_PATH": a["archive_path"].strip(),
        "CPU_MAX": str(a["cpu_max_pct"]),
        "RAM_RESERVE_GB": str(a["ram_reserve_gb"]),
        "DISK_FREE_MIN_GB": str(a["disk_free_min_gb"]),
        "DATE": today,
        "MODE": a["mode"],
        "DECISIONS": rows if rows else f"| 0 | {today} | (no standing decision yet; the lead appends them here) |",
    }


def fill(text, var, where):
    """Replace every {{KEY}}; an unknown key is an error naming the file."""
    missing = sorted({m for m in VAR_RE.findall(text) if m not in var})
    if missing:
        raise ValueError(f"{where}: no value for {', '.join(missing)}")
    return VAR_RE.sub(lambda m: var[m.group(1)], text)


def plan_files(with_examples):
    """[(template path, destination relative path)] for every file to copy."""
    out = []
    for dirpath, dirnames, filenames in os.walk(TEMPLATES):
        rel_dir = os.path.relpath(dirpath, TEMPLATES).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            sub = f"{rel_dir}/{d}" if rel_dir else d
            if d in SKIP_NAMES:
                continue
            if not with_examples and any(sub == e or sub.startswith(e + "/") for e in EXAMPLE_DIRS):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            if name.endswith(SKIP_SUFFIX):
                continue
            rel = f"{rel_dir}/{name}" if rel_dir else name
            out.append((os.path.join(dirpath, name), RENAMES.get(rel, rel)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write the hardware-in-the-loop project skeleton")
    ap.add_argument("--answers", required=True, help="answers.json from the onboarding interview")
    ap.add_argument("--dest", required=True, help="repository root to write into (created when missing)")
    ap.add_argument("--with-examples", action="store_true", help="also copy the example board and CAD examples")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written, write nothing")
    args = ap.parse_args(argv)

    if not os.path.isdir(TEMPLATES):
        fail(f"templates not found at {TEMPLATES}")
    a = load_answers(args.answers)
    dest = os.path.abspath(args.dest)
    home = os.path.abspath(os.path.expanduser("~"))
    if os.path.normcase(dest) == os.path.normcase(home):
        fail("refusing to scaffold into the home directory itself")
    today = datetime.date.today().isoformat()
    var = variables(a, today)

    written, kept, errors = [], [], []
    # ---- 1. render everything first, so a template error writes nothing at all
    jobs = []
    for src, rel in plan_files(args.with_examples):
        target = os.path.join(dest, *rel.split("/"))
        if os.path.exists(target):
            kept.append(rel)
            continue
        if rel.endswith(".md"):
            try:
                with open(src, "r", encoding="utf-8") as fh:
                    jobs.append((target, rel, fill(fh.read(), var, rel), src))
            except ValueError as exc:
                errors.append(str(exc))
        else:
            jobs.append((target, rel, None, src))
    # ---- the plan for the chosen mode and the machine limits
    plan_src = os.path.join(REFERENCES, "plan-template.md" if a["mode"] == "swarm" else "single-session-plan.md")
    plan_dst = os.path.join(dest, "harness", "swarm", "PLAN.md")
    if os.path.exists(plan_dst):
        kept.append("harness/swarm/PLAN.md")
    elif os.path.isfile(plan_src):
        with open(plan_src, "r", encoding="utf-8") as fh:
            try:
                jobs.append((plan_dst, "harness/swarm/PLAN.md", fill(fh.read(), var, os.path.basename(plan_src)), plan_src))
            except ValueError as exc:
                errors.append(str(exc))
    env_dst = os.path.join(dest, "harness", "swarm", "machine.env")
    env_text = (f"# machine limits from the onboarding interview ({today}); read by slot.sh and usage_statusline.py\n"
                f"HIL_PROJECT_NAME={re.sub(r'[^0-9A-Za-z_.-]', '_', a['project_name'].strip())}\n"
                f"HIL_CPU_MAX={a['cpu_max_pct']}\nHIL_RAM_RESERVE_GB={a['ram_reserve_gb']}\n"
                f"HIL_DISK_FREE_MIN_GB={a['disk_free_min_gb']}\nHIL_HANDOFF_PCT={a['handoff_pct']}\n")
    if os.path.exists(env_dst):
        kept.append("harness/swarm/machine.env")
    else:
        jobs.append((env_dst, "harness/swarm/machine.env", env_text, None))
    if errors:
        fail("template variables without a value (nothing written):\n  " + "\n  ".join(errors), code=1)

    # ---- 2. write
    if not args.dry_run:
        for target, rel, text, src in jobs:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if text is None:
                shutil.copyfile(src, target)                    # binary-safe copy of a non-Markdown template
            else:
                with open(target, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
            written.append(rel)
        for d in (".tmp", os.path.join("docs", "cad_decisions"), os.path.join("harness", "swarm", f"resume_{today}")):
            os.makedirs(os.path.join(dest, d), exist_ok=True)
    else:
        written = [rel for _, rel, _, _ in jobs]

    # ---- 3. report
    tag = "would write" if args.dry_run else "wrote"
    print(f"[scaffold] {tag} {len(written)} files into {dest}; kept {len(kept)} existing")
    for rel in written:
        print(f"  + {rel}")
    for rel in kept:
        print(f"  = {rel} (exists, not overwritten)")
    print("[scaffold] next: git init -b main; git add -A; git commit; git branch dev; "
          "pwsh -File <skill>/scripts/setup_tools.ps1 -ToolsRoot <tools dir>; pwsh -File windows/ci.ps1 -Only audit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
