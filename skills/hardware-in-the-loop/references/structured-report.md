# Structured final report (what every lane returns)

The lead merges a dozen of these; a report that needs to be read twice costs the lead's context. Facts only: SHAs,
numbers, file paths, commands. No narrative, no apologies.

```markdown
## <LANE ID> report

Branch / worktree: swarm/<name> at <short SHA> (pushed: yes / no), .claude/worktrees/<name>
Commits: <sha> <subject> (one per line)
Files changed: <path> (+added / -removed lines), grouped by folder

Tests (before -> after, each with its command):
- <suite>: 412/412 -> 431/431   `pwsh -File windows/ci.ps1 -Repo <wt> -Only host`
- audit new findings: 0         `python -I tools/audit_placeholders.py --changed-since origin/dev --fail-on-new`

Numbers vs acceptance:
| Metric | Acceptance | Got | Command / data path |
|---|---|---|---|

Images: docs/cad_decisions/<date>/<topic>/<file>.png (what each one shows)
Data: docs/data/<topic>/<date>/ (README written: yes)

Open items: <item, why open, what closes it>
Hardware-pending: <item, the bench test that closes it, BENCH id>
Interface deviations: <interface, what changed vs the plan, who is affected>
Hand-offs made: <value / file> -> <lane> (where it is)
Owner questions: <question, options, recommended default, what I assumed meanwhile>
Worktree removable: yes / no (why)
```
