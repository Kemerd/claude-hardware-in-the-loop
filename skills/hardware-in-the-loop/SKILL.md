---
name: hardware-in-the-loop
description: Sets up and runs the agent-driven hardware-in-the-loop engineering method for a hardware project - generator-first KiCad boards with Freerouting and live part verification, parametric CadQuery CAD with plug-in parts and mass/CG tables, C firmware host-tested against a native C/C++ digital twin (models for rigid bodies, brushed and brushless motors, joints, thermal, batteries, sensors; scenarios over seeds; a C ABI) hosted in Unreal Engine 5.8.4+ or Isaac Sim, CUDA Monte Carlo sweeps, verified research citations, DECISIONS / QUESTIONS records, a PowerShell CI matrix with no-shortcut and portability audits, dev/main branching with merge locks, machine-safety slots, detached jobs, usage-limit handoffs and overnight sessions, in either single-session or swarm mode. Use when the user says "hardware in the loop", "digital twin", "firmware in the loop", "set up a hardware project", "PCB/CAD/firmware swarm", mentions simulating firmware in Unreal or Isaac Sim, asks to scaffold a hardware repo for agents, or wants agents to design boards, enclosures, firmware or a twin with gates.
---

# Hardware in the loop

You are setting up (or running) a hardware project where agents do the engineering and gates prove it. Read this
whole file before acting. This is a starting point the project grows into its own harness, not a finished
product: adapt every template to the project, and say so to the owner when something here does not fit. The method is described in the repository's `docs/` (METHOD, PCB, CAD, FIRMWARE, SIM, SWARM,
TOOLS, LESSONS); this file is the procedure.

## 0. Say the cost first

Before anything else, tell the user, plainly:

> Running this method with a swarm of agents is expensive. On the project it comes from, one swarm day burned 4-5
> million tokens, mostly by waking lane agents that each carried 300-800k tokens of context. The cheaper mode is a single
> session with no subagents that works step by step, runs long machine jobs detached, and resumes from a "Where I
> stopped" section. Which mode do you want? (recommended: single session)

Record the answer as `mode` (`single` or `swarm`). Do not spawn any subagent before the user chose swarm mode.

## 1. Onboarding interview

Run the interview in `references/interview.md` through the question dialog: batches of at most four questions, the
recommended default first in every question. When a question gets no answer, take the default and write
"default taken" next to it. Cover: project goal and definition of done, goals in order, budget, quantities, default
printed material (carbon-fibre nylon / PETG / PLA / machined aluminium), fastening (heat-set inserts / thread-forming /
through-bolts), enclosure (sealed / vented / open), mechanical clearance, PCB fab and assembly vendor, MCU family, main
supply and worst transient, board class and coating, simulation tiers (native twin / Isaac Sim / Unreal) and the GPU,
machine limits (CPU cap default 90 %, RAM kept free for the system default 4 GB, free-disk floor default 50 GB),
archive location, usage tripwire (default 95 %), standing decisions.

Then batch 6 (the build spec: environment, temperature range, IP rating and cooling, standards such as IPC class or
MIL-STD-810H / 461G, wire such as SAE AS22759 "Tefzel", EMC, vibration, derating headroom) and batch 7 (how the
owner likes to work: long sims detached in the background at below-normal priority or only overnight, engines only
when they say, how results reach them, what happens to questions while they are away). Also ask:

- the firmware language, recommending C (say why: the same core compiles for the board and the twin; Python stays
  for tools, CAD and the Isaac harness);
- the twin tiers, recommending native twin + Unreal 5.8.4+ when there is a GPU (Isaac Sim when the team knows it);
- the storage layout, recommending the repo on an NVMe / SSD and `.archive` linked to the biggest disk;
- whether the team already describes boards in a board-as-code tool (SKiDL, atopile, circuit-synth). If it does,
  keep that tool for the netlist and put this method's gates after it (KiCad ERC / DRC, live part checks, the fab
  set); if not, use the Python generator pipeline of the skeleton. Do not argue tools: the gates are the point.

Materials, fastening, enclosure sealing, clearance and the build spec are PROJECT DECISIONS: never pick them
silently; ask, recommend, record.

Write the answers to `answers.json` in the target repository's `.tmp/` (schema in `references/interview.md`).
Every answer and every default taken becomes a numbered row of `DECISIONS.md` (the scaffold writes the first ones).

## 2. Generate the skeleton

```
python -I <skill>/scripts/scaffold.py --answers <repo>/.tmp/answers.json --dest <repo> [--with-examples] [--dry-run]
```

It copies `templates/` (README_AGENT.md, PROJECT_STRUCTURE.md, REQUIREMENTS.md, README.md, CODING_RULES.md,
`.gitignore`, `.gitattributes`, `windows/ci.ps1` + `windows/ci/` (groups `audit` and `pcb`), `tools/` (audit +
portability with an empty tracked baseline and waiver file), `pcb/` (DESIGN_STANDARDS.md, `tools/kicad_env.py`,
`tools/board_pipeline.py`, `tools/distributor_verify.py`), `cad/` (`cad_plugins.py`, `render.py`, `mass_cg_table.py`),
`firmware/host_tests/hil_check.h`, `docs/bench/BENCH_REGISTER.md`, `docs/data/README.md`, `harness/swarm/` (AGENT_RULES,
slot / detach / watch / merge lock / merge-to-dev / promote / WIP backup / handoff hook / status line scripts) and
`.claude/settings.local.json` (status line + PostToolUse hook)), fills the template variables, writes
`harness/swarm/machine.env` and `harness/swarm/PLAN.md` for the chosen mode. It never overwrites an existing file;
compare kept files by hand and merge what is useful. `--with-examples` adds the example board (a 2-layer 3.3 V LDO
breakout that runs every pipeline step) and the example enclosure builder with two plug-ins.

It also writes the digital twin (`sim/twin/`: models, plant, C ABI, runner, scenarios, CUDA sweep, tools), the
firmware core (`firmware/core/`), `sim/unreal/` when the tiers name Unreal, `sim/isaac/` when they name Isaac,
`DECISIONS.md`, `QUESTIONS.md`, `CITATION.md`, `tools/check_citations.py` and `tools/archive_tmp.py`.

Then fill the decision table in `pcb/DESIGN_STANDARDS.md` section 0 from the answers, and make the archive link
(`mklink /J .archive "<archive path>"` on Windows, `ln -s <archive path> .archive` elsewhere; ask first).

## 3. Tools

```
pwsh -File <skill>/scripts/setup_tools.ps1 -ToolsRoot <a folder outside the user profile> [-VerifyOnly]
bash <skill>/scripts/setup_tools.sh --tools-root <dir> [--verify-only]          # Linux
```

KiCad 10 (winget), Java 21 (winget), Freerouting 2.1.0 (pinned SHA-256), OSS CAD Suite 2026-10-24 (pinned SHA-256,
only for FPGA projects), a CadQuery venv. It writes `<ToolsRoot>/hil_tools.env` with `HIL_FREEROUTING_JAR`,
`HIL_JAVA`, `HIL_KICAD_CLI`, `HIL_CQ_PYTHON`. Ask before installing anything machine-wide; run `-VerifyOnly` first and
show the user what is missing. Nothing goes into the user profile.

For the twin tiers: `pwsh -File <skill>/scripts/check_twin_tools.ps1` finds (never installs) CMake, a compiler,
Ninja, Python, Unreal Engine >= 5.8.4, an Isaac Sim Python, the NVIDIA driver and nvcc, with the install hint for
each missing one. Installing Unreal, Visual Studio, Isaac Sim or CUDA is machine-wide: show the report and ask.

## 4. Git: main and dev

In the target repository (ask before running git in a repository that already has history):

```
git init -b main            # when it is not a repository yet
git add -A && git commit -m "Project skeleton (hardware-in-the-loop method)"
git branch dev
git push -u origin main dev # when a remote exists
```

Use the machine's configured git identity. No attribution lines or co-author trailers. Never force-push.
Then run the first CI pass and keep its report as the main reference:

```
pwsh -File windows/ci.ps1 -Only audit
pwsh -File windows/ci.ps1 -Only twin          # the native twin: build, host suites, scenarios, determinism, ABI
```

## 5. Arm the harness

- `.claude/settings.local.json` wires `usage_statusline.py` (status line, tripwire at `HIL_HANDOFF_PCT`) and
  `handoff_hook.sh` (PostToolUse). Prove the hook: `touch harness/swarm/HANDOFF_SELFTEST`, run any tool, expect the
  "hook is live" context, delete the file.
- Start the WIP backup detached: `bash harness/swarm/detach.sh harness/swarm/wip_backup.sh .tmp/wip_backup.log`.
- Check `bash harness/swarm/locks/slot.sh status`.
- The lead session runs from the main checkout (that is where `harness/` lives); edits happen in worktrees.

## 6. Working: single-session mode (default)

Follow `harness/swarm/PLAN.md` (from `references/single-session-plan.md`):

1. One numbered step at a time, each with the gate that proves it.
2. Work in `.claude/worktrees/main-work` on `swarm/main-work`; commit, push, `merge_to_dev.sh` after every green gate.
3. Heavy jobs: `eval "$(bash harness/swarm/locks/slot.sh take <kind> <you> "<job>" <GB>)"`, release after.
4. Long jobs: `bash harness/swarm/detach.sh <chain.sh> <log>` and immediately `bash harness/swarm/watch.sh <log>` as a
   background task; continue with the next step while it runs.
5. Owner questions: batched, recommended default first; defaults taken are written into the plan.
6. Stopping: append "## Where I stopped" (done with SHAs, running jobs with PIDs and logs, exact next step).

## 7. Working: swarm mode

Follow `docs/SWARM.md` and `references/wave-checklist.md`:

1. Wave 1, recon: read-only agents write verified facts with file:line anchors to `harness/swarm/recon_<area>.md`.
2. Wave 2, plan: `PLAN.md` (`references/plan-template.md`) with lanes, one owner per file (cross-lane changes as patch
   files in `harness/swarm/handoff/`), interface additions, acceptance numbers; `briefs.md` with one brief per lane
   (`references/brief-template.md`).
3. Wave 3, lanes in worktrees `swarm/<name>`: Sonnet for mechanical work (ports, codecs, renders, tests, docs), Opus
   for subtle work (control, physics, geometry, signal integrity). Each returns the structured report
   (`references/structured-report.md`).
4. Wave 4, skeptics: fresh agents with the diff and the report only; `audit_placeholders.py --changed-since origin/dev
   --fail-on-new`, the lane's CI groups, three numbers re-checked.
5. Wave 5, integration: every lane lands on dev with `merge_to_dev.sh` (always, red included, under MERGE_LOCK); full CI
   on dev with `-Compare <main report> -FailOn regressions`; `promote_dev.sh` only with zero regressions.

Keep lanes few and briefs exact; every wake-up of a big-context agent costs real money.

## 7b. The digital twin (both modes)

Follow `references/twin-setup.md`. In short:

1. Run the example twin as shipped; `ci.ps1 -Only twin` GREEN proves the toolchain.
2. Move the product's control code into `firmware/core` (C, HAL only; `references/firmware-host.md` for ESP32,
   ESP32-P4, STM32, RP2040, nRF, Arduino), grow its host suites.
3. Research each physical effect the scenarios need (`references/research-citations.md`): datasheet, standard,
   textbook, paper; open and verify every source; a `CITATION.md` row and a `[REF-nnn]` tag per model.
4. Replace the example plant with the product's, reusing the models; mass properties from the CAD through
   `sim/twin/tools/cad_to_twin.py`, never typed.
5. Scenarios for the task, every fault the firmware must survive and the build-spec corners, limits from
   `REQUIREMENTS.md` / `DECISIONS.md`; rows in `MATRIX.txt`; seeds in CI, 40 seeds at night.
6. Host it: Unreal (recommended; kinematic replay is hash-identical to the native runner, Chaos torque coupling for
   contact) or Isaac Sim (behind `sim/isaac/gpu_gate.py`). Engines start only when the owner agreed or overnight.
7. Margins: Monte Carlo over per-unit tolerances (`sim/twin/gpu/`, CPU reference, CUDA optional).
8. Bench: every constant that matters gets a BENCH row and a bench-versus-twin comparison when hardware exists.

## 7c. Records the agents keep

- `DECISIONS.md`: read first; never re-ask a decision; a change is a new row that supersedes an old one.
- `QUESTIONS.md`: every question for the owner, with options, the recommended default first, what it blocks and
  when the default will be taken; closed questions become decisions.
- `CITATION.md`: every model, constant and algorithm with a verified source (`ci.ps1 -Only citations`).
- `.archive/`: results leave `.tmp/` only through `tools/archive_tmp.py` (hash-checked, with a manifest).

## 8. Discipline (both modes)

- PCB: generator-first (`docs/PCB.md`). ERC 0/0, DRC 0 violations / 0 unconnected / 0 parity at default severities,
  every purchased line verified live, fab set + STEP + results JSON with SHA-256s. Hand placement by you is expected
  when the auto-placer fails. More layers are allowed; report the price delta. Relaxing a rule needs a cited basis.
- CAD: builders + plug-ins; a mass / station / CG table (`cad/mass_cg_table.py`) with every part change; fit and clash
  PNGs (`cad/render.py`) in `docs/cad_decisions/<date>/<topic>/`.
- Firmware: C (no interpreter in shipped firmware), out-of-tree builds, host suites with `hil_check.h`, scenarios
  through the real interface, seed sweeps, determinism; hardware-pending listed.
- Twin: every model cited and tested against a closed form; no Python physics; the same seed gives the same trace.
- No unfinished work shown as finished; bench-only constants get `BENCH B-xx-nn` ids.
- Machine: slots, below-normal priority, timeouts, never kill what you did not start, never `find` or recurse from a
  drive root (search only named project or tool folders), check disk first, one Unreal / GPU job at a time, GPU only
  through the gate with `HIL_GPU=1` for that job, no engine launch while the owner is at the machine unless agreed.

## 8b. Before anything real is ordered

Green gates are necessary, not sufficient. Before a board, a part or a print is ordered: walk the owner through the
CAD renders and the board (layout, stack-up, BOM with the ratings), answer "why" for every part and number with the
datasheet line or decision behind it, and run an adversarial review pass: fresh agents get the files (schematic,
board, BOM, CAD exports, firmware diff, twin results), not your conclusions, and hunt for what the gates do not
check. Findings go into the report; the owner decides the order. Keep it friendly and brief; the point is a second
pair of eyes, not a ceremony.

## 9. Handoff and overnight

- When `HANDOFF_NOW` appears (the hook tells you), stop starting work and follow its instruction: commit, push, release,
  write `harness/swarm/resume_<date>/RESUME_<date_time>_<lane>.md` (`references/resume-doc.md`).
- Before the owner leaves for the night: a detached night chain and the morning questions
  (`references/overnight-questions.md`). The night runs on the defaults; record every default taken.
