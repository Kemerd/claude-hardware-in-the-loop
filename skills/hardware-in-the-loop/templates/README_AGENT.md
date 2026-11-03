# README for an agent seeing this repository for the first time

You are working on {{PROJECT_NAME}}: {{PROJECT_SUMMARY}}

This page is the ten-minute orientation. The binding rules are in `harness/swarm/AGENT_RULES.md` (live, not tracked),
`CODING_RULES.md` and the owner's instructions. When they disagree, the owner wins.

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
fill, not to keep; delete by name only after the result is archived); expensive results in the archive
(`{{ARCHIVE_PATH}}`); live coordination (plan, board log, locks, resume documents) in `harness/`.

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

## 4. Machine safety

- Heavy jobs take a slot first: `eval "$(bash harness/swarm/locks/slot.sh take <kind> <you> "<job>" <GB>)"` and
  release it when done. The gate keeps {{RAM_RESERVE_GB}} GB free and starts nothing above {{CPU_MAX}} % CPU.
- Long jobs run detached: `bash harness/swarm/detach.sh <chain.sh> <log>`, then
  `bash harness/swarm/watch.sh <log>` as a background task so you are woken on success and on failure.
- Never kill a process you did not start; kill your own only by exact PID. Never `find` over a drive root.
- Check free disk space before big builds (keep at least {{DISK_FREE_MIN_GB}} GB free).

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
