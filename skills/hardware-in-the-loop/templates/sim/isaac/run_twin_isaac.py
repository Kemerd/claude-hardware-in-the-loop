#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 run_twin_isaac.py - Isaac Sim hosts the twin: PhysX integrates the rig, the twin's firmware + actuator + electrical
                     + thermal models run every physics step through hil_twin_step_external.

 Interpreter: Isaac Sim's Python (the pip "isaacsim" package in its own venv on a tools drive, or the launcher's
              python.bat). Written against the Isaac Sim 6.x core API (isaacsim.core.simulation_manager,
              isaacsim.core.experimental.prims); a 5.x install falls back to isaacsim.core.prims.SingleArticulation.
 Run: <isaac python> sim/isaac/run_twin_isaac.py --dll <hil_twin_api> --params <params.txt> --scenario <x.scn>
      [--seed 1] [--headless] [--out <repo>/.tmp/isaac/<run>] [--min-free-mb 16000] [--allow-shared-gpu]
 Paths: command line

 What Python does here: build the stage, read the joint state from PhysX, hand it to the C twin, write the torque
 back. Nothing physical is computed in Python. Per physics step (PHYSICS_PRE_STEP callback, one twin step each):
     scenario events due -> q, qd from PhysX (AXIS_SIGN) -> hil_twin_step_external -> joint effort for this step
 Output: <out>/isaac_result.json (metrics, the scenario's checks, the trace hash) and one line
     [twin-isaac] <scenario> seed <n>: PASS|FAIL <metric>=<value> ...
 The trace hash differs from the native runner's on purpose (PhysX integrates, not the twin); the verdict is the
 scenario's own accept lines, judged with the same metric names.
 Exit: 0 PASS, 1 FAIL, 2 bad arguments, 3 the GPU gate is closed, 4 Isaac failed.
=======================================================================================================================
"""
import argparse
import json
import os
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "twin", "tools"))
from gpu_gate import require_gpu  # noqa: E402
from rig_usd import AXIS_SIGN, RIG, build_rig  # noqa: E402
from twin_ctypes import Judge, Scenario, Twin, load_library  # noqa: E402


class ArticulationAdapter:
    """One-DOF view over the 6.x experimental Articulation or the 5.x SingleArticulation."""

    def __init__(self, path):
        self.kind = None
        try:
            from isaacsim.core.experimental.prims import Articulation  # 6.x

            self.a = Articulation(path)
            self.kind = "experimental"
        except ImportError:
            from isaacsim.core.prims import SingleArticulation  # 5.x

            self.a = SingleArticulation(path)
            self.a.initialize()
            self.kind = "single"

    def state(self):
        """(q, qd) of DOF 0 in PhysX's sign."""
        if self.kind == "experimental":
            q = self.a.get_dof_positions().numpy()[0][0]
            qd = self.a.get_dof_velocities().numpy()[0][0]
        else:
            q = self.a.get_joint_positions()[0]
            qd = self.a.get_joint_velocities()[0]
        return float(q), float(qd)

    def set_position(self, q):
        """Teleport DOF 0 (start-up only)."""
        if self.kind == "experimental":
            self.a.set_dof_positions([[q]])
        else:
            self.a.set_joint_positions([q])

    def set_effort(self, tau):
        """Effort on DOF 0 for the coming step (must be written every step)."""
        if self.kind == "experimental":
            self.a.set_dof_efforts([[tau]])
        else:
            self.a.set_joint_efforts([tau])


def main():
    ap = argparse.ArgumentParser(description="Isaac Sim hosts the twin")
    ap.add_argument("--dll", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--out", default=os.path.join(REPO, ".tmp", "isaac", time.strftime("%Y%m%d_%H%M%S")))
    ap.add_argument("--min-free-mb", type=int, default=16000)
    ap.add_argument("--allow-shared-gpu", action="store_true")
    a = ap.parse_args()

    # ---- outputs never under the user profile; the gate before Kit starts (a closed gate costs nothing)
    home = os.path.expanduser("~")
    if os.path.abspath(a.out).lower().startswith(os.path.abspath(home).lower()):
        print(f"[twin-isaac] refusing --out under the user profile: {a.out}")
        return 2
    os.makedirs(a.out, exist_ok=True)
    require_gpu(a.min_free_mb, a.allow_shared_gpu)

    # ---- the twin first: a bad params file fails in a second, not after Kit has started
    sc = Scenario(a.scenario)
    tw = Twin(None, a.params, seed=a.seed, trace_path=os.path.join(a.out, f"{sc.name}_s{a.seed}.csv"), lib=load_library(a.dll))
    props, dt, q0 = tw.joint_props(0), tw.dt, tw.joint(0)[0]

    from isaacsim import SimulationApp  # noqa: E402 - must be created before any omni import

    app = SimulationApp({"headless": a.headless})
    rc = 4
    try:
        import omni.timeline
        import omni.usd
        from isaacsim.core.simulation_manager import SimulationEvent, SimulationManager

        stage = omni.usd.get_context().get_stage()
        build_rig(stage, props, dt, q0)
        SimulationManager.set_physics_dt(dt)
        omni.timeline.get_timeline_interface().play()
        app.update()
        art = ArticulationAdapter(RIG)
        art.set_position(AXIS_SIGN * q0)

        judge = Judge()
        st = {"k": 0, "err": None, "done": False}
        n_steps = int(round(sc.duration / dt))

        def on_pre_step(step_dt, context):
            """One twin step per physics step: events, state in, torque out."""
            if st["done"] or st["err"]:
                return
            try:
                sc.apply_due(tw, st["k"] * dt)
                q, qd = art.state()
                tau = tw.step_external([AXIS_SIGN * q], [AXIS_SIGN * qd])[0]
                art.set_effort(AXIS_SIGN * tau)
                judge.after_step(tw)
                st["k"] += 1
                st["done"] = st["k"] >= n_steps
            except Exception as exc:  # noqa: BLE001 - reported and ends the run
                st["err"] = f"{type(exc).__name__}: {exc}"

        cb = SimulationManager.register_callback(on_pre_step, event=SimulationEvent.PHYSICS_PRE_STEP)
        t0 = time.time()
        while not st["done"] and not st["err"] and app.is_running():
            app.update()
        SimulationManager.deregister_callback(cb)
        if st["err"]:
            print(f"[twin-isaac] run broke at step {st['k']}: {st['err']}")
        else:
            passed, rows = judge.finish(tw, sc)
            res = {"scenario": sc.name, "seed": a.seed, "steps": st["k"], "wall_s": round(time.time() - t0, 2),
                   "trace_fnv1a64": f"{tw.trace_hash:016x}", "metrics": judge.m, "passed": passed,
                   "checks": [{"metric": r[0], "op": r[1], "limit": r[2], "value": r[3], "pass": r[4]} for r in rows]}
            with open(os.path.join(a.out, "isaac_result.json"), "w", encoding="utf-8") as fh:
                json.dump(res, fh, indent=2)
            line = " ".join(f"{r[0]}={r[3]:.6g}{'' if r[4] else '(!)'}" for r in rows)
            print(f"[twin-isaac] {sc.name} seed {a.seed}: {'PASS' if passed else 'FAIL'} {line}")
            rc = 0 if passed else 1
    finally:
        tw.close()
        app.close()
    return rc


if __name__ == "__main__":
    sys.exit(main())
