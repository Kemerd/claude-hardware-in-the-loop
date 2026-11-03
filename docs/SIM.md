# Simulation: three tiers, one source of truth

Status: **design and roadmap**. The native twin pattern is the part the method relies on daily (see
[FIRMWARE.md](FIRMWARE.md) section 4); the Isaac Sim and Unreal tiers are described here with their file layouts and the
prompts an agent would use to build them, but this repository does not ship an implementation of either.

The one rule across all tiers: **everything reads the CAD exports**. Geometry, masses, CG, inertia and joints come from
`cad/<asm>/parts.json`, `articulation.json` and the STEP / STL files. No tier carries hand-copied numbers; a CAD change
regenerates every tier's inputs.

| Tier | Runs | Purpose | Cost |
|---|---|---|---|
| 1. Native twin (C++) | CI, every change, CPU only | firmware in the loop, seed sweeps, determinism, regression guards | seconds to minutes |
| 2. Isaac Sim | on demand, GPU, one job at a time | contact-rich physics, sensor rendering for perception, synthetic data | minutes to hours, a big GPU |
| 3. Unreal Engine visual twin | on demand, GPU, never while the owner is at the machine | 1:1 renders, camera / depth sensor simulation, demo videos | hours, a big GPU, editor licence terms |

## 1. Native twin (tier 1)

Layout:

```
sim/twin/
  CMakeLists.txt             builds twin + links firmware/<target>/components/core (the real control code)
  src/physics/               rigid body / circuit / thermal model
  src/devices/               sensor and actuator models behind the hosted HAL
  src/station/               the real station protocol (UDP or serial framing) the scenarios talk to
  tools/cad_to_twin.py       parts.json + articulation.json -> out/params.txt (mass, CG, inertia, joints)
  out/params.txt             generated, committed with the CAD change that moved it
missions/*.json              scenarios: initial state, disturbances, commands, acceptance numbers
windows/ci/twin.ps1          CI runner: build, scenarios x seeds, determinism hash, regression hash
```

Prompt an agent would use to start it (single-session step or a lane brief):

> Build `sim/twin/`: a C++17 CMake project that links `firmware/<target>/components/core` unchanged and replaces the
> HAL (`hal_*.h`) with host implementations backed by device models (IMU noise and bias from the datasheet table in
> `docs/datasheets/<imu>.md`, motor constants from `docs/bench/BENCH_REGISTER.md` rows B-MOT-*). Generate
> `out/params.txt` from `cad/<asm>/parts.json` with `tools/cad_to_twin.py` (mass, CG, inertia about the CG, joints from
> `articulation.json`). Drive it only through the real station protocol. Scenarios are `missions/*.json`; each run
> prints one summary line `[twin] <scenario> seed <n>: PASS|FAIL <metric>=<value>`. Add `windows/ci/twin.ps1` with suites
> `twin_build`, `twin_scenarios` (seeds 1..5), `twin_determinism` (same seed twice, SHA-256 of the trace must match).
> Acceptance: all scenarios pass at seeds 1..5, determinism hash equal, no audit findings, portability clean.

## 2. Isaac Sim (tier 2, roadmap)

Layout:

```
sim/isaac/
  build_usd.py               parts.json + articulation.json + STL -> assets/<asm>.usd (meshes, masses, joints, materials)
  scenes/*.py                scene scripts (world, lights, sensors), one per scenario family
  scripts/gpu_gate.py        the ONE entry point for GPU work: takes the gpu slot, sets the EULA variable, runs a scene
                             headless with a timeout, writes outputs to a private folder, releases the slot
  out/                       ignored; results digested into docs/data/isaac/<date>/
```

Rules: GPU only through `gpu_gate.py` (slot kind `gpu`, capacity 1: one heavy GPU job at a time); the EULA acceptance
variable is set by the gate, never in a user profile; outputs (renders, datasets) are private and never committed;
Python for Isaac runs in its own venv on a tools drive; CI never touches the GPU (CI only runs CPU tests of
`build_usd.py`, for example that every part in `parts.json` became a prim with the right mass).

Prompt:

> Write `sim/isaac/build_usd.py` that converts `cad/<asm>/parts.json`, `articulation.json` and `stl/*.stl` into
> `sim/isaac/assets/<asm>.usd`: one Xform + Mesh per part with `MassAPI` (mass, centre of mass from the JSON), joints
> from `articulation.json` with limits, materials from a shared table. No primitive stand-ins: every prim is the CAD
> mesh. Add a CPU-only test that opens the USD with `pxr` and checks part count, total mass and CG against `parts.json`
> within 0.1 %. Then write `scripts/gpu_gate.py` that takes `slot.sh take gpu <agent> <job> <GB>`, runs a scene
> headless with a timeout, writes to `.tmp/isaac/<run>/`, digests the result into one CSV row in
> `docs/data/isaac/<date>/`, and releases the slot in a finally block.

## 3. Unreal Engine visual twin (tier 3, roadmap)

Layout:

```
sim/unreal/
  <Project>.uproject, Source/, Config/   the project (Content/ and caches are ignored; assets regenerate)
  tools/cad_to_unreal.py                 STEP / STL + parts.json -> import manifest (static meshes, materials, sockets)
  tools/mcp/                             an MCP server for the editor, so an agent can place actors, set cameras,
                                         start render queue jobs and read results through tools instead of the GUI
  Sequences/                             camera paths for demo videos
```

Rules: one Unreal instance at a time (slot kind `unreal`, capacity 1; building with UBT is kind `ubt`, also 1); no
editor, commandlet, play-in-editor or render-queue launch while the owner is at the machine (compiling is fine);
never touch an editor the owner opened; stills first, the owner approves, then the video; videos go to the archive
(never deleted), small stills to `docs/data/unreal/<date>/`.

Prompt:

> Write `sim/unreal/tools/cad_to_unreal.py`: read `cad/<asm>/parts.json` and the STEP files, write an import manifest
> (one static mesh per part, material slot from the material name, pivot at the part frame, sockets at the joint
> origins of `articulation.json`). Then, using the editor MCP tools, import the manifest into `/Game/Twin/<asm>`,
> assemble a Blueprint whose components follow the CAD frame 1:1 (check: the component bounding boxes match the STL
> bounds within 0.1 mm), place it in a neutral studio level, and render three stills (iso, front, top) to
> `.tmp/unreal/<date>/`. Take `slot.sh take unreal ...` first, stop if the owner is at the machine, release in a finally.

## 4. Sensor simulation (roadmap, tiers 2 and 3)

Cameras and depth sensors rendered from the same CAD, with the intrinsics from the sensor datasheet table, feeding the
same perception code that runs on the target. A perception result is reported against a held-out split, never against
the frames it was tuned on, and synthetic-to-real gaps are measured and stated, not assumed away.
