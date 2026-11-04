#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 test_twin_ctypes.py - the twin's C ABI driven from Python, the way the Isaac Sim harness and notebooks use it.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python -I sim/twin/tests/test_twin_ctypes.py --dll <hil_twin_api>
              --params <sim/twin/params/example_rig.txt>
 Paths: command line

 Checks: binding and ABI version, refusals turn into exceptions with the library's message, the closed loop homes and
 settles, two instances with one seed agree bit for bit, the external (host physics) mode returns a torque, a fault
 injection reaches the firmware. Prints one "[hil] twin_ctypes: P/T checks passed, F failed" line; exit 1 on failure.
=======================================================================================================================
"""
import argparse
import os
import sys

# ---- Windows consoles: never crash on a character the code page lacks
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# ---- the binding lives next to this suite's sibling folder; resolve it from this file, never from the cwd
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from twin_ctypes import Twin, TwinError, load_library  # noqa: E402

PASSED = 0
FAILED = 0


def check(ok, what):
    """Record one check; print the failures only."""
    global PASSED, FAILED
    if ok:
        PASSED += 1
    else:
        FAILED += 1
        print(f"FAIL {what}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--dll", required=True, help="the hil_twin_api shared library")
    ap.add_argument("--params", required=True, help="a params file (example_rig.txt)")
    a = ap.parse_args()

    lib = load_library(a.dll)
    check(lib.hil_twin_abi_version() == 1, "ABI version 1")

    # ---- a bad params path is an exception carrying the library's message
    try:
        Twin(None, a.params + ".missing", lib=lib)
        check(False, "missing params refused")
    except TwinError as exc:
        check("not found" in str(exc), "missing params message")

    # ---- closed loop: arm, home, move, settle
    with Twin(None, a.params, seed=3, lib=lib) as tw:
        check(abs(tw.dt - 0.001) < 1e-12, "dt is the firmware tick")
        tw.command("arm", 1)
        tw.step(5000)
        check(tw.get("fw.state") == 2.0, "homed and running after 5 s")
        tw.command("set_pos", 0.6)
        tw.step(3000)
        q, _, _ = tw.joint(0)
        check(abs(q - 0.6) < 0.02, f"settled at 0.6 rad (q = {q:.4f})")
        check(len(tw.link_rx()) > 0, "telemetry bytes arrive")
        try:
            tw.command("launch", 1)
            check(False, "unknown command refused")
        except TwinError:
            check(True, "unknown command refused")

        # ---- fault injection reaches the firmware: the encoder dies, the core faults
        tw.set("fault.encoder", 1)
        tw.step(20)
        check(tw.get("fw.state") == 3.0, "encoder fault -> FAULT state")

    # ---- two instances, one seed: identical traces
    hashes = []
    for _ in range(2):
        with Twin(None, a.params, seed=11, lib=lib) as tw:
            tw.command("arm", 1)
            tw.step(3000)
            hashes.append(tw.trace_hash)
    check(hashes[0] == hashes[1] and hashes[0] != 0, "same seed, same trace hash")

    # ---- external mode: host owns the joint; returns a finite actuator torque
    with Twin(None, a.params, seed=1, lib=lib) as tw:
        props = tw.joint_props(0)
        check(props["m_link"] > 0 and props["j_reflected"] > 0, "joint props for the host body")
        tau = tw.step_external([props["q_min"] + 0.2], [0.0])
        check(len(tau) == 1 and tau[0] == tau[0], "external step returns a torque")

    total = PASSED + FAILED
    print(f"[hil] twin_ctypes: {PASSED}/{total} checks passed, {FAILED} failed")
    return 0 if FAILED == 0 and total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
