# {{PROJECT_NAME}}: single-session plan (step by step, no subagents)

Mode: single session. Started {{DATE}}. One session does the work itself, one step at a time, commits and pushes after
every green gate, and spawns NO subagents. Long machine jobs (builds, CI, routing, renders, sims) run detached
(`bash harness/swarm/detach.sh <chain.sh> <log>`) with a watcher (`bash harness/swarm/watch.sh <log>` as a background
task), and the session works on the next step while they run.

If the session has to stop, it appends a "## Where I stopped" section to THIS file (what is done with SHAs, what is
running with PIDs and logs, the exact next step), and the next session resumes from it.

## 0. Ground rules

`harness/swarm/AGENT_RULES.md` (binding), `CODING_RULES.md`, `REQUIREMENTS.md`. Machine limits: CPU cap {{CPU_MAX}} %,
{{RAM_RESERVE_GB}} GB RAM kept free, {{DISK_FREE_MIN_GB}} GB disk floor. Work in `.claude/worktrees/main-work` on
branch `swarm/main-work`; land on dev after every step (`merge_to_dev.sh`), promote dev to main when CI is green.

## 1. Steps (in order; one at a time)

| # | Step | Gate that proves it | State | SHA |
|---|---|---|---|---|
| 1 | Toolchain verified (`scripts/setup_tools.ps1 -VerifyOnly`) | every tool reports a version | open | |
| 2 | First CI run on main, saved as the reference report | `windows/ci.ps1` writes `ci_report.json` | open | |

## 2. Detached jobs

| Started | Chain | Log | PID | Marker | Result |
|---|---|---|---|---|---|

## 3. Owner questions (batched, recommended default first)

| # | Question | Options | Default | Answer / default taken |
|---|---|---|---|---|

## Where I stopped
