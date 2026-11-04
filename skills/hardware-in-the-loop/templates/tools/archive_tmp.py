#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 archive_tmp.py - moves finished results out of .tmp/ into the archive (.archive/, a link to a big disk), with a
                  manifest and a hash check before anything is deleted.

 Interpreter: any Python >= 3.9 (stdlib only). Run:
     python -I tools/archive_tmp.py --status                       what is in .tmp, how big, what the disks have
     python -I tools/archive_tmp.py <.tmp/sub/folder> [...] --topic <name> [--keep-source] [--dry-run]
 Paths: command line (folders to archive); the repository root and .archive resolve from this file

 Layout and rules (PROJECT_STRUCTURE.md):
   - the repository lives on an NVMe / SSD; .archive is a directory junction (Windows: mklink /J .archive D:\archive\
     <project>) or a symlink (Linux / macOS: ln -s /mnt/big/archive/<project> .archive) to a large disk, because a
     complex project produces hundreds of GB of traces, renders, videos and datasets over a few weeks;
   - .tmp is scratch: fast, local, gitignored, and cleaned only by moving results to the archive with this script;
   - a move copies each file, re-reads the copy, compares SHA-256, writes .archive/<topic>/<date>_<name>/MANIFEST.json
     (source path, size, SHA-256, git commit, date), and only then deletes the source (unless --keep-source);
   - nothing is ever deleted from .archive by a script; videos and sim outputs are kept;
   - it refuses to run when .archive is missing, is not a link to another disk, or has less free space than the move
     plus the free-disk floor (HIL_DISK_FREE_MIN_GB, default 50).
 Exit: 0 done, 1 a copy failed its hash check (nothing of that folder was deleted), 2 refused / bad arguments.
=======================================================================================================================
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
TMP = os.path.join(REPO, ".tmp")
ARCHIVE = os.path.join(REPO, ".archive")


def sha256(path):
    """SHA-256 of a file, streamed in 1 MB blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def tree_size(path):
    """Total bytes under a folder (or one file)."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dp, _, fns in os.walk(path):
        for f in fns:
            try:
                total += os.path.getsize(os.path.join(dp, f))
            except OSError:
                pass
    return total


def is_link(path):
    """True for a symlink or a Windows junction."""
    if os.path.islink(path):
        return True
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (AttributeError, OSError):
        return False


def git_commit():
    """HEAD of the repository, or 'unknown'."""
    try:
        out = subprocess.run(["git", "-C", REPO, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def status():
    """Print what .tmp holds and the free space on both disks."""
    print(f"[archive] repo   {REPO}")
    for label, p in (("tmp", TMP), ("archive", ARCHIVE)):
        if not os.path.exists(p):
            print(f"[archive] {label:7s} {p}: missing")
            continue
        free = shutil.disk_usage(os.path.realpath(p)).free / 1e9
        link = " (link -> " + os.path.realpath(p) + ")" if is_link(p) else ""
        print(f"[archive] {label:7s} {p}{link}: {free:.1f} GB free")
    if os.path.isdir(TMP):
        rows = sorted(((tree_size(os.path.join(TMP, d)), d) for d in os.listdir(TMP)), reverse=True)
        for size, d in rows[:30]:
            print(f"  {size / 1e9:9.3f} GB  .tmp/{d}")
    return 0


def archive_one(src, topic, keep, dry):
    """Copy, verify, manifest, then delete one folder or file. Returns 0 or 1."""
    src = os.path.abspath(src)
    try:
        inside = os.path.commonpath([src, TMP]) == TMP
    except ValueError:  # another drive on Windows
        inside = False
    if not os.path.exists(src) or not inside or src == TMP:
        print(f"[archive] refusing {src}: only paths inside .tmp/ are archived")
        return 2
    stamp = datetime.date.today().isoformat()
    dst_root = os.path.join(ARCHIVE, topic, f"{stamp}_{os.path.basename(src)}")
    if os.path.exists(dst_root):
        print(f"[archive] refusing: {dst_root} exists (never overwritten)")
        return 2
    files = [src] if os.path.isfile(src) else [os.path.join(dp, f) for dp, _, fns in os.walk(src) for f in sorted(fns)]
    manifest = {"source": os.path.relpath(src, REPO).replace("\\", "/"), "date": stamp, "commit": git_commit(),
                "topic": topic, "files": []}
    for f in files:
        rel = os.path.relpath(f, src if os.path.isdir(src) else os.path.dirname(src))
        dst = os.path.join(dst_root, rel)
        if dry:
            print(f"  would copy {f} -> {dst}")
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        h_src = sha256(f)
        shutil.copy2(f, dst)
        h_dst = sha256(dst)
        if h_src != h_dst:
            print(f"[archive] HASH MISMATCH {f}: source kept, archive copy left for inspection")
            return 1
        manifest["files"].append({"path": rel.replace("\\", "/"), "bytes": os.path.getsize(f), "sha256": h_src})
    if dry:
        return 0
    with open(os.path.join(dst_root, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    if not keep:
        if os.path.isdir(src):
            shutil.rmtree(src)
        else:
            os.remove(src)
    print(f"[archive] {manifest['source']} -> {dst_root} ({len(manifest['files'])} files, verified"
          f"{'' if keep else ', source removed'})")
    return 0


def main():
    ap = argparse.ArgumentParser(description="move .tmp results into the archive with a verified manifest")
    ap.add_argument("paths", nargs="*", help="folders or files inside .tmp/")
    ap.add_argument("--topic", default="", help="archive folder name (twin, isaac, unreal, renders, bench ...)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--keep-source", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.status or not a.paths:
        return status()
    if not a.topic or any(c in a.topic for c in "/\\:"):
        print("[archive] --topic is required (a plain folder name)")
        return 2

    # ---- the archive must exist, be a link to another disk, and have room for the move plus the floor
    if not os.path.isdir(ARCHIVE):
        print("[archive] .archive is missing: create the link to the big disk first (see this script's header)")
        return 2
    if not is_link(ARCHIVE):
        print("[archive] .archive is a plain folder on the repository's disk; make it a junction / symlink to a big disk")
        return 2
    need = sum(tree_size(p) for p in a.paths)
    floor = float(os.environ.get("HIL_DISK_FREE_MIN_GB", "50")) * 1e9
    free = shutil.disk_usage(os.path.realpath(ARCHIVE)).free
    if free - need < floor:
        print(f"[archive] refusing: {need / 1e9:.2f} GB to move, {free / 1e9:.1f} GB free, floor {floor / 1e9:.0f} GB")
        return 2
    worst = 0
    for p in a.paths:
        worst = max(worst, archive_one(p, a.topic, a.keep_source, a.dry_run))
    return worst


if __name__ == "__main__":
    sys.exit(main())
