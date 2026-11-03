# Resume document (written when a session stops: usage tripwire, end of day, crash recovery)

File name: `harness/swarm/resume_<YYYY-MM-DD>/RESUME_<YYYY-MM-DD_HHMM>_<lane>.md` (date and time first, so the folder
sorts by date). A copy is committed on the lane's branch as `handoff/RESUME_<YYYY-MM-DD_HHMM>_<lane>.md`. The next
session reads the newest resume documents BEFORE anything else.

Write it from what you already know; do not research more once the tripwire has fired.

```markdown
# RESUME <YYYY-MM-DD HH:MM> <lane>

## Goal and brief
<one paragraph; link to the brief section>

## Where I stopped
- Branch / worktree / last pushed SHA: swarm/<name>, .claude/worktrees/<name>, <sha>
- Uncommitted work: none / <files, and why they could not be committed>
- Running jobs: <chain, log path, PID, expected marker>; stopped jobs: <PID, why>
- Slots / locks held: none (released at <time>)

## Owner decisions and constraints I was given
- <decision, date, source>

## Done
- <what> (<sha>, <files>, <numbers>, <image paths>)

## In progress (exact state)
- <what, which step, what the next command is>

## Results so far
| Metric | Value | Command / data path |
|---|---|---|

## Blocked
- <what, why, what unblocks it>

## Dependencies on other lanes
- <lane, what, state>

## Next steps (exact)
1. <command or edit>

## Gates that must run at the end
- <command>

## Open owner questions
- <question, options, recommended default>

## Write-offs (abandoned, and why)
- <what was dropped, why, what replaces it>
```
