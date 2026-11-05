#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 hil_update.py - keeps a project that was scaffolded from the hardware-in-the-loop method in step with the method's
                 repository, even when the project has customised every template.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python -I tools/hil_update.py <command> [options]
 Paths: the project root is the parent of this file's folder (override with --repo); the version stamp is
        harness/TEMPLATE_VERSION.json; scratch goes to .tmp/ (the download in .tmp/hil_upstream, diffs and the plan
        in .tmp/hil_update)

 Commands
   check            cheap, silent when current, never fails: compares the stamped version with the VERSION file on
                    the upstream main branch (one small HTTPS request, at most once a day) and, when upstream is
                    newer, prints the CHANGELOG entries in between. Meant for a SessionStart hook.
                      --force      ignore the once-a-day cache
   ignore VERSION   stop announcing that version (a newer one is announced again)
   plan             downloads upstream main (zip, no git needed) and sorts every template file into
                      new      upstream has it, the project does not: safe to add
                      update   upstream changed it and the project copy is untouched since the scaffold: safe to overwrite
                      merge    upstream changed it AND the project changed it (or it is a filled-in Markdown template):
                               a unified diff is written to .tmp/hil_update/diff/, merge it by hand
                      same / keep / removed   nothing to do
                    and writes .tmp/hil_update/plan.json. Changes nothing in the project.
   apply            performs the safe part of the plan (new + update), bumps the stamp, lists what still needs merging
   mark PATH...     after merging a file by hand: records it as merged so the next plan treats it as current
   adopt            stamps a project that was scaffolded before versions existed (every file counts as customised
                    until it matches upstream, so nothing is ever overwritten blindly)

 Common options: --repo DIR (project root), --upstream DIR (a local checkout of the method repo instead of GitHub;
 for forks, tests and offline use). Environment: HIL_NO_UPDATE_CHECK=1 turns `check` off.
 A file is never overwritten when the project changed it. The stamp is project-owned: commit it.
=======================================================================================================================
"""
import argparse
import difflib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

UPSTREAM_REPO = "Kemerd/claude-hardware-in-the-loop"
RAW_BASE = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/main/skills/hardware-in-the-loop/"
ZIP_URL = f"https://github.com/{UPSTREAM_REPO}/archive/refs/heads/main.zip"
SKILL_REL = "skills/hardware-in-the-loop"
STAMP_REL = "harness/TEMPLATE_VERSION.json"
CHECK_EVERY_S = 24 * 3600
HTTP_TIMEOUT_S = 4          # `check` runs at every session start: a dead network must cost seconds, not minutes
ZIP_TIMEOUT_S = 120

# These four mirror scripts/scaffold.py (RENAMES, EXAMPLE_DIRS, TIER_DIRS, VIEWER_PATHS, SKIP_*). Keep them in step.
RENAMES = {
    "gitignore.template": ".gitignore",
    "gitattributes.template": ".gitattributes",
    "claude/settings.local.json.example": ".claude/settings.local.json",
}
EXAMPLE_DIRS = ("pcb/example_board", "cad/examples")
TIER_DIRS = {"sim/unreal": "unreal", "sim/isaac": "isaac"}
VIEWER_PATHS = ("tools/cad_viewer", "windows/cad_viewer.ps1")
SKIP_NAMES = {"__pycache__", ".tmp", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".kicad_prl")


# ---------------------------------------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------------------------------------
def root_of(args):
    """Project root: --repo, else the parent of the folder this script lives in."""
    if args.repo:
        return os.path.abspath(args.repo)
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def sha(path):
    """SHA-256 of a file with CRLF folded to LF (a Windows checkout must not look customised); None if unreadable."""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read().replace(b"\r\n", b"\n")).hexdigest()
    except OSError:
        return None


def vtuple(text):
    """'1.2.10' -> (1, 2, 10); anything unparsable -> None."""
    m = re.fullmatch(r"\s*v?(\d+)\.(\d+)\.(\d+)\s*", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else default
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def http_text(url, timeout):
    """Body of a URL as text, or None on any failure (offline, rate limit, 404): callers must cope."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "hil-update-check"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception:
        return None


def changelog_between(text, installed, latest):
    """CHANGELOG.md sections ('## x.y.z ...') with installed < version <= latest, newest first, as one string."""
    out = []
    for block in re.split(r"(?m)^(?=## )", text or ""):
        m = re.match(r"## \[?v?(\d+\.\d+\.\d+)", block)
        v = vtuple(m.group(1)) if m else None
        if v and installed < v <= latest:
            out.append((v, block.rstrip()))
    return "\n\n".join(b for _, b in sorted(out, reverse=True))


# ---------------------------------------------------------------------------------------------------------------------
# check / ignore
# ---------------------------------------------------------------------------------------------------------------------
def cmd_check(args):
    """Announce a newer upstream version. Silent when current, disabled, offline or ignored. Never raises."""
    try:
        if os.environ.get("HIL_NO_UPDATE_CHECK"):
            return 0
        root = root_of(args)
        stamp = load_json(os.path.join(root, *STAMP_REL.split("/")), {})
        installed = vtuple(stamp.get("version", ""))
        if not installed:
            print("HIL_UPDATE_UNSTAMPED this project has no method version stamp. If it came from the "
                  "hardware-in-the-loop skeleton, run: python -I tools/hil_update.py adopt")
            return 0
        cache_path = os.path.join(root, ".tmp", "hil_update_check.json")
        cache = load_json(cache_path, {})
        latest_s, log = cache.get("latest"), cache.get("changelog", "")
        if args.force or time.time() - float(cache.get("checked", 0)) > CHECK_EVERY_S or not latest_s:
            if args.upstream:
                base = os.path.join(os.path.abspath(args.upstream), *SKILL_REL.split("/"))
                read = lambda n: open(os.path.join(base, n), encoding="utf-8").read() if os.path.isfile(os.path.join(base, n)) else None
            else:
                read = lambda n: http_text(RAW_BASE + n, HTTP_TIMEOUT_S)
            fetched = read("VERSION")
            if fetched and vtuple(fetched):
                latest_s, log = fetched.strip(), read("CHANGELOG.md") or ""
                save_json(cache_path, {"checked": time.time(), "latest": latest_s, "changelog": log,
                                       "ignored": cache.get("ignored", "")})
            else:
                return 0                                 # offline or unreadable: say nothing, try again next session
        latest = vtuple(latest_s)
        pending = stamp.get("pending_merge") or []
        if latest and latest > installed and cache.get("ignored") != latest_s:
            print(f"HIL_UPDATE_AVAILABLE installed={stamp['version']} latest={latest_s}")
            print("What is new since the installed version:\n")
            print(changelog_between(log, installed, latest) or "(no changelog entry found)")
            print("\nFor the agent: tell the owner this in two or three plain lines (what improves, how long it takes), "
                  "ask whether to pull it into this project now, and follow README_AGENT.md section 7. "
                  "Do not update without a yes. If the answer is no: python -I tools/hil_update.py ignore " + latest_s)
        elif pending:
            print(f"HIL_UPDATE_PENDING_MERGE {len(pending)} file(s) from the last update still need merging by hand: "
                  + ", ".join(pending[:8]) + (" ..." if len(pending) > 8 else ""))
    except Exception as exc:                             # a version nag must never break a session
        print(f"[hil_update] check skipped: {exc}", file=sys.stderr)
    return 0


def cmd_ignore(args):
    root = root_of(args)
    path = os.path.join(root, ".tmp", "hil_update_check.json")
    cache = load_json(path, {})
    if not vtuple(args.version):
        print("[hil_update] give a version like 1.2.0", file=sys.stderr)
        return 2
    cache["ignored"] = args.version.strip()
    save_json(path, cache)
    print(f"[hil_update] {args.version} will not be announced again (a newer version will)")
    return 0


# ---------------------------------------------------------------------------------------------------------------------
# upstream tree and the plan
# ---------------------------------------------------------------------------------------------------------------------
def fetch_upstream(root, local):
    """Skill folder of the upstream repo as a local path: a --upstream checkout, or main.zip unpacked to .tmp."""
    if local:
        skill = os.path.join(os.path.abspath(local), *SKILL_REL.split("/"))
        return skill if os.path.isdir(skill) else None
    dest = os.path.join(root, ".tmp", "hil_upstream")
    try:
        req = urllib.request.Request(ZIP_URL, headers={"User-Agent": "hil-update-check"})
        with urllib.request.urlopen(req, timeout=ZIP_TIMEOUT_S) as resp:
            data = resp.read()
        shutil.rmtree(dest, ignore_errors=True)
        os.makedirs(dest, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            for name in zf.namelist():                    # refuse anything that would land outside dest
                target = os.path.abspath(os.path.join(dest, name))
                if not target.startswith(os.path.abspath(dest) + os.sep):
                    raise ValueError(f"unsafe path in archive: {name}")
            zf.extractall(dest)
    except Exception as exc:
        print(f"[hil_update] cannot download upstream: {exc}", file=sys.stderr)
        return None
    for entry in os.listdir(dest):
        skill = os.path.join(dest, entry, *SKILL_REL.split("/"))
        if os.path.isdir(skill):
            return skill
    return None


def template_files(templates, opts):
    """{destination path: template file} for the options the project was scaffolded with (mirrors scaffold.plan_files)."""
    out = {}
    tiers = (opts.get("sim_tiers") or "").lower()
    for dirpath, dirnames, filenames in os.walk(templates):
        rel_dir = os.path.relpath(dirpath, templates).replace("\\", "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        keep = []
        for d in dirnames:
            sub = f"{rel_dir}/{d}" if rel_dir else d
            if d in SKIP_NAMES:
                continue
            if not opts.get("with_examples") and any(sub == e or sub.startswith(e + "/") for e in EXAMPLE_DIRS):
                continue
            if any((sub == t or sub.startswith(t + "/")) and k not in tiers for t, k in TIER_DIRS.items()):
                continue
            if opts.get("cad_viewer") is False and any(sub == v or sub.startswith(v + "/") for v in VIEWER_PATHS):
                continue
            keep.append(d)
        dirnames[:] = sorted(keep)
        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if name.endswith(SKIP_SUFFIX) or (opts.get("cad_viewer") is False and rel in VIEWER_PATHS):
                continue
            out[RENAMES.get(rel, rel)] = os.path.join(dirpath, name)
    return out


def build_plan(root, skill, stamp):
    """Sort every upstream template file into new / update / merge / same / keep / removed."""
    files = stamp.get("files") or {}
    up = template_files(os.path.join(skill, "templates"), stamp.get("options") or {})
    plan = {"new": [], "update": [], "merge": [], "same": [], "keep": [], "removed": []}
    for dest, src in sorted(up.items()):
        s_new, mine = sha(src), os.path.join(root, *dest.split("/"))
        rec = files.get(dest) or {}
        if not os.path.exists(mine):
            plan["removed" if rec else "new"].append(dest)      # deleted on purpose in the project, or really new
            continue
        p = sha(mine)
        if p == s_new:
            plan["same"].append(dest)
        elif rec.get("src") == s_new:
            plan["keep"].append(dest)                           # upstream unchanged; any difference is the project's own
        elif rec.get("out") and rec["out"] == p and "{{" not in open(src, encoding="utf-8", errors="replace").read():
            plan["update"].append(dest)                         # untouched since the scaffold, upstream moved on
        else:
            plan["merge"].append(dest)                          # both sides changed, a filled-in template, or unknown history
    return plan, up


def write_diffs(root, plan, up):
    """Unified diffs (project -> upstream) for the files that need a manual merge."""
    out_dir = os.path.join(root, ".tmp", "hil_update", "diff")
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(out_dir, exist_ok=True)
    for dest in plan["merge"]:
        try:
            a = open(os.path.join(root, *dest.split("/")), encoding="utf-8", errors="replace").read().splitlines(True)
            b = open(up[dest], encoding="utf-8", errors="replace").read().splitlines(True)
        except OSError:
            continue
        diff = "".join(difflib.unified_diff(a, b, f"project/{dest}", f"upstream/{dest}"))
        with open(os.path.join(out_dir, dest.replace("/", "__") + ".diff"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(diff)
    return out_dir


def load_upstream_version(skill):
    try:
        return open(os.path.join(skill, "VERSION"), encoding="utf-8").read().strip()
    except OSError:
        return ""


def cmd_plan(args):
    root = root_of(args)
    stamp = load_json(os.path.join(root, *STAMP_REL.split("/")), {})
    if not stamp:
        print("[hil_update] no stamp: run `adopt` first", file=sys.stderr)
        return 2
    skill = fetch_upstream(root, args.upstream)
    if not skill:
        return 1
    plan, up = build_plan(root, skill, stamp)
    diff_dir = write_diffs(root, plan, up)
    version = load_upstream_version(skill)
    save_json(os.path.join(root, ".tmp", "hil_update", "plan.json"),
              {"installed": stamp.get("version"), "upstream": version, "skill_dir": skill, **plan})
    print(f"[hil_update] installed {stamp.get('version')} -> upstream {version}")
    for key, note in (("new", "will be added"), ("update", "will be overwritten (untouched in the project)"),
                      ("merge", f"merge by hand, diffs in {diff_dir}"), ("removed", "you deleted these; not re-added"),
                      ("keep", "upstream unchanged"), ("same", "identical")):
        print(f"  {key:8} {len(plan[key]):4}  {note}")
        if key in ("new", "update", "merge", "removed"):
            for dest in plan[key]:
                print(f"           {dest}")
    print("[hil_update] next: apply (new + update), merge the 'merge' files, then mark <files>")
    return 0


def record(stamp, dest, src, root):
    """Stamp one file: the upstream template hash it came from and the project hash it has now."""
    stamp.setdefault("files", {})[dest] = {"src": sha(src), "out": sha(os.path.join(root, *dest.split("/")))}


def cmd_apply(args):
    root = root_of(args)
    sp = os.path.join(root, *STAMP_REL.split("/"))
    stamp = load_json(sp, {})
    skill = fetch_upstream(root, args.upstream) if args.upstream else None
    if not skill:                                         # reuse the download `plan` made
        cached = load_json(os.path.join(root, ".tmp", "hil_update", "plan.json"), {})
        skill = cached.get("skill_dir") if os.path.isdir(cached.get("skill_dir") or "") else None
    if not stamp or not skill:
        print("[hil_update] run `plan` first (it downloads upstream)", file=sys.stderr)
        return 2
    plan, up = build_plan(root, skill, stamp)
    for dest in plan["new"] + plan["update"]:
        target = os.path.join(root, *dest.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(up[dest], target)
        record(stamp, dest, up[dest], root)
        print(f"  {'+' if dest in plan['new'] else '~'} {dest}")
    for dest in plan["same"]:
        record(stamp, dest, up[dest], root)
    for dest in plan["keep"]:                             # upstream unchanged: remember the hash, keep the project's out
        stamp.setdefault("files", {}).setdefault(dest, {})["src"] = sha(up[dest])
    stamp["version"] = load_upstream_version(skill) or stamp.get("version")
    stamp["updated"] = time.strftime("%Y-%m-%d")
    stamp["pending_merge"] = sorted(set(plan["merge"]))
    save_json(sp, stamp)
    save_json(os.path.join(root, ".tmp", "hil_update_check.json"),
              {**load_json(os.path.join(root, ".tmp", "hil_update_check.json"), {}), "checked": 0})
    print(f"[hil_update] stamp now {stamp['version']}; {len(plan['merge'])} file(s) to merge by hand: "
          + (", ".join(plan["merge"]) or "none"))
    return 0


def cmd_mark(args):
    root = root_of(args)
    sp = os.path.join(root, *STAMP_REL.split("/"))
    stamp = load_json(sp, {})
    plan = load_json(os.path.join(root, ".tmp", "hil_update", "plan.json"), {})
    skill = plan.get("skill_dir")
    if not stamp or not skill or not os.path.isdir(skill):
        print("[hil_update] run `plan` first", file=sys.stderr)
        return 2
    up = template_files(os.path.join(skill, "templates"), stamp.get("options") or {})
    for dest in args.paths:
        dest = dest.replace("\\", "/")
        if dest not in up or not os.path.exists(os.path.join(root, *dest.split("/"))):
            print(f"  ! {dest}: not an upstream template file or missing in the project", file=sys.stderr)
            continue
        record(stamp, dest, up[dest], root)
        stamp["pending_merge"] = [p for p in stamp.get("pending_merge", []) if p != dest]
        print(f"  = {dest} marked merged")
    save_json(sp, stamp)
    return 0


def cmd_adopt(args):
    root = root_of(args)
    sp = os.path.join(root, *STAMP_REL.split("/"))
    if os.path.exists(sp):
        print("[hil_update] already stamped")
        return 0
    # no history is known: version 1.0.0 (the first stamped release), no file records, so every differing file is a merge
    save_json(sp, {"version": "1.0.0", "upstream": UPSTREAM_REPO, "adopted": time.strftime("%Y-%m-%d"),
                   "options": {"with_examples": os.path.isdir(os.path.join(root, "pcb", "example_board")),
                               "sim_tiers": ("unreal " if os.path.isdir(os.path.join(root, "sim", "unreal")) else "")
                               + ("isaac" if os.path.isdir(os.path.join(root, "sim", "isaac")) else ""),
                               "cad_viewer": os.path.isdir(os.path.join(root, "tools", "cad_viewer"))},
                   "files": {}, "pending_merge": []})
    print(f"[hil_update] wrote {STAMP_REL} (version 1.0.0, no file history: nothing is overwritten without a merge)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Keep a scaffolded project in step with the method repository")
    ap.add_argument("--repo", help="project root (default: parent of this script's folder)")
    ap.add_argument("--upstream", help="local checkout of the method repository instead of GitHub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("--force", action="store_true")
    i = sub.add_parser("ignore")
    i.add_argument("version")
    sub.add_parser("plan")
    sub.add_parser("apply")
    m = sub.add_parser("mark")
    m.add_argument("paths", nargs="+")
    sub.add_parser("adopt")
    args = ap.parse_args(argv)
    return {"check": cmd_check, "ignore": cmd_ignore, "plan": cmd_plan, "apply": cmd_apply,
            "mark": cmd_mark, "adopt": cmd_adopt}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
