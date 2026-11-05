# {{PROJECT_NAME}}: chain plan (one lead, one or two subagents at a time)

Mode: chain. Started {{DATE}}. One long lead session owns the plan and the decisions. It hands bounded cards to one or two subagents at a time, takes each report, checks it, and goes on. Long machine jobs (builds, CI, routing, renders, sims) run detached (`bash harness/swarm/detach.sh <chain.sh> <log>`) with a watcher (`bash harness/swarm/watch.sh <log>` as a background task).

Why this works: the file dumps, build logs and trial and error stay in the subagent's context, and the lead keeps only the conclusion. The lead's context stays small enough to hold the whole project for days.

If the session has to stop, it appends a "## Where I stopped" section to THIS file (what is done with SHAs, what is running with PIDs and logs, the card in flight, the exact next step), and the next session resumes from it.

## 0. Ground rules

`harness/swarm/AGENT_RULES.md` (binding), `CODING_RULES.md`, `REQUIREMENTS.md`. Machine limits: CPU cap {{CPU_MAX}} %, {{RAM_RESERVE_GB}} GB RAM kept free, {{DISK_FREE_MIN_GB}} GB disk floor. Land on dev after every card (`merge_to_dev.sh`), promote dev to main when CI is green.

- **One or two subagents at a time.** Start a second only when the two cards touch different files and will not both need the heavy machine slots. Do not start one just because you can: the lead has to read and check every report. The lead may work on files no card touches meanwhile.
- **A card is bounded.** One outcome, the files to read, the acceptance numbers, what to leave alone, a report file. If you cannot write the brief, the lead does not understand the task yet.
- **A fresh agent per card.** Never wake an old agent with a large context to ask it a small thing.
- **Lead model:** Fable or Opus. **Subagent model:** Opus for subtle or hard cards, Sonnet for mechanical ones, chosen per card.
- **The lead does a small job itself** when it is a handful of tool calls. A card costs more than it looks.
- **The lead checks the report.** Spot-check at least one number against the files before it counts as done.

## 1. Cards (in order)

| # | Card | Model | Gate that proves it | State | SHA |
|---|---|---|---|---|---|
| 1 | Toolchain verified (`scripts/setup_tools.ps1 -VerifyOnly`) | lead | every tool reports a version | open | |
| 2 | First CI run on main, saved as the reference report | lead | `windows/ci.ps1` writes `ci_report.json` | open | |

## 2. Detached jobs

| Started | Chain | Log | PID | Marker | Result |
|---|---|---|---|---|---|

## 3. Owner questions (batched, recommended default first)

| # | Question | Options | Default | Answer / default taken |
|---|---|---|---|---|

## Where I stopped
