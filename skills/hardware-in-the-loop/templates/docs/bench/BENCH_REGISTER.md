# Bench parameter register

A value that only a bench test can settle is registered here with an id. The code that uses the value cites the id
(`design value, BENCH B-xx-nn`), carries the design value until the bench runs, and the row says how to measure it and
what range the design already tolerates:

- measured inside the pass range: write the measured number into every cited file and mark the row MEASURED with the
  date, the rig and the data path (`docs/data/bench/<id>/<date>/`);
- measured outside it: the design is reopened (the row says what to re-run), never the range widened.

Ids are grouped by subsystem so parallel lanes never collide: `B-<SUBSYSTEM>-<nn>` (for example `B-PWR-01`,
`B-MOT-03`, `B-IMU-02`). The audit gate accepts a comment that cites an id in place of an admitted guess.

## How to write a row

| Column | What goes in it |
|---|---|
| Id | `B-<SUBSYSTEM>-<nn>`, never reused |
| Parameter (design value) | the quantity, its unit, the value the code carries now, and where that value came from (datasheet table, calculation file, similar part) |
| Used in | every file and symbol that holds the value (`firmware/.../motor.h` `MOT_KV_DESIGN`) |
| Test method | the rig, the instruments with their resolution, the procedure step by step, the number of repeats |
| Pass range | the range the design tolerates, and why (which margin or check consumes it) |
| State | OPEN / MEASURED <date> <value> / REOPENED <date> |

## Register

| Id | Parameter (design value) | Used in | Test method | Pass range | State |
|---|---|---|---|---|---|
