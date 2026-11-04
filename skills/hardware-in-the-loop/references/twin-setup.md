# The digital twin: set it up, make it the product's, host it in Unreal or Isaac Sim

This is the procedure an agent follows to give a project a C / C++ digital twin that runs the REAL firmware control
code, judged by scenarios over seeds, and hosted, when wanted, in Unreal Engine (recommended) or Isaac Sim. Read
`docs/SIM.md` of the method repository for the why (the docs/ folder is not copied by the scaffold; read it in
the repository the skill came from); this page is the how.

Shell note: Claude Code runs PowerShell or Git Bash, not cmd. Commands below are PowerShell; set environment
variables with `$env:NAME = 'value'` (in PowerShell, `set NAME=value` does NOT set an environment variable) and run
a quoted executable with `& "<path>" args`.

## 0. What the skeleton already gives you (all of it built and tested)

| Piece | Path | What it is |
|---|---|---|
| Model library | `sim/twin/include/hil_twin/*.h`, `src/*.c` | C11, no heap in the loop: 6-DOF rigid body (RK4, penalty ground contact with regularised Coulomb friction), brushed DC motor, PMSM / BLDC in dq with a FOC drive in torque mode, geared revolute joint with hard stops, lumped thermal RC network (backward Euler), 1RC Thevenin battery, IMU / barometer / rangefinder / NTC + ADC sensor models with per-unit tolerances, deterministic random streams, params loader, CSV trace with a hash |
| Firmware core | `firmware/core/` | the portable C11 control code of the example (one robot joint: homing, cascaded position / velocity / current loop, winding-temperature observer, thermal derate, battery / encoder / stall faults) behind a function-pointer HAL, plus the framed link codec |
| Example plant | `sim/twin/src/plant.c` | the actuator rig wired to the firmware through the hosted HAL; brushed or brushless from the params |
| C ABI | `sim/twin/include/hil_twin/hil_twin_api.h` | the one interface every host uses: native stepping, external (host physics) stepping, link bytes, named values, fault injection, trace hash |
| Runner | `hil_twin` | one scenario at one seed, one verdict line, a trace, a JSON |
| Scenarios | `sim/twin/scenarios/*.scn`, `MATRIX.txt` | timelines of link commands and environment / fault events, acceptance lines; the matrix CI runs |
| CUDA sweep | `sim/twin/gpu/` | a Monte Carlo over per-unit tolerances, CPU reference + GPU through the driver API, bit-identical |
| Tools | `sim/twin/tools/` | `cad_to_twin.py` (CAD export to mass properties), `twin_ctypes.py` (Python binding, scenario player, telemetry decoder, judge) |
| CI | `windows/ci/twin.ps1` | `twin_build`, `twin_tests`, `twin_scenarios`, `twin_determinism`, `twin_abi` |
| Unreal | `sim/unreal/HilTwin`, `sim/unreal/host` | a UE 5.8 runtime plugin (kinematic replay or Chaos torque coupling) and a host project; automation tests incl. hash parity with the native runner |
| Isaac Sim | `sim/isaac/` | `gpu_gate.py`, `rig_usd.py` (USD articulation from the twin's own properties), `run_twin_isaac.py` |

## 1. Tools (ask before installing anything machine-wide)

```
pwsh -File <skill>/scripts/check_twin_tools.ps1
```

It finds, never installs, and prints what is missing with the install hint. Show the owner the report, recommend,
ask. Everything goes outside the user profile where the installer allows it (a tools drive), and caches are pointed
into the repository's `.tmp/` (CI does this; the GPU gate does it for GPU jobs).

### Native twin (always)
CMake 3.20+, a C11 / C++17 compiler (clang recommended on Windows: `winget install LLVM.LLVM`; MSVC and gcc work),
Ninja (optional; Visual Studio ships one), Python 3.9+ for the tools. Build:
```
cmake -S sim/twin -B .tmp/build/twin -G Ninja -DCMAKE_BUILD_TYPE=Release `
      -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ -DCMAKE_RC_COMPILER=llvm-rc   # what CI uses on Windows
cmake --build .tmp/build/twin
pwsh -File windows/ci.ps1 -Only twin          # the gate; must be GREEN before any other tier
```
Linux / macOS: the same cmake lines with gcc or clang (drop the RC compiler); the binaries have no `.exe` and the
library is `hil_twin_api.so` / `.dylib`. CI's `windows/ci.ps1` runs under pwsh on Linux too, but it is tested on
Windows; on Linux run the cmake lines and the binaries directly if a path problem shows. Below, `<build>` means
`.tmp/build/twin` (or its `Release/` subfolder with a multi-config generator such as Visual Studio); always take the
DLL, `hil_twin` and the expected hashes from the SAME `<build>`.

### Unreal Engine 5.8.4 or newer (the recommended host)
1. Install the **Epic Games Launcher**, sign in, *Unreal Engine > Library > Engine Versions > +*, pick **5.8.4 or
   newer**, install to a large non-system drive. Options: keep "Editor symbols for debugging" if crashes must be
   debugged (large). Linux: download the Linux binary build from the Epic site, or build from source (link the Epic
   account to GitHub first, then `git clone` the EpicGames/UnrealEngine repository and follow its README).
2. Install **Visual Studio** in the version the engine's release notes name (2022 17.x or 2026) with *Game
   development with C++* and *Desktop development with C++*, plus the Windows SDK the notes name. (Rider works too.)
3. Check the release notes of the exact version for the supported toolchain; when the engine refuses a compiler,
   the build log names the expected one.
4. Re-run `check_twin_tools.ps1`: the Unreal row must be `found` with a version >= 5.8.4 (or set `HIL_UE_ROOT`).

### Isaac Sim (when chosen; Unreal is recommended unless the team knows Isaac or needs its libraries)
1. An RTX GPU with the current NVIDIA driver; check the Isaac Sim release's requirements page (VRAM, driver, OS).
2. A venv on a tools drive with the Python version that Isaac Sim release requires (it changes between majors:
   read the install page), then `pip install "isaacsim[all,extscache]==<version>" --extra-index-url
   https://pypi.nvidia.com`. First launch accepts the EULA (`OMNI_KIT_ACCEPT_EULA=YES` for that job only) and
   compiles shaders for a long time; run it once while the owner watches.
3. Set `HIL_ISAAC_PYTHON` to that venv's python. The harness targets the Isaac Sim 6.x core API and falls back to
   the 5.x one; with another major, read its migration guide and adapt `ArticulationAdapter` in
   `run_twin_isaac.py` (nothing else touches Isaac).

### CUDA (optional)
The twin needs only the NVIDIA **driver** for its GPU path (the PTX is embedded). The CUDA **toolkit** (12.x or
newer, plus MSVC on Windows) is needed only to regenerate the PTX after a kernel change:
`pwsh -File sim/twin/gpu/build_ptx.ps1` from a Developer PowerShell.

## 2. Make the twin the product's (the order matters)

Work in numbered plan steps, each closed by `ci.ps1 -Only twin` (and the other groups it touches) going GREEN.

1. **Run the example as it is.** `<build>/hil_twin.exe sim/twin/params/example_rig.txt sim/twin/scenarios/reach.scn
   --seed 1 --out .tmp/twin` (`hil_twin` is not on PATH). Read the trace. This proves the toolchain before anything is changed.
2. **Write the firmware core first, not the plant.** Move the product's control code into `firmware/core/` (keep it
   there: the twin build reads `firmware/core` by default; if it must live elsewhere, configure the twin with
   `-DHIL_CORE_DIR=<path>`, and add every new `firmware/core/src/*.c` to the `hil_core` library in
   `sim/twin/CMakeLists.txt`): C11, no vendor headers, hardware only through `hal.h`, one
   `*_step()` at a fixed rate. Extend `hal_t` with the product's sensors and actuators (raw values only). Grow
   `firmware/host_tests` with it (`hil_check.h`). See `references/firmware-host.md` for ESP32 / ESP32-P4 / ARM.
3. **Research before modelling.** For each physical effect the scenarios need, follow
   `references/research-citations.md`: datasheet first, then standard, textbook, paper; verify; add the
   `CITATION.md` row; tag the code `[REF-nnn]`.
4. **Replace the plant.** Edit `src/plant.c` / `plant.h` in place (they are listed in `HIL_PLANT_SOURCES` in
   `sim/twin/CMakeLists.txt`; a renamed or added file goes there too), and keep the `tw_plant_create`,
   `tw_plant_step`, `tw_plant_step_external` and `tw_plant_destroy` functions and the fields `twin_api.c` reads.
   Keep `params/example_rig*.txt` and the example scenarios until `tests/test_twin_api.c` is pointed at the
   product's params, or `twin_tests` fails. The FIRST row of `scenarios/MATRIX.txt` is the determinism and
   host-parity row: put the product's nominal scenario there. Reuse the models (`tw_motor`,
   `tw_pmsm`, `tw_joint`, `tw_body`, `tw_thermal`, `tw_battery`, `tw_imu`, `tw_baro`, `tw_range`, `tw_adc`);
   write new models in the same style (a params struct, a state struct, `*_init` that validates, `*_step` that
   refuses non-finite input, a closed-form test in `tests/`). Keep the rules:
   - every number from the params file; the firmware's view (`fw.*`) kept apart from the truth;
   - the firmware sees only raw values through the HAL and acts only through it;
   - one random stream per noise source, seeded from (run seed, stream id);
   - a firmware tick, then actuators, mechanics, electrical, thermal, trace, in that order, every step.
5. **Mass properties from the CAD, never typed.** Export `parts.json` from the CadQuery builder (with
   `inertia_kgmm2` for parts that matter), run `sim/twin/tools/cad_to_twin.py` into `sim/twin/out/*.txt`,
   `include =` it from the params file, and commit it with the CAD change that moved it. For a free body (a robot
   base, a drop test of the enclosure) feed the totals into `tw_body_params`.
6. **More joints or bodies.** The ABI signatures already take arrays (`hil_twin_step_external(q[], qd[], tau[], n)`,
   `hil_twin_joint_count`), but the example is wired for ONE joint. For N joints, in this order:
   - firmware: give `hal_t`'s `encoder_read` / `motor_write` (and the ADC channels) a joint index, make
     `hil_core_board_t` and the tunables per joint, run one cascade per joint in `hil_core_step`;
   - params: prefix the keys per joint (`joint0.*`, `motor0.*`, `fw.joint0.*`), run `cad_to_twin.py` once per joint
     (`--out sim/twin/out/joint<i>_mass.txt`, each with that joint's moving parts and axis);
   - plant and ABI: arrays of joints and motors in `tw_plant`; in `twin_api.c` remove the `n_joints != 1` and
     `i != 0` checks and return the real count from `hil_twin_joint_count`;
   - dynamics: N independent `tw_joint`s are right only for joints that do not carry each other (separate
     axes on a bench). A serial arm has coupled dynamics (mass matrix M(q), Coriolis, gravity of the links
     beyond). Either write a `tw_chain` model (recursive Newton-Euler / composite rigid body after [REF-003], with
     a closed-form two-link test) or let the host integrate the chain (Unreal ChaosTorque with one constraint per
     joint, or Isaac PhysX as an articulation) and judge by scenario verdicts;
   - hosts: one link component and one hinge per joint in `AHilTwinJointActor` (loop over
     `hil_twin_joint_count`), the counts in `HilTwinTests.cpp`, one revolute joint per DOF in `rig_usd.py`;
   - ABI version: adding exports keeps `HIL_TWIN_ABI_VERSION` (hosts bind by name); changing a signature bumps it.
7. **Scenarios.** One file per behaviour that matters: the normal task, each fault the firmware must survive
   (sensor dead, sensor open / short, stall, low battery, link loss), the environment corners from the build spec
   (hot soak at the top of the temperature range, cold start), the overload the derating headroom is for. Each has
   acceptance lines with limits from `REQUIREMENTS.md` or `DECISIONS.md`, never invented. Commands go through the
   link (as a station or app would send them); environment and faults through `set`. Add rows to `MATRIX.txt`. The `stall` / fault scenarios with expected faults are as important as the passing ones.
8. **Seeds.** CI runs each row over its seed count; the nightly chain runs
   `pwsh -File windows/ci.ps1 -Only twin_scenarios -TwinSeeds 40` (overrides every row's count). A result is the distribution, not
   the best seed; a scenario that passes at seed 1 and fails at seed 7 has a real problem.
9. **Determinism.** `twin_determinism` must stay green: no wall-clock time, no uninitialised memory, no iteration
   over hash containers, no fast-math, no FMA contraction. A different hash with the same seed is a bug, found
   before anything else is debugged.
10. **Monte Carlo for margins.** Adapt `gpu/sweep_main.c` (or write a sweep over the twin itself, one handle per
    thread) for the questions a single run cannot answer: what fraction of built units exceed a limit given the
    datasheet tolerances. The CUDA path is optional; the CPU path is the reference.

## 3. Unreal: host the twin (recommended tier)

1. `pwsh -File sim/unreal/link_plugin.ps1` (makes `sim/unreal/host/Plugins/HilTwin` a junction to the plugin).
   Linux: `mkdir -p sim/unreal/host/Plugins && ln -s ../../HilTwin sim/unreal/host/Plugins/HilTwin`, then build with
   `<UE>/Engine/Build/BatchFiles/Linux/Build.sh HilTwinHostEditor Linux Development -Project=...`.
2. Build (take the `ubt` slot first: `eval "$(bash harness/swarm/locks/slot.sh take ubt <you> "UE build" 8)"`):
   ```
   & "<UE>\Engine\Build\BatchFiles\Build.bat" HilTwinHostEditor Win64 Development -Project="<repo>\sim\unreal\host\HilTwinHost.uproject" -WaitMutex
   ```
   It must end with `Result: Succeeded` and no warnings from `HilTwin`.
3. Automation tests, headless and without a GPU (`-nullrhi`), take the `unreal` slot:
   ```
   $env:HIL_TWIN_DLL = '<repo>\<build>\hil_twin_api.dll'
   $env:HIL_TWIN_PARAMS = '<repo>\sim\twin\params\example_rig.txt'
   $env:HIL_TWIN_SCENARIO = '<repo>\sim\twin\scenarios\reach.scn'
   # the expected hash from the SAME build's runner:
   $line = & '<repo>\<build>\hil_twin.exe' $env:HIL_TWIN_PARAMS $env:HIL_TWIN_SCENARIO --seed 1 --no-trace | Select-String 'fnv1a64='
   $env:HIL_TWIN_EXPECT_HASH = ([regex]::Match("$line", 'fnv1a64=([0-9a-f]{16})')).Groups[1].Value
   & "<UE>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "<repo>\sim\unreal\host\HilTwinHost.uproject" -ExecCmds="Automation RunTests HilTwin;Quit" -unattended -nullrhi -nosplash -abslog="<repo>\.tmp\ue\auto.log"
   ```
   Both `HilTwin.Abi` and `HilTwin.KinematicParity` must report `Result={Success}`: Unreal hosting the twin gives
   the native runner's trace hash bit for bit. Record the hash pair in the step's report.
4. A map: open the host project in the editor (only when the owner agreed to an editor launch), create a level,
   place `HilTwinJointActor`, set *Twin Library*, *Params File*, *Scenario File*, *Coupling*. Replace the cube link
   with the CAD mesh (export glTF / FBX from the CAD builder; Interchange imports them; keep +X along the link and
   the pivot at the joint axis). Save the map in the host project's `Content/` (tracked only if small; big assets
   regenerate from the CAD).
5. **Kinematic** coupling (default): the twin integrates; Unreal shows it. Use it for review, demos, rendered
   sensors and videos. **ChaosTorque** coupling: Chaos owns the link (mass, CoM, axis inertia and the reflected
   rotor inertia applied from `hil_twin_joint_props`), the twin returns the actuator torque each async physics
   step. Needs *Project Settings > Physics > Tick Physics Async* with the step equal to the twin's (the host's
   `DefaultEngine.ini` sets 0.001 s). Use it when the link meets the world (contacts, payloads).
   Acceptance for ChaosTorque before relying on it: the reach scenario played in both modes settles on the same
   targets within the scenario's own `final_err_rad` limit, and the hot / overload scenarios give the same verdicts;
   record both in the report. (Kinematic parity is bit-exact; ChaosTorque is a different integrator, so it is judged
   by verdicts, not hashes.)
6. Headless runs of a map (agents, CI-like batches, never while the owner works unless agreed):
   ```
   & "<UE>\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" <host>.uproject /Game/<Map> -game -nullrhi -unattended -HilTwinScenario=<scn> -HilTwinSeed=3 -HilTwinQuitOnFinish -abslog=<repo>\.tmp\ue\run.log
   ```
   The log line `[twin-ue] <scenario> seed <n> done t=... trace fnv1a64=...` closes the run; the trace lands in
   `Saved/HilTwin/`. Console commands for interactive sessions: `HilTwin.Arm 1`, `HilTwin.SetPos 0.5`,
   `HilTwin.Set fault.encoder 1`, `HilTwin.Stop`.
7. Videos and renders: stills first, the owner approves, then the Movie Render Queue job; outputs go to
   `.archive/unreal/<date>/` (never deleted), small stills to `docs/data/unreal/<date>/`.
8. If the engine ships an editor automation / MCP plugin the team uses, enable it in the host project so agents
   drive the editor through tools; the twin plugin does not depend on it.

## 4. Isaac Sim: host the twin

1. CPU check first (no GPU, any Python with `usd-core`):
   `python sim/isaac/rig_usd.py --dll <dll> --params <params> --out .tmp/isaac/rig.usda` prints `OK`.
2. The gate: `HIL_GPU=1` set for this one job, then `python -I sim/isaac/gpu_gate.py` must say `open` (exit 3 =
   closed: report blocked with the reason, do CPU work, retry later; never kill or pause another job).
3. The run (take the `gpu` slot):
   ```
   %HIL_ISAAC_PYTHON% sim/isaac/run_twin_isaac.py --dll <dll> --params <params> --scenario <scn> --seed 1 --headless --out <repo>/.tmp/isaac/<run>
   ```
   PhysX integrates the rig; one twin step per physics step; the verdict line `[twin-isaac] <scenario> seed <n>:
   PASS|FAIL ...` uses the scenario's own acceptance lines. Joint sign: the rig's PhysX joint angle is the negative
   of the twin's q (documented in `rig_usd.py`); a sign error shows as a link that drives away from its target, so
   the reach scenario is the first run on a new rig.
4. Replace the box with the CAD (STL / USD from the CAD builder), keep masses from `hil_twin_joint_props`, keep the
   joint without PhysX drive and limits (the twin owns the torque and the stops).

## 5. Running the sims without hurting the machine (and overnight)

- Every heavy job takes a slot (`harness/swarm/locks/slot.sh take <kind> ...`: `sim` for twin batches, `ubt` for
  Unreal builds, `unreal` for an engine instance, `gpu` for Isaac / CUDA), runs at below-normal priority, has a
  timeout, and is started detached (`harness/swarm/detach.sh <chain.sh> <log>`) with a watcher
  (`harness/swarm/watch.sh <log>`) so the agent is woken on success and failure.
- One engine instance and one GPU job at a time. Engines start only when the owner said so or overnight
  (`DECISIONS.md`, working style).
- Before a night: propose the chain (the 40-seed matrix, the Monte Carlo, the Unreal headless batch), its slots and
  timeouts, and the morning questions (`references/overnight-questions.md`); the night runs on defaults.
- Results: the digest (one CSV / JSON row per run) into `docs/data/twin/<date>/`; raw runs moved from `.tmp/` with
  `python -I tools/archive_tmp.py .tmp/<run> --topic twin`.

## 6. Done means

- `ci.ps1 -Only twin` GREEN on the branch, with the product's plant and firmware core;
- every model with a verified `CITATION.md` row (`ci.ps1 -Only citations`);
- every scenario's limits traceable to `REQUIREMENTS.md` / `DECISIONS.md`;
- the chosen host tier's acceptance recorded (Unreal: both automation tests green and the ChaosTorque verdict
  comparison; Isaac: the reach and one fault scenario PASS behind the gate);
- what the twin cannot show listed as hardware-pending with the bench procedure that will close it.
