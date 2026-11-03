---
name: hardware-in-the-loop
description: Sets up and runs the agent-driven hardware-in-the-loop engineering method for a hardware project - generator-first KiCad boards with Freerouting and live part verification, parametric CadQuery CAD with plug-in parts and mass/CG tables, host-tested firmware against a digital twin, a PowerShell CI matrix with no-shortcut and portability audits, dev/main branching with merge locks, machine-safety slots, detached jobs, usage-limit handoffs and overnight sessions, in either single-session or swarm mode. Use when the user says "hardware in the loop", "set up a hardware project", "PCB/CAD/firmware swarm", asks to scaffold a hardware repo for agents, or wants agents to design boards, enclosures or firmware with gates.
---

# Hardware in the loop

You are setting up (or running) a hardware project where agents do the engineering and gates prove it. Read this
whole file before acting. The method is described in the repository's `docs/` (METHOD, PCB, CAD, FIRMWARE, SIM, SWARM,
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

Materials, fastening, enclosure sealing and clearance are PROJECT DECISIONS: never pick them silently; ask, recommend,
record.

Write the answers to `answers.json` in the target repository's `.tmp/` (schema in `references/interview.md`).

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

Then fill the decision table in `pcb/DESIGN_STANDARDS.md` section 0 from the answers.

## 3. Tools

```
pwsh -File <skill>/scripts/setup_tools.ps1 -ToolsRoot <a folder outside the user profile> [-VerifyOnly]
bash <skill>/scripts/setup_tools.sh --tools-root <dir> [--verify-only]          # Linux
```

KiCad 10 (winget), Java 21 (winget), Freerouting 2.1.0 (pinned SHA-256), OSS CAD Suite 2026-10-24 (pinned SHA-256,
only for FPGA projects), a CadQuery venv. It writes `<ToolsRoot>/hil_tools.env` with `HIL_FREEROUTING_JAR`,
`HIL_JAVA`, `HIL_KICAD_CLI`, `HIL_CQ_PYTHON`. Ask before installing anything machine-wide; run `-VerifyOnly` first and
show the user what is missing. Nothing goes into the user profile.

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

## 8. Discipline (both modes)

- PCB: generator-first (`docs/PCB.md`). ERC 0/0, DRC 0 violations / 0 unconnected / 0 parity at default severities,
  every purchased line verified live, fab set + STEP + results JSON with SHA-256s. Hand placement by you is expected
  when the auto-placer fails. More layers are allowed; report the price delta. Relaxing a rule needs a cited basis.
- CAD: builders + plug-ins; a mass / station / CG table (`cad/mass_cg_table.py`) with every part change; fit and clash
  PNGs (`cad/render.py`) in `docs/cad_decisions/<date>/<topic>/`.
- Firmware: out-of-tree builds, host suites with `hil_check.h`, scenarios through the real interface, seed sweeps,
  determinism; hardware-pending listed.
- No unfinished work shown as finished; bench-only constants get `BENCH B-xx-nn` ids.
- Machine: slots, below-normal priority, timeouts, never kill what you did not start, never `find` from a root, check
  disk first, one Unreal / GPU job at a time, no Unreal launch while the owner is at the machine.

## 9. Handoff and overnight

- When `HANDOFF_NOW` appears (the hook tells you), stop starting work and follow its instruction: commit, push, release,
  write `harness/swarm/resume_<date>/RESUME_<date_time>_<lane>.md` (`references/resume-doc.md`).
- Before the owner leaves for the night: a detached night chain and the morning questions
  (`references/overnight-questions.md`). The night runs on the defaults; record every default taken.
