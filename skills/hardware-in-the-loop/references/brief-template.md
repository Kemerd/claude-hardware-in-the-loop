# Lane brief (addendum to the plan)

A brief is the ONLY context a lane agent has besides the repository. Write it like a work order for a peer engineer:
exact files, exact numbers, exact hand-offs, and the exact shape of the report you want back. If you cannot fill a
field, the lane is not ready to start.

```markdown
## <LANE ID> <one-line goal>

Read first: harness/swarm/AGENT_RULES.md, PLAN.md sections "Teams" + "Interface additions", recon files
<recon_a.md, recon_b.md> (verified facts with file:line anchors: use them instead of re-discovering).

Worktree / branch: .claude/worktrees/<name> on swarm/<name> (create it from origin/dev).
Landing: push your branch after every green gate; land on dev with merge_to_dev.sh: <yes / no, the integrator lands it>.

Owns (only you edit these): <files and folders>
May read: <files>
Must not touch: <files owned by other lanes>; cross-lane changes go as patches in harness/swarm/handoff/.

Facts you start from (with anchors):
- <fact> (<file>:<line>)

Task:
1. <step with the file and the function>
2. <step>

Acceptance (numbers, each with the command that proves it):
- <metric> <= <value>    (`<command>`)
- CI: `pwsh -File windows/ci.ps1 -Repo <worktree> -Only <groups>` no worse than the dev report <path>
- audit: `python -I tools/audit_placeholders.py --changed-since origin/dev --fail-on-new` = 0

Hand-offs you owe: <value / file> to <lane> by <milestone>
Hand-offs you receive: <value / file> from <lane>

Subagents: spawn Sonnet for mechanical work (ports, codecs, renders, tests, docs), Opus for subtle work (control,
physics, geometry, signal integrity). Give each the same discipline: files, numbers, report shape.

Machine: heavy jobs through slot.sh (kind <kind>, about <GB> GB), detached chains with watch.sh, BelowNormal, timeouts.

Hardware-pending: list what cannot be closed without hardware; never stub it.

Return: the structured report (references/structured-report.md), nothing else.
```
