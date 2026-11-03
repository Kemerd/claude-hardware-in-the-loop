# docs/data: citable results

Results go to docs, caches go to `.tmp/`. Anything another agent or the owner will cite later (summary JSON / CSV, result
tables, verdicts, reports, render stills, small proof logs) lives here under `docs/data/<topic>/<date>/`. Fit and clash
decision images live in `docs/cad_decisions/<date>/<topic>/`.

Every topic folder has a `README.md` with:

| Field | Example |
|---|---|
| What it is | thermal soak of the motor driver at 2 A, 30 min |
| Command | `pwsh -File windows\ci.ps1 -Only twin_thermal` or the exact script line |
| Branch and commit | `swarm/thermal` at `1a2b3c4d` |
| Date | 2026-11-03 |
| State | CURRENT / SUPERSEDED (by which folder) / STALE (why) |
| How to reproduce | the steps, the inputs, the seed |

Raw and bulky output stays in `.tmp/<job>/` and then moves to the archive. Each file here is at most 50 MB; large CSVs
are gzipped or digested (one row per run). Videos are listed with their archive path, never committed.

## Topic index

| Folder | What it is | State |
|---|---|---|
