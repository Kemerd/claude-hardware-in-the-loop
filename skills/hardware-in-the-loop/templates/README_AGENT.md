# README for an agent seeing this repository for the first time

You are working on {{PROJECT_NAME}}: {{PROJECT_SUMMARY}}

This page is the ten-minute orientation. The binding rules are in `harness/swarm/AGENT_RULES.md` (live, not tracked),
`CODING_RULES.md` and the owner's instructions. When they disagree, the owner wins.

Read in this order: this page, `DECISIONS.md` (what is settled, never re-ask it), `QUESTIONS.md` (what is open:
do not decide those yourself), `REQUIREMENTS.md`, the plan's "Where I stopped" section, `CITATION.md` before you
touch a model.

## 1. Where you work

- The main checkout belongs to the owner. Never edit its working tree, never stash or autostash in it.
- Work in a git worktree: `.claude/worktrees/<name>` on a branch `swarm/<name>` (single-session mode: one worktree,
  one branch). Commit and push after every green gate; never leave commits only on your machine.
- Two shared branches: `dev` receives every lane, always, even with red gates (`harness/swarm/merge_to_dev.sh`);
  `main` only moves by a fast-forward from `dev` when the CI matrix on dev is green with zero regressions against the
  last main report (`harness/swarm/promote_dev.sh`, integrator only).
- Never force-push. Never commit `*.kicad_prl`, a file over 50 MB, or a blob over 100 MB.

## 2. Where things live

See `PROJECT_STRUCTURE.md`. In short: code and small tracked results in the repo trees; scratch in `.tmp/` (yours to
fill, not to keep: results leave it only through `tools/archive_tmp.py`); expensive results in the archive
(`.archive/`, linked to `{{ARCHIVE_PATH}}` on the big disk); live coordination (plan, board log, locks, resume
documents) in `harness/`.

## 3. How work is gated

- `pwsh -File windows/ci.ps1 -Repo <your worktree>` runs the CI groups; before a promotion, compare against the last
  main report: `-Compare <main ci_report.json> -FailOn regressions` (0 regressions).
- `python -I tools/audit_placeholders.py --fail-on-new`: no new unfinished-work findings against the tracked baseline.
- `python -I tools/check_portability.py --fail-on-any`: every script states its interpreter, prints UTF-8, anchors
  its paths on its own location.
- Boards: ERC 0 errors / 0 warnings, DRC 0 violations / 0 unconnected / 0 parity at KiCad's default severities, every
  purchased line verified live, fab set + STEP + results JSON (`pcb/DESIGN_STANDARDS.md`, `docs/PCB.md` of the method).
- Every part change: a mass / station / CG table before and after. Every fit or clash decision: PNGs in
  `docs/cad_decisions/<date>/<topic>/`.
- A constant only a bench can settle: a row in `docs/bench/BENCH_REGISTER.md`, cited in code as
  "design value, BENCH B-xx-nn".
- The digital twin: `pwsh -File windows/ci.ps1 -Only twin` (build, host suites, the scenario matrix over seeds,
  determinism, the C ABI from Python). A firmware change is not done until its scenarios pass in the twin.
- A model or constant from a paper, a standard or a datasheet: a verified row in `CITATION.md` and `[REF-nnn]` in
  the code (`tools/check_citations.py`, suite `citations`).

## 4. Machine safety

- Heavy jobs take a slot first: `eval "$(bash harness/swarm/locks/slot.sh take <kind> <you> "<job>" <GB>)"` and
  release it when done. The gate keeps {{RAM_RESERVE_GB}} GB free and starts nothing above {{CPU_MAX}} % CPU.
- Long jobs run detached: `bash harness/swarm/detach.sh <chain.sh> <log>`, then
  `bash harness/swarm/watch.sh <log>` as a background task so you are woken on success and on failure.
- Never kill a process you did not start; kill your own only by exact PID. Never `find` over a drive root.
- Check free disk space before big builds (keep at least {{DISK_FREE_MIN_GB}} GB free).
- GPU work (Isaac Sim, CUDA sweeps, ML) only through `sim/isaac/gpu_gate.py` with `HIL_GPU=1` set for that one job;
  one GPU job at a time; never start Unreal or Isaac while the owner works at the machine unless they said so.

## 5. When the session has to stop

If `harness/swarm/HANDOFF_NOW` exists (the usage tripwire), stop starting work, commit and push, release your slots and
write a resume document under `harness/swarm/resume_<date>/`. The next session reads the newest resume documents and
the plan's "Where I stopped" section first.

## 6. First commands

```
git worktree list                                  # what exists
type harness\swarm\PLAN.md                         # the current plan and its "Where I stopped" section
dir harness\swarm\resume_*                         # newest resume documents first
pwsh -File windows\ci.ps1 -Only audit              # a fast smoke test of the gates
```

## 7. Staying current with the method (template updates)

This project was scaffolded from the hardware-in-the-loop method (https://github.com/Kemerd/claude-hardware-in-the-loop).
The method keeps improving (better board prompts, a newer viewer, fixed tools), and this project may have changed
every template since. `harness/TEMPLATE_VERSION.json` records the version it came from and, per template file, what
was shipped, so an update can tell files nobody touched from files the project made its own.

- At session start the `SessionStart` hook runs `python -I tools/hil_update.py check`. It is silent when current
  (one small request, at most once a day, offline is fine). When upstream is newer it prints `HIL_UPDATE_AVAILABLE`
  and the changelog entries in between. No hook output and no stamp: run `python -I tools/hil_update.py adopt` once.
- **Ask first, never update silently.** Tell the owner in plain lines what is new, taken from the changelog, for
  example: "A new version of the method is on GitHub. New: a better PCB layout prompt, a faster CAD viewer.
  Pulling it into this project takes about 30 seconds and never overwrites files you customised. Want it?"
  Use the question dialog; the recommended answer is yes. On no: `python -I tools/hil_update.py ignore <version>`
  (a newer version is announced again).
- On yes, in a worktree on a fresh branch, never the owner's main checkout:
  1. `python -I tools/hil_update.py plan` downloads upstream and sorts every template file: `new`, `update`
     (untouched here, safe), `merge` (changed on both sides; diffs in `.tmp/hil_update/diff/`), nothing to do.
  2. `python -I tools/hil_update.py apply` adds the new files, refreshes the untouched ones and bumps the stamp.
  3. Merge each `merge` file by hand: read its diff, keep the project's customisations, take the upstream
     improvement. A filled-in Markdown template (project name, decisions) keeps its own values. When unsure, ask.
     Then `python -I tools/hil_update.py mark <files>`.
  4. Run `pwsh -File windows/ci.ps1 -Only audit` and the groups the touched files belong to; commit as one change
     ("Update method templates to <version>"); report what changed and what is left to decide.
- If the skill itself is installed (`.claude/skills/hardware-in-the-loop`), refresh it from the same download
  (`.tmp/hil_upstream`): copy `SKILL.md`, `references/`, `scripts/`, `VERSION` and `CHANGELOG.md` over it.
