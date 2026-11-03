# The method, end to end

This page is the whole method on one page. The detail lives in the topic pages: [PCB](PCB.md), [CAD](CAD.md),
[FIRMWARE](FIRMWARE.md), [SIM](SIM.md), [SWARM](SWARM.md), [TOOLS](TOOLS.md), [LESSONS](LESSONS.md).

The idea in one paragraph: hardware is described by code (board generators, parametric CAD builders, firmware), every
artefact is produced by a script that agents can run, read and diff, and every claim is checked by a gate that runs on
the PC: CI suites, a no-shortcut audit, ERC and DRC, a digital twin running the real firmware. Agents do the work in
isolated git worktrees, land everything on `dev`, and `main` only moves when the whole matrix is green. The machine is
protected from the agents (slots, priorities, detached jobs), the work is protected from crashes (push after every gate,
WIP snapshots), and the token budget is protected by a tripwire that makes every agent stop and write a resume document.

## 1. Storage tiers

| Tier | Where | Tracked | Holds | Lifetime |
|---|---|---|---|---|
| Repository | the repo trees | yes | source, generators and their exports (JSON / STL / STEP), PCB projects and fab sets, docs, small results under `docs/data/<topic>/<date>/` | forever |
| Scratch | `.tmp/` in every checkout | no | build trees, CI output, frames, sim runs, router work folders, logs | until archived; then deleted BY NAME |
| Archive | a big drive, linked as `archive/` (a junction on Windows) | no | videos (never deleted), sim outputs, renders, routing outputs, old scratch, data of removed worktrees | forever |

- A tracked result folder has a `README.md`: command line, branch, commit, date, state (CURRENT / SUPERSEDED / STALE),
  how to reproduce. Each file at most 50 MB; no blob over 100 MB.
- Live coordination (plan, board log, locks, resume documents, the harness scripts) lives in `harness/`, ignored by git,
  in the main checkout only.
- The root stays clean: `README.md`, `README_AGENT.md`, `PROJECT_STRUCTURE.md`, `REQUIREMENTS.md`, `CODING_RULES.md`,
  `.gitignore`, `.gitattributes`. Anything else at the root gets filed into a tree.

## 2. Branching, and never losing work

```
 swarm/<lane-a> ──┐  (worktree .claude/worktrees/lane-a)
 swarm/<lane-b> ──┼──> dev  ── CI matrix green, 0 regressions vs the last main report ──> main (fast-forward)
 swarm/<lane-c> ──┘  every lane lands on dev ALWAYS, red gates included (stated in the merge note)
```

- Lanes work in git worktrees `.claude/worktrees/<name>` on branches `swarm/<name>`. The main checkout belongs to the
  owner: agents never edit its tree and never stash in it (the stash stack is shared by all worktrees).
- Push after every green gate. `git rev-list HEAD --not --remotes` should be empty whenever a lane stops.
- `harness/swarm/wip_backup.sh` snapshots uncommitted work of every checkout every 20 minutes to `backup/wip-<branch>`
  using a throwaway index (nobody's tree or index is touched). Each snapshot has the previous backup as a parent, so the
  push is always a fast-forward: nothing in the method ever force-pushes.
- Every lane lands on `dev` with `merge_to_dev.sh`, even with red gates: nothing is lost and conflicts between lanes
  show up the same day. The red state is written in the merge note.
- `dev` is promoted to `main` by `promote_dev.sh` only when the CI report for that exact dev commit is green and its
  `-Compare` against the last main report shows zero regressions; the push is a fast-forward.
- `MERGE_LOCK` (an atomic `mkdir` with `owner.txt`: agent, branch, time, pid) serialises every merge into dev and main.
  An integrator role does the final merges; other agents only push their branches unless their brief says otherwise.
- Never commit `*.kicad_prl`, files over 50 MB, secrets, or private data.

## 3. Gates

| Gate | Tool | Fails when |
|---|---|---|
| CI matrix | `windows/ci.ps1` (registry of groups -> suites; each group its own pwsh process, groups in parallel, "late" groups after) | any suite fail / build_fail / error; with `-Compare <main report> -FailOn regressions`: any regression |
| No-shortcut audit | `tools/audit_placeholders.py --fail-on-new` | a NEW unwaived high / medium finding vs the tracked baseline: open-work markers, admitted guesses, stub functions, drivers returning canned success, CAD blocks named or shaped as stand-ins, default credentials in config |
| Portability | `tools/check_portability.py --fail-on-any` | a script without an interpreter line, UTF-8 console, `__file__` / `$0` / `$PSScriptRoot` anchoring, or with a user-profile path |
| Boards | the PCB pipeline + `windows/ci/pcb.ps1` | ERC not 0 errors / 0 warnings; DRC not 0 violations / 0 unconnected / 0 parity at KiCad's DEFAULT severities; a purchased line that does not resolve live to its MPN |
| Standards | rule ids in `pcb/DESIGN_STANDARDS.md` | a rule broken without a cited, reviewed exception |
| Bench register | `docs/bench/BENCH_REGISTER.md` | a constant only a bench can settle appears in code without its `BENCH B-xx-nn` id |
| Mechanical | builder `checks()` + plug-in checks | interference, keep-out violations, mass / CG outside the budget |
| Firmware | host suites + twin scenarios | any failed check; the summary line missing |

CI writes `ci_report.json` plus per-group results and logs under `.tmp/ci/<stamp>/`, never under the user profile, and
never touches the GPU. A run where nothing ran is red. A suite that exits 0 without its summary line is red.

## 4. Engineering method per discipline

- **PCB** ([PCB.md](PCB.md)): a board is a Python script that declares data; one shared pipeline writes the schematic,
  localizes every footprint into the board's own library, runs ERC, builds and checks placement, routes with
  Freerouting, cleans up, pours, runs the final DRC with parity, writes the fab set, verifies every purchased line live,
  exports STEP and fit JSON, and records SHA-256 hashes. Deterministic, home-dir isolated.
- **CAD** ([CAD.md](CAD.md)): parametric CadQuery builders, one per assembly, exporting STEP / STL / JSON that the twin,
  the sims and the viewer read 1:1. Parts are added as plug-in files. Every part change carries a mass / station / CG
  table; every fit or clash decision carries PNGs. Materials and fastening are project decisions.
- **Firmware** ([FIRMWARE.md](FIRMWARE.md)): out-of-tree builds, host-compiled suites with thousands of checks in CI,
  firmware-in-the-loop against a native twin through the real station API, bench rigs with logging, flash scripts per
  target, hardware-pending items listed explicitly.
- **Simulation** ([SIM.md](SIM.md)): three tiers (native twin in CI, Isaac Sim on the GPU behind a gate, an Unreal
  visual twin), all reading the CAD exports.

## 5. Agents: two modes

| Mode | How it works | Cost | When |
|---|---|---|---|
| Single session | one session works step by step from a plan, spawns no subagents, runs long jobs detached and resumes from "Where I stopped" | lowest | the default; most projects, most days |
| Swarm | a lead runs waves: recon, plan + briefs, lanes in worktrees with subagents, skeptics, integration | a swarm day has burned **4-5 million tokens**; waking agents that carry 300-800k tokens of context is the expensive part | a deadline, many independent lanes, an owner who accepts the cost |

The swarm is described in [SWARM.md](SWARM.md): model allocation, waves, briefs, file ownership, skeptics, the integrator.

## 6. Machine safety

- Every heavy job takes a slot (`harness/swarm/locks/slot.sh`): atomic `mkdir` slots with `owner.txt` (pid, agent, job,
  expected GB), stale slots reclaimed when the pid is dead, a gate of free RAM minus pending reservations minus the job's
  need >= the reserve (owner default 4-8 GB) AND CPU below the cap (default 90 %), a start gap so each start's CPU ramp
  is seen, a fairness queue with a starvation guard, and per-kind capacity (one Unreal, one GPU job).
- Jobs run at below-normal priority with timeouts. Nobody kills a process they did not start; your own only by exact PID.
- Long chains launch detached (`detach.sh`: PowerShell `Start-Process -WindowStyle Hidden` on Windows, because Git Bash
  has no `setsid` and `nohup` children die with the session). They write `done` / `FAILED` markers; `watch.sh` wakes the
  agent on success AND failure and flags a job with no log growth for 10 minutes as hung.
- Disk space is checked before big builds and before creating worktrees (each worktree is a full checkout).
- Never `find` over a drive root or a whole repo; use `rg --files -g <name> <dir>` or `git ls-files`.

## 7. Usage limits and handoffs

- A status-line script reads the Claude Code status JSON, shows the weekly utilisation, and at the threshold (owner
  default 95 %) creates `harness/swarm/HANDOFF_NOW`.
- A PostToolUse hook (fast path: two file tests, drains stdin) then returns an `additionalContext` instruction to the
  lead and every subagent: stop starting work, commit and push, release slots, write a resume document to
  `harness/swarm/resume_<date>/RESUME_<date_time>_<lane>.md` with "Where I stopped", open items, and write-offs (what
  is abandoned and why).
- Resume documents and the plan's "Where I stopped" section are the first thing the next session reads.

## 8. Overnight sessions

Before the owner leaves, the lead prepares a detached night chain (builds, gates, routing, renders; one slot and a
timeout per step, independent steps continue after a failure) and a batched list of morning questions, each with
context, options, the recommended default and what was assumed meanwhile. The night runs on the defaults; the answers
in the morning go into the standing decisions. Templates: `skills/hardware-in-the-loop/references/overnight-questions.md`.

## 9. Owner decisions

Decisions for the owner are batched into the question dialog (at most four per batch), recommended default first. With
no answer, the default is taken and written into the plan; settled decisions go into the "Standing owner decisions"
table of `AGENT_RULES.md` so nobody asks twice.
