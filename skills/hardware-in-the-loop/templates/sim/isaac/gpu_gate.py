#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 gpu_gate.py - the ONE gate in front of every GPU job (Isaac Sim, CUDA sweeps, ML): fails closed.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python -I sim/isaac/gpu_gate.py [--min-free-mb 16000]
 Paths: command line

 The gate passes only when ALL of these hold:
   1. HIL_GPU=1 is set for this job (an explicit opt-in per shell or job; never set it globally; CI sets HIL_GPU=0);
   2. nvidia-smi answers within 10 s;
   3. no foreign compute JOB holds the GPU. On Windows (WDDM) every desktop app with a GPU context is listed as a
      "compute app", so only executables that run compute work count: names starting with python, kit or isaac,
      plus any name in HIL_GPU_WATCH (comma separated, e.g. "ollama.exe,blender.exe"); a garbled row counts as
      foreign. --allow-shared skips this check for an owner-approved co-run;
   4. at least --min-free-mb of VRAM is free.
 It never kills, pauses or renices anything. When it says no: read the reason, do CPU work, retry later (10 min or
 more), and report "blocked" with the reason if it stays closed. Model and tool caches are pointed at
 HIL_CACHE_DIR (or <repo>/.tmp/cache) so a GPU job never fills the user profile.
 Exit: 0 open, 3 closed (the reason is printed), 2 bad arguments.
=======================================================================================================================
"""
import argparse
import os
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _smi(args):
    """Run nvidia-smi with a timeout; returns (ok, stdout lines or reason)."""
    try:
        out = subprocess.run(["nvidia-smi"] + args, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"nvidia-smi did not answer: {exc}"
    if out.returncode != 0:
        return False, f"nvidia-smi exit {out.returncode}: {out.stderr.strip()[:200]}"
    return True, [l.strip() for l in out.stdout.splitlines() if l.strip()]


def gpu_allowed(min_free_mb=16000, allow_shared=False):
    """(ok, reason). Never raises; every unexpected condition is a closed gate with its reason."""
    if os.environ.get("HIL_GPU", "") != "1":
        return False, "HIL_GPU is not 1 (opt in for this job only: set HIL_GPU=1 in the job's environment)"
    ok, rows = _smi(["--query-gpu=memory.free", "--format=csv,noheader,nounits"])
    if not ok:
        return False, rows
    try:
        free = int(rows[0].split(",")[0])
    except (IndexError, ValueError):
        return False, f"cannot read free VRAM from {rows[:1]}"
    if not allow_shared:
        ok, apps = _smi(["--query-compute-apps=pid,process_name", "--format=csv,noheader"])
        if not ok:
            return False, apps
        watch = {x.strip().lower() for x in os.environ.get("HIL_GPU_WATCH", "").split(",") if x.strip()}
        foreign = []
        for row in apps:
            parts = [p.strip() for p in row.split(",")]
            name = os.path.basename(parts[1].replace("\\", "/")).lower() if len(parts) >= 2 else ""
            if not name or name.startswith(("python", "kit", "isaac")) or name in watch:
                foreign.append(row)
        if foreign:
            return False, "GPU busy with other compute processes: " + "; ".join(foreign[:4])
    if free < min_free_mb:
        return False, f"only {free} MB VRAM free, need {min_free_mb}"
    return True, f"{free} MB VRAM free"


def require_gpu(min_free_mb=16000, allow_shared=False):
    """Exit 3 with the reason when the gate is closed; otherwise point the caches into the repo and return."""
    ok, why = gpu_allowed(min_free_mb, allow_shared)
    if not ok:
        print(f"[gpu-gate] closed: {why}")
        sys.exit(3)
    cache = os.environ.get("HIL_CACHE_DIR") or os.path.join(REPO, ".tmp", "cache")
    for var, sub in (("HF_HOME", "hf"), ("TORCH_HOME", "torch"), ("XDG_CACHE_HOME", "xdg"), ("WARP_CACHE_PATH", "warp")):
        os.environ.setdefault(var, os.path.join(cache, sub))
        os.makedirs(os.environ[var], exist_ok=True)
    print(f"[gpu-gate] open: {why}")


def main():
    ap = argparse.ArgumentParser(description="fail-closed GPU gate")
    ap.add_argument("--min-free-mb", type=int, default=16000)
    ap.add_argument("--allow-shared", action="store_true", help="owner-approved co-run: skip the foreign-process check")
    a = ap.parse_args()
    ok, why = gpu_allowed(a.min_free_mb, a.allow_shared)
    print(f"[gpu-gate] {'open' if ok else 'closed'}: {why}")
    return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
