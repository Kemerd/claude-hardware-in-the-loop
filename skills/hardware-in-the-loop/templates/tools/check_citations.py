#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 check_citations.py - the citation gate: every [REF-nnn] tag in code has a verified row in CITATION.md.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python -I tools/check_citations.py [--root DIR] [--json OUT]

 Why: a model with a wrong or invented source looks exactly as finished as one with a right source. Tags in code tie
 each equation to a row; the row says what was implemented and how the source was checked. The gate fails on:
   missing     a tag in code with no row in CITATION.md                                   (gating)
   unverified  a row that code uses whose Verified column does not start with "yes"        (gating)
   malformed   a table row whose id is not REF-nnn or that lacks a DOI / URL / ISBN / doc   (gating)
   duplicate   the same id on two rows                                                     (gating)
   unused      a row nothing tags (reported, never gating: background reading is fine)
 Scanned: source and script files under the repository (C, C++, CUDA, C#, Python, PowerShell, shell, CMake, params,
 scenario and Markdown files outside CITATION.md), skipping .git, .tmp, .archive, build and engine cache folders.
 Output: one summary line "[citations] N tags in F files, R rows, missing M, unverified U, malformed X, unused K".
 Exit: 0 clean, 1 a gating finding, 2 bad arguments or no CITATION.md while tags exist.
=======================================================================================================================
"""
import argparse
import json
import os
import re
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT_DEFAULT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TAG_RE = re.compile(r"\[(REF-\d{3})\]")
ROW_RE = re.compile(r"^\|\s*(REF-\d{3}|[^|]*?)\s*\|(.*)\|\s*$")
EXTS = {".c", ".h", ".cpp", ".hpp", ".cu", ".cs", ".py", ".ps1", ".psm1", ".sh", ".txt", ".scn", ".md", ".ini", ".json"}
SKIP_DIRS = {".git", ".tmp", ".archive", "archive", "build", "Binaries", "Intermediate", "Saved", "DerivedDataCache",
             "node_modules", "__pycache__", ".venv", "venv", "harness", ".claude"}


def scan_tags(root):
    """{id: [(file, line)]} over every scanned file, and the number of files that carry a tag."""
    tags, files = {}, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() not in EXTS or name == "CITATION.md":
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    hit = False
                    for i, line in enumerate(fh, 1):
                        for m in TAG_RE.finditer(line):
                            tags.setdefault(m.group(1), []).append((rel, i))
                            hit = True
                    files += hit
            except OSError as exc:
                print(f"[citations] cannot read {rel}: {exc}", file=sys.stderr)
    return tags, files


def read_rows(path):
    """[(id, cells, line)] from the markdown table rows of CITATION.md whose first cell looks like an id."""
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            if not line.startswith("|") or line.startswith("|---") or line.startswith("| Id"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if cells and cells[0].upper().startswith("REF"):
                rows.append((cells[0], cells, i))
    return rows


def main():
    ap = argparse.ArgumentParser(description="citation gate")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    ap.add_argument("--json", default="", help="write the findings as JSON here")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    tags, nfiles = scan_tags(root)
    cit = os.path.join(root, "CITATION.md")
    if not os.path.isfile(cit):
        print(f"[citations] no CITATION.md at {root}")
        return 2 if tags else 0

    rows = read_rows(cit)
    findings = {"missing": [], "unverified": [], "malformed": [], "duplicate": [], "unused": []}
    by_id = {}
    for rid, cells, line in rows:
        if not re.fullmatch(r"REF-\d{3}", rid) or len(cells) < 5:
            findings["malformed"].append(f"CITATION.md:{line} {rid}: expected | Id | Reference | DOI/URL | Used for | Verified |")
            continue
        link = cells[2]
        if not re.search(r"(https?://|doi\.org|ISBN|JESD|ISO |IEC |MIL-|IEEE Std|NOAA)", link):
            findings["malformed"].append(f"CITATION.md:{line} {rid}: no DOI / URL / ISBN / document number")
        if rid in by_id:
            findings["duplicate"].append(f"CITATION.md:{line} {rid} also at line {by_id[rid][1]}")
        by_id[rid] = (cells, line)

    for rid, places in sorted(tags.items()):
        where = ", ".join(f"{f}:{l}" for f, l in places[:3]) + (" ..." if len(places) > 3 else "")
        if rid not in by_id:
            findings["missing"].append(f"{rid} used at {where} has no row")
            continue
        verified = by_id[rid][0][4].lower()
        if not verified.startswith("yes"):
            findings["unverified"].append(f"{rid} used at {where} is not verified ('{by_id[rid][0][4]}')")
    for rid in sorted(by_id):
        if rid not in tags:
            findings["unused"].append(f"{rid} (CITATION.md:{by_id[rid][1]}) is tagged nowhere")

    for k in ("missing", "unverified", "malformed", "duplicate"):
        for f in findings[k]:
            print(f"  {k}: {f}")
    gating = sum(len(findings[k]) for k in ("missing", "unverified", "malformed", "duplicate"))
    ntags = sum(len(v) for v in tags.values())
    print(f"[citations] {ntags} tags in {nfiles} files, {len(by_id)} rows, missing {len(findings['missing'])}, "
          f"unverified {len(findings['unverified'])}, malformed {len(findings['malformed'])}, "
          f"unused {len(findings['unused'])}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump({"tags": ntags, "rows": len(by_id), "findings": findings, "gating": gating}, fh, indent=2)
    return 1 if gating else 0


if __name__ == "__main__":
    sys.exit(main())
