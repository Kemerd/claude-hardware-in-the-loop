# Simulation: one C twin, three hosts

The firmware's control code, compiled unchanged, runs against models of the hardware it drives. The models are
plain C behind one C ABI (`hil_twin_api`), so the same library runs in CI, in Unreal Engine and in Isaac Sim.
Everything here ships in the skeleton (`skills/hardware-in-the-loop/templates/sim/`), built and tested; the
procedure an agent follows is `skills/hardware-in-the-loop/references/twin-setup.md`.

It is a starting point. The example plant is a single robot joint (motor, gearbox, link under gravity) because that
exercises every model class at once; a project replaces the plant and keeps the shape.

| Tier | Runs | What integrates the physics | Use it for | Cost |
|---|---|---|---|---|
| 1. Native twin (C) | CI, every change, CPU only | the twin itself (fixed 1 ms step) | firmware in the loop, fault scenarios, seed sweeps, determinism, Monte Carlo margins | seconds |
| 2. Unreal Engine 5.8.4+ (recommended host) | on demand, one instance at a time | the twin (Kinematic, bit-identical to tier 1) or Chaos (ChaosTorque: Chaos owns the link, the twin returns the actuator torque each async physics step) | seeing what the firmware does, contact with the world, rendered sensors, demos, videos | minutes, a GPU |
| 3. Isaac Sim | on demand, behind a fail-closed GPU gate | PhysX, one twin step per physics step | teams that already use Isaac, its robot / sensor libraries, synthetic data | minutes, an RTX GPU |

The one rule across tiers: **everything reads the same params and the same CAD exports**. Masses, CoM and inertia
come from `cad/<asm>/parts.json` through `sim/twin/tools/cad_to_twin.py`; the Unreal and Isaac hosts read the joint
properties back from the twin (`hil_twin_joint_props`) instead of carrying their own numbers.

## 1. The native twin

```
sim/twin/
  include/hil_twin/        models (twin_math, rng, rigid_body, actuators, thermal, battery, sensors, params, trace)
                           and the C ABI hil_twin_api.h
  src/                     the models, plant.c (the example rig + the hosted HAL), twin_api.c, twin_main.c (runner)
  params/                  example_rig.txt (brushed DC), example_rig_bldc.txt (PMSM + FOC drive), the CAD-export include
  scenarios/               *.scn timelines with acceptance lines; MATRIX.txt (what CI runs, over how many seeds)
  gpu/                     the thermal Monte Carlo: one kernel body for CPU and CUDA, embedded PTX, driver-API loader
  tools/                   cad_to_twin.py, twin_ctypes.py (binding, scenario player, telemetry decoder, judge)
  tests/                   model suite (closed forms), firmware-core suite, ABI suite, Python ABI suite
firmware/core/             the control code the twin links (C11, no vendor headers, hardware only through hal.h)
windows/ci/twin.ps1        twin_build, twin_tests, twin_scenarios, twin_determinism, twin_abi
```

**Models** (each with its source in `CITATION.md` and a closed-form test): 6-DOF rigid body with RK4 and a penalty
ground contact with regularised Coulomb friction; averaged brushed DC motor with temperature-dependent resistance;
PMSM / BLDC in the dq frame behind a FOC drive in torque mode (PI current loops, SVPWM voltage limit, sub-stepped);
geared revolute joint with reflected inertia, friction and hard stops; lumped thermal RC network (backward Euler,
any number of nodes); 1RC Thevenin battery with an OCV table; IMU (white noise, bias random walk, per-unit scale and
bias, lever arm, quantisation, saturation), ISA barometer, rangefinder, NTC divider, ADC. Random numbers come from
one xoshiro256** stream per noise source, so adding a sensor never shifts another's noise.

**Rules the twin enforces**: the firmware sees only raw values through the HAL and acts only through it; the
firmware's board constants (`fw.*`) are kept apart from the plant truth, so a wrong divider in firmware shows;
every params key must be read (a typo fails `hil_twin_create`); scenarios drive the firmware through its own link
protocol and are judged from its telemetry plus plant truth, never from firmware internals; the same seed gives a
byte-identical trace (no wall clock, no fast-math, no FMA contraction).

**Scenarios** in the example: reach (homing, three moves under gravity), overload (five minutes holding a payload:
the firmware must derate on its winding-temperature observer and stay under class B), hot soak at 75 degC, encoder
failure mid-move, a stall beyond the actuator's capacity, each on the brushed and the brushless rig. The first run
of the overload scenario found a real design error: derating on the motor-case NTC alone let the winding reach
132 degC; the firmware now estimates the winding temperature and holds it to 83 degC.

**Monte Carlo** (`gpu/`): per-unit tolerances on the thermal path and the load. In the example the nominal peak
winding temperature is 98 degC, comfortably under 130, yet 0.6 % of 100,000 simulated units exceed it, which is
the kind of answer a single nominal run cannot give. The CUDA path (RTX 5090: 100k units in about a second) is
bit-identical to the CPU reference; it runs only with `HIL_GPU=1` and falls back to the CPU on any driver error.

## 2. Unreal Engine (recommended host)

```
sim/unreal/HilTwin/        runtime plugin: FHilTwinApi (private DLL copy, bind by name, ABI check),
                           AHilTwinJointActor (Kinematic / ChaosTorque), the scenario player, HilTwin.* console
                           commands, automation tests HilTwin.Abi and HilTwin.KinematicParity
sim/unreal/host/           a minimal C++ host project (async physics at 1 ms); link_plugin.ps1 adds the plugin
```

Kinematic mode replays the twin at its own step; the automation test plays a scenario through the plugin and
compares the trace hash with the native runner's: they are identical (built and run headless with UE 5.8.4).
ChaosTorque mode gives the link to Chaos (mass, CoM, axis inertia plus reflected rotor inertia from the twin) and
applies the twin's actuator torque each async physics step; it is compiled and its acceptance procedure is in
`twin-setup.md` (it is judged by scenario verdicts, not hashes, because a different integrator runs). Engines start
only when the owner agreed, or overnight; one instance at a time, behind the `unreal` / `ubt` slots.

What Unreal is for here: watching the real firmware drive virtual actuators, feeding it rendered sensor data,
contact with a scene, and video. The physics that decides a verdict stays in the twin unless ChaosTorque is chosen
on purpose; a simulation's credibility comes from its models' sources and from bench comparison, not from the
engine.

## 3. Isaac Sim

`sim/isaac/gpu_gate.py` (opt-in per job, nvidia-smi answers, no foreign compute job, enough free VRAM; fails
closed), `rig_usd.py` (the rig as a USD articulation authored from the twin's own joint properties, reflected
inertia as the PhysX joint armature; a CPU round-trip check with plain `usd-core`), `run_twin_isaac.py` (Isaac Sim
6.x core API with a 5.x fallback; PhysX integrates, the twin runs once per physics pre-step; the verdict uses the
scenario's own acceptance lines).

## 4. What a twin does not prove

A twin shows that the firmware does what the models say the hardware will let it do. It does not show that the
models describe this hardware: that takes a bench. Every constant that decides a verdict has a BENCH register row,
and when hardware exists the same scenario runs on the bench and in the twin and the report shows both. Until then
the results are labelled as model results, with their sources.
