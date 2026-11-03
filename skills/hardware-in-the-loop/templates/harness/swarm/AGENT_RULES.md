# {{PROJECT_NAME}}: hard rules for every agent (owner's, non-negotiable)

Read this before you start. Your brief has the task; this file has the rules. When the owner says something different
in the session, the owner wins and the lead writes it here.

## Git

- Commit with the machine's configured git identity. No attribution lines, no co-author trailers, no generated-by
  footers, anywhere.
- Work in your own worktree `.claude/worktrees/<name>` on branch `swarm/<name>`. Never edit the main checkout's working
  tree, never `git stash` there (the stash stack is shared by every worktree), never autostash.
- Push your branch after every meaningful step and after every green gate; `git rev-list HEAD --not --remotes` must be
  empty when you stop. `harness/swarm/wip_backup.sh` covers uncommitted files every 20 minutes as crash insurance only.
- Never force-push. No file over 50 MB, no blob over 100 MB, never `*.kicad_prl`.
- Landing: `bash harness/swarm/merge_to_dev.sh <you> --gate-note "<gate state>"` lands your branch on `dev`, ALWAYS,
  even with red gates (state them in the note). Do not touch `main`: the integrator promotes `dev` to `main` with
  `promote_dev.sh` when dev's CI is green with zero regressions. Your brief says if you may land on dev yourself or
  only push your branch for the integrator.
- `MERGE_LOCK` (`harness/swarm/merge_lock.sh`) serialises every merge into dev and main. Take it, merge, push,
  release. Never delete someone else's lock.

## Results

- Results go to docs, caches go to `.tmp/`. Anything another agent or the owner will cite (summary JSON / CSV, tables,
  verdicts, decision images, render stills, small proof logs) goes to `docs/data/<topic>/<date>/` with a `README.md`
  (command line, branch, commit, date, state CURRENT / SUPERSEDED / STALE, how to reproduce) and a row in
  `docs/data/README.md`. Decision images: `docs/cad_decisions/<date>/<topic>/`.
- Raw and bulky output stays in `.tmp/<job>/` and moves to the archive when the job is done. Videos are never deleted.
- Data is hoarded: never delete sim outputs, traces, reports or renders; archive them. Delete scratch only by naming
  the folder, after the archive copy exists.

## Machine safety (the PC has crashed before)

- Every heavy job (builds, sims, CAD chains, KiCad batches, routers, renders, Unreal) takes a slot:
  `eval "$(bash harness/swarm/locks/slot.sh take <kind> <you> "<job>" <GB>)"`, records its pid, releases in a trap.
  The gate keeps {{RAM_RESERVE_GB}} GB free and starts nothing above {{CPU_MAX}} % CPU. One Unreal, one GPU job at a time.
- Jobs run at below-normal priority with a timeout. A job with no log growth for 10 minutes is hung.
- Never kill a process you did not start; stop your own only by exact PID. Never touch the owner's applications.
- Never run `find` over a drive root or a whole repository; use `rg --files -g <name> <dir>`, `git ls-files`, or a
  directory listing of a known parent, always with a timeout.
- Check free disk space before big builds, sims and new worktrees (keep {{DISK_FREE_MIN_GB}} GB free). Say at the end
  whether your worktree can be removed.
- Long chains run detached (`bash harness/swarm/detach.sh <chain.sh> <log>`); start `bash harness/swarm/watch.sh <log>`
  as a background task so you are woken on success AND on failure.
- No Unreal launch (editor, commandlet, play-in-editor, render queue) while the owner is at the machine; compiling is fine.
- Never drive the owner's mouse or keyboard.

## Quality

- Fix at the cause. Never lower a gate bar, widen a rule, add a waiver, or disable a test to get green.
- No unfinished work shown as finished, no fake data, no stand-in parts. A bench-only constant gets a
  `BENCH B-xx-nn` id. Estimates say they are estimates.
- Heavy comments; Doxygen in C and C++; defensive code (check every input).
- Every part change: a mass / station / CG table (part, mass before and after, station x y z, CG shift, inertia
  change) and the builder's checks. Every fit or clash decision: PNGs (readable, clash in red, dimensions).
- Boards: hand placement by you is expected when an auto-placer fails (explicit, reasoned coordinates in the
  generator). More layers are allowed as a cause-level fix; report the fab price delta. Relaxing any rule needs a
  cited basis in the report.
- Known failures on main do not count against you, but you show you are no worse than main.

## Standing owner decisions

The lead appends every owner decision here with its date and number, so no lane re-asks a settled question.

| # | Date | Decision |
|---|---|---|
{{DECISIONS}}

## Reporting

End with a structured report (template `references/structured-report.md` of the skill): branch, commits, files, tests
before and after with commands, numbers against the acceptance numbers, open items, interface deviations, hand-offs
made, hardware-pending items, and whether your worktree can be removed.
