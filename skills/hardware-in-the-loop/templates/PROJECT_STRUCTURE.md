# Project structure: where code, data and scratch live

This page says where every kind of file belongs, so the root stays clean and no result is ever lost.
`README.md` is the human entry point, `README_AGENT.md` the agent entry point, `REQUIREMENTS.md` the definition of
done, `CODING_RULES.md` the coding rules.

## 1. The three storage tiers

| Tier | Path | Tracked | What goes there | Lifetime |
|---|---|---|---|---|
| Repository | the trees below | yes | source, generators and their exported JSON / STL / STEP, PCB projects and fab sets, docs, small results (`docs/data/<topic>/<date>/`, each file <= 50 MB, no blob > 100 MB) | forever (git history) |
| Scratch | `.tmp/` in the main checkout and in every worktree | no | build trees, CI output, render frames, sim run folders, routing work folders, logs: anything a job produces while it runs | until the result is archived; then cheap-to-remake intermediates are deleted BY NAME, never with a sweeping remove |
| Archive | `{{ARCHIVE_PATH}}` (linked as `archive/` in the main checkout, ignored) | no | everything expensive that is not in git: videos (never deleted), sim run outputs, renders, routing outputs, old scratch trees, data from removed worktrees (`worktrees/<name>/`) | forever |

Live coordination files are not scratch and never sit in `.tmp/`: they live in `harness/` (ignored, section 4).

## 2. Repository trees

| Tree | Contents |
|---|---|
| `firmware/` | one folder per microcontroller target; `host_tests/` builds and runs the control code on the PC |
| `sim/` | `twin/` (native digital twin running the real firmware control code), `isaac/` (USD build, GPU-gated), `unreal/` (visual twin glue) |
| `cad/` | CadQuery builders, one folder per assembly, each with `parts/` plug-ins and its exports (`<asm>_cad.json`, `parts.json`, `articulation.json`, `stl/`, `step/`) |
| `pcb/` | one folder per board: its generator script, `kicad/` project, `fab/` set, results JSON; `tools/` (the shared pipeline); `DESIGN_STANDARDS.md` (the rule book) |
| `docs/` | design notes, `bench/` (procedures + `BENCH_REGISTER.md`), `cad_decisions/<date>/<topic>/` (fit and clash PNGs with the decision note), `data/<topic>/<date>/` (tracked results), `datasheets/` |
| `tools/` | repo-wide Python tooling: `audit_placeholders.py` (+ `audit_baseline.json`, `audit_waivers.json`), `check_portability.py` |
| `windows/` | PowerShell entry points: `ci.ps1` and `ci/*.ps1` (one runner per CI group) |
| `interfaces/` | the contracts between parts: pin maps, wire list, protocol notes |

Root files: `README.md`, `README_AGENT.md`, `PROJECT_STRUCTURE.md`, `REQUIREMENTS.md`, `CODING_RULES.md`,
`.gitignore`, `.gitattributes`. Anything else at the root is a mistake to be filed into a tree above.

## 3. Rules for results

- A number that goes into a report lives in git: `docs/data/<topic>/<date>/` (CSV / JSON / MD). Its `README.md`
  states the command line, branch, commit, date and state (CURRENT / SUPERSEDED / STALE). The raw run goes to the
  archive. A sim or study job ends by writing its digest (one CSV row per run) there; a raw run folder with no
  digest is unfinished work.
- Fit and clash decisions come with PNGs in `docs/cad_decisions/<date>/<topic>/`.
- Every board change regenerates its fab set under `pcb/<board>/fab/` and its results JSON.
- `*.kicad_prl` (KiCad per-user state) is never committed.
- Files the twin and the sims read (parameters generated from CAD) are regenerated and committed together with the
  CAD change that moved them.

## 4. `harness/`: live coordination (ignored)

`harness/swarm/` holds the active plan (`PLAN.md`), the milestone log (`BOARD.md`), `AGENT_RULES.md`, `machine.env`
(machine limits), the scripts (`detach.sh`, `watch.sh`, `locks/slot.sh`, `merge_lock.sh`, `merge_to_dev.sh`,
`promote_dev.sh`, `wip_backup.sh`, `handoff_hook.sh`, `usage_statusline.py`) and the resume documents
(`resume_<date>/`). The scripts are copies of the skill templates; a fix to one goes back into the skill.

## 5. Worktrees

Agent lanes work in `.claude/worktrees/<name>` on branches `swarm/<name>`. Before a worktree is removed, its
uncommitted work is committed and pushed on its own branch and its ignored outputs are archived to
`{{ARCHIVE_PATH}}/worktrees/<name>/`. Check free disk space before creating worktrees: each one is a full checkout.
