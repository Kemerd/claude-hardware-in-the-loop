# Wave checklist (swarm mode)

Tick through this for every wave. Each line is cheap to check and expensive to skip.

## Before wave 1 (recon)

- [ ] The owner was told the cost: a swarm day has burned 4-5 million tokens; the single-session mode was offered.
- [ ] Usage tripwire armed (`HIL_HANDOFF_PCT`, status line shows "week N%"), hook self-test answered
      (`touch harness/swarm/HANDOFF_SELFTEST`, run any tool, see "hook is live", remove the file).
- [ ] `wip_backup.sh` running detached; `slot.sh status` clean (no stale slots); disk floor checked.
- [ ] A clean CI report of main exists (the regression reference): path written in the plan.
- [ ] Recon scopes do not overlap; each recon agent is told: verified facts only, file:line anchors.

## Wave 2 (plan + briefs)

- [ ] Every lane has: owned files (no file owned twice), acceptance numbers with commands, hand-offs in and out.
- [ ] Interface additions listed with producer and consumers.
- [ ] Model per lane: Opus for subtle work (control, physics, geometry, signal integrity), Sonnet for mechanical work.
- [ ] Owner questions batched (at most four per dialog), recommended default first; defaults written into the plan.
- [ ] Lane count justified: fewer, larger lanes beat many small ones (each wake-up re-reads a big context).

## Wave 3 (lanes)

- [ ] Each lane has a worktree on `swarm/<name>` from `origin/dev`.
- [ ] Heavy jobs only through `slot.sh`; long chains detached with a watcher.
- [ ] Lanes push after every green gate; the lead audits `git rev-list HEAD --not --remotes` per worktree.
- [ ] The board log (PLAN section 6) gets one line per milestone with SHA and numbers.

## Wave 4 (skeptics)

- [ ] A skeptic gets the lane's report and the diff, NOT the lead's opinion of it.
- [ ] Skeptic runs: `python -I tools/audit_placeholders.py --changed-since origin/dev --fail-on-new`, the lane's CI
      groups, and spot-checks three numbers from the report against their commands.
- [ ] Verdict per lane: green / red with the failing evidence.

## Wave 5 (integration)

- [ ] Every lane landed on dev with `merge_to_dev.sh` (red lanes too, with their gate note).
- [ ] Full CI on dev with `-Compare <main report> -FailOn regressions`.
- [ ] Zero regressions: integrator runs `promote_dev.sh`; otherwise the regressions become the next wave's first lane.
- [ ] Worktrees that are merged and clean are removed by name after their ignored outputs are archived.
- [ ] Plan updated: "Where I stopped" for the next session.
