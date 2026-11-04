# PCB: the generator-first method

A board is a Python script. The script declares data (parts with their datasheet pin tables, nets, outline, holes,
placement, net classes, pours) and one shared pipeline turns that data into a schematic, a board, checks, a fab set and
the files the mechanical CAD needs. Nobody edits a `.kicad_sch` or `.kicad_pcb` by hand; the generator is the design.

Files (in the skill's templates, copied into a project as `pcb/`):

| File | What it is |
|---|---|
| `pcb/tools/kicad_env.py` | finds KiCad, isolates its per-user folders, seeds minimal library tables, turns KiCad's git integration off |
| `pcb/tools/board_pipeline.py` | the `Board` class: declaration API + the 12 pipeline steps |
| `pcb/tools/distributor_verify.py` | live check that every distributor order code resolves to the MPN the schematic names |
| `pcb/example_board/gen_example_board.py` | a real 2-layer 3.3 V LDO breakout that runs every step (about 20 s) |
| `pcb/DESIGN_STANDARDS.md` | the rule book with rule ids (PROT-01, RED-01, FAB-02, ...) and the project decision table |

## 1. Why generator-first

- **The agent can do it.** An agent cannot drag parts in a GUI, but it can write data and read DRC JSON. A declared board is
  reviewable in a diff, and the reason for every coordinate sits next to it as a comment.
- **One fix fixes every board.** The pipeline is shared. A lesson learned on one board (a footprint quirk, a DRC rule, a
  router flag) is fixed once in `board_pipeline.py` and every board picks it up on its next run.
- **Repeatable.** Same inputs, same files (section 4), so a diff of a generated file means the design changed.
- **Gated.** The pipeline refuses to write a fab file unless ERC, DRC, schematic parity and the live distributor check are
  all clean. A board that "mostly works" cannot be ordered by accident.

## 2. The 12 steps

| # | Step | Tool | Stops the run when |
|---|---|---|---|
| 1 | design checks: every symbol pin has a footprint pad and vice versa, no single-pin nets, every part placed, protection coordination (TVS-02: the clamp of every TVS, carried through series parts and declared divider nodes, against every rated pin; unrated supply pins are findings) | python | any finding |
| 2 | board library: every footprint copied into the board's OWN `<board>_lib.pretty` | python (text) | a stock footprint is missing |
| 3 | schematic + symbol library + project + lib tables written from the data | python (text) | |
| 4 | ERC | `kicad-cli sch erc --severity-all` | any error or warning |
| 5 | board: outline, footprints, nets, holes, design rules, net classes, stitching vias | pcbnew | a footprint does not load |
| 6 | placement DRC (courtyards, edge clearance, parity) before any routing | `kicad-cli pcb drc --schematic-parity` | any violation or parity item |
| 7 | routing | Freerouting 2.1 (Specctra DSN -> SES) | router missing, timeout, hang, failed import |
| 8 | router remnant clean-up (KiCad's dangling-track test, repeated until stable) | pcbnew | |
| 9 | copper pours + zone fill, then dangling vias | pcbnew | |
| 10 | final DRC with schematic parity, all severities, KiCad default severities | `kicad-cli pcb drc` | any error / warning / unconnected / parity item |
| 11 | live distributor verification of every purchased line | `distributor_verify.py` | a mismatch, an unknown code, or (without `--offline-ok`) an unreachable distributor |
| 12 | fab set (Gerbers, Excellon, CPL, BOM, zip), STEP, CAD fit JSON, results JSON | `kicad-cli pcb export ...` | |

Fab files are written only when steps 4, 10 and 11 are clean. The results JSON and CAD fit JSON are always written, so a
red run still says exactly why.

### Details that matter

- **Pin tables come from the datasheet**, as `(number, name, electrical type)`. Electrical types drive ERC: a regulator
  output is `power_out`, its input `power_in`; a net with `power_in` pins and no `power_out` pin gets a `PWR_FLAG`
  automatically (step 3). Unused pins get a no-connect flag; their pads get KiCad's `unconnected-(<ref>-<pin>-Pad<n>)` net
  so schematic parity agrees.
- **Every footprint lives in the board's own library.** Stock footprints are copied in with their silkscreen moved to
  the fab layers (a text edit of the `.kicad_mod`, never a pcbnew `Remove()`, see the lessons). Custom footprints are
  written from the vendor's land-pattern table with `Board.custom_footprint()`: the WHOLE land pattern of the real
  package, never a reduced-pin stand-in that "fits for now". The project `fp-lib-table` lists only the board library, so a
  board never depends on what happens to be installed on a machine.
- **Fab capability is a set of DRC rules.** The rules dict (`JLC_RULES`, conservative values at or above JLCPCB's
  published standard-process minimums) is written into the board's design settings, so KiCad's own DRC enforces the fab.
- **Parity links footprints to symbols by path** (`/<symbol uuid>` for the root sheet) and every symbol field (MPN,
  distributor code) is mirrored on the footprint.
- **Placement is the agent's job when an auto-placer would fail.** `Board.place(ref, x, y, rot, side)` takes explicit,
  reasoned coordinates (power stages, connectors, high-speed escapes, fine-pitch fan-out, current paths); the comment on
  each line says why. Do not loop blind reruns of an auto-placer.
- **The CPL uses KiCad's rotations.** Assembly houses define 0 degrees per package differently; check the assembly
  preview before paying and put any offset in the board script.

## 3. Freerouting setup and flags

Freerouting 2.1.0 (GPL-3.0, a Java 21 application) is the router. The pipeline finds it through `HIL_FREEROUTING_JAR` and
Java through `HIL_JAVA` / `JAVA_HOME` / `PATH`. Without the jar, the run stops at step 7 with "router not installed" and
writes no fab files (`place` mode needs no router).

The command the pipeline runs:

```
java -Djava.io.tmpdir=<out>/.router -Duser.home=<out>/.router -Xmx2g -jar freerouting.jar
     -de <board>.dsn -do <board>.ses -mp <passes> -mt <threads>
     --gui.enabled=false --profile.allow_telemetry=false --usage_and_diagnostic_data.disable_analytics=true
```

- `-Duser.home` and `-Djava.io.tmpdir` keep Freerouting's settings, logs and its 60-odd temp files out of the profile.
- Freerouting 2.1.0's own defaults (seen in the `freerouting.json` it writes on first start) are `allow_telemetry: true`,
  a GUI, and up to 31 optimizer threads; it also checks for a newer version on start. The pipeline seeds
  `freerouting.json` with telemetry, analytics and GUI off and the thread cap applied.
- The process runs at BelowNormal priority (Windows), streams its output into `<out>/.router/freerouting.log`, has a hard
  timeout (`router_timeout_s`, default 1800 s), and is killed (its own handle only) when the log has not grown for
  10 minutes.
- The DSN's first line holds the absolute board path; the pipeline rewrites it to the base name, so no machine path ends
  up in a generated file.
- Planes: give a pour net its own net class and let the pour connect it, or route it; the example routes everything and
  adds the GND pours afterwards.

## 4. Determinism

| What | How | Verified on the example board |
|---|---|---|
| string hashing | the interpreter re-executes itself once with `PYTHONHASHSEED=0` (`HIL_NO_REEXEC=1` to skip) | |
| schematic UUIDs | uuid5 of (board name, item key) | `.kicad_sch` and `.kicad_pro` byte-identical across two runs |
| board KIIDs | `pcbnew.KIID.SeedGenerator(seed)` before any object is created | |
| routing | Freerouting 2.1 is NOT bit-reproducible, even with one thread: two runs gave two different, equally clean track sets. A successful route is therefore frozen as `<board>_route.ses` next to the outputs and re-imported on later runs; `reroute` runs the router again | two runs from the frozen route: `.kicad_pcb` byte-identical, every Gerber / drill / CSV content hash identical |
| fab files | KiCad stamps the export time into Gerber and drill headers, so raw SHA-256s change every run. The results JSON carries both the raw SHA-256 of every fab file and a content hash with the time-stamp lines left out (`fab_content_sha256`) | content hashes identical across runs |
| fab zip | members written with a fixed time stamp | |

Commit the frozen SES together with the generator. A placement change that breaks it shows up as unconnected items in
the final DRC; rerun with `reroute` and commit the new SES.

## 5. Home-directory isolation

Every kicad-cli call and every `import pcbnew` reads and writes KiCad's per-user settings, documents and cache. A pipeline
that runs hundreds of times must not touch the profile (it is the GUI's, it fills up, and parallel runs corrupt one shared
settings file). `kicad_env.isolate()`, called before `import pcbnew`:

- points `KICAD_CONFIG_HOME`, `KICAD_DOCUMENTS_HOME`, `KICAD_CACHE_HOME` at `<repo>/.tmp/kicad_home/{config,documents,cache}`
  (override with `HIL_KICAD_HOME`) and `TEMP` / `TMP` at its `tmp/` (wx writes temp files during DSN export);
- seeds `kicad_common.json` with `"git": {"enableGit": false}`. Observed on KiCad 10.0.6: the first kicad-cli run writes a
  `git` section with `enableGit: true` and a 5-minute update interval into `kicad_common.json`; a scripted run has no use
  for KiCad tracking repository status;
- `seed_lib_tables()` writes global `fp-lib-table` / `sym-lib-table` files that list only the stock libraries the board
  references, so a CLI start never enumerates every stock library;
- refuses a KiCad home inside the user profile (the OS temp folder excepted).

`*.kicad_prl` (per-user view state that kicad-cli writes next to the project) is deleted at the end of every run and is
never committed.

## 6. Hard lessons (each one cost a day)

- **Never `Remove()` items under a live SWIG wrapper.** `fp.Remove(item)` / `board.Remove(item)` frees the C++ object while
  Python still holds a wrapper; KiCad's heap is corrupted and later `FootprintLoad` calls return bare `SwigPyObject`s
  ("not iterable"). Move unwanted shapes to a fab layer (in the footprint text, before loading) instead. The only removals
  in the pipeline are router remnants, done after the last `FootprintLoad` of the process with a fresh item list per
  round. Never "fix" the symptom with retries.
- **KiCad assert dialogs hang headless runs.** A layer-less `PCB_VIA` width call can raise an assert dialog that nobody
  will ever click. Pass the layer argument (`via.SetWidth(pcbnew.F_Cu, w)`).
- **No log growth for 10 minutes = hung.** Every long child process streams into a log; the watcher kills it (by its own
  PID) when the log stops growing, and the run reports it.
- **Hand placement is expected.** When an auto-placer or the router does not converge after one cause-level fix, the agent
  places the critical parts by hand with explicit, reasoned coordinates in the generator, and hand-routes or
  polygon-builds the critical nets; then the tools finish the rest.
- **More layers are an allowed cause-level fix, but cost rises fast.** Try placement and floorplan fixes first; when adding
  layers, report the fab price delta (2 -> 4 -> 6 layers at the build quantity) so the owner sees the trade.
- **Relaxing a rule needs a cited basis** (a DESIGN_STANDARDS row, an IPC table, the fab capability page); otherwise move
  copper.
- **Never demote a DRC severity.** The pipeline never edits `rule_severities`; 0 / 0 / 0 / 0 means at KiCad's defaults.
- **Never commit `*.kicad_prl`** (per-user state) or a file over 50 MB.
- **Stock 0 is a warning, a mismatch is a failure.** Stock moves daily; a code that resolves to a different MPN assembles
  the wrong part. Real example from building this pipeline: LCSC C25804 is `0603WAF1002T5E` (10 k, 0603), not the 0402
  part it was assumed to be; `distributor_verify.py` reported the mismatch and the board picked the right MPN.

## 7. Protection rules the method always applies

From `pcb/DESIGN_STANDARDS.md`; the example board shows each one.

| Rule | What | In the example |
|---|---|---|
| PROT-01 | no one-time fuses | none on the board |
| PROT-02 | eFuse FLT / PG to firmware | (no eFuse on this board) |
| PROT-03 | PTC with a sense line | F1 PTC, R2 / R3 divider to header pin 5 (VSENSE) |
| RED-01 | two or more contacts per power / ground net per connector | J2: +3V3 on pins 1 and 2, GND on 3, 4 and 6 |
| DER-01 | derating | 16 V / 10 V ceramics on 5 V / 3.3 V rails |
| TVS-01 | TVS on connector power pins | D1 SMAJ6.0CA across J1 |
| TVS-02 | every part behind a TVS rated above its clamp (machine check in step 1) | D1 clamps at 10.3 V: U1 TPS70933 IN rated 32 V, C1 16 V, F1 24 V; EN on the 22 k : 10 k divider sees 3.2 V of its 7 V |
| EMC-02 | return-path stitching | four locked GND stitching vias between the two pours |
| EMC-03 | hot-loop rule (switchers) | C1 / C2 placed at U1's pins (linear regulator, same placement discipline) |

## 8. Running the example

```
set HIL_FREEROUTING_JAR=<tools dir>\freerouting\freerouting-2.1.0.jar
"C:\Program Files\KiCad\10.0\bin\python.exe" pcb\example_board\gen_example_board.py --out .tmp\example_board
```

Numbers from the test run on 2026-11-03 (KiCad 10.0.6, Freerouting 2.1.0, Java 21):

| Step | Result |
|---|---|
| design | 11 parts, 6 nets, 0 problems; TVS-02: VIN / VIN_PROT worst 10.3 V, VSENSE worst 3.22 V, every rated pin above |
| schematic | 11 symbols, 2 power flags (VIN_PROT, GND) |
| ERC | 0 errors, 0 warnings |
| placement DRC | 0 errors, 0 warnings, 0 parity; 26 unconnected before routing; 4 `via_dangling` on the locked stitching vias (expected before the pours; set aside by count, never by type alone) |
| routing | Freerouting 2.1, 1 thread, 40 passes max, 5.6 s; 2 remnant segments removed |
| final DRC | 0 errors, 0 warnings, 0 unconnected, 0 parity; routed 100 % |
| distributor | every purchased line verified live against LCSC (11 on the 2026-11-04 re-run after the TVS-02 fix), 0 mismatch, 0 unverified |
| fab | 15 files (9 Gerbers + job file, drill, pos, CPL, BOM, zip) + STEP (865 kB) |
| whole run | 21 s with the router, 18 s from the frozen route |

Also tested: an MPN changed to a wrong value -> "distributor verification did not pass (mismatch 1): no fab files
written", exit 1; distributor unreachable -> line "unverified", exit 1, and with `--offline-ok` exit 0 at the verifier
but still no fab files from the pipeline.

The results JSON (`<board>_results.json`, schema `hil-board-results/1`) holds: `steps.library`, `design`, `schematic`,
`erc`, `board`, `placement_drc`, `route` (router, passes, threads, seconds, SES hash), `cleanup` (removed remnants, pours),
`final_drc` (errors, warnings, unconnected, parity, per-type counts, routed %), `distributor` (every line with the MPN the
distributor returned, brand, package, stock), `fab.files` (SHA-256 per file), `fab_content_sha256`, `step`, plus
`verdict` (green / red / placed), `kicad` version, `seconds` and `error` when red.

## 9. Roadmap

- **Standards checker** (`pcb/tools/standards_check.py`): implement the remaining machine rows of DESIGN_STANDARDS.md
  (PROT-01..03, RED-01, DER-01, TVS-01, EMC-02/03/04; TVS-02 is already in the pipeline's design step) over the board file and the BOM, printing rule ids; report-only first, then a CI
  gate once the boards are clean.
- **Completion router** for what Freerouting cannot do: fine-pitch (0.4-0.5 mm) escapes where its maze search fails, with
  clearance-exact A* and the frozen-route mechanism for the result.
- **BGA fan-out helpers**: dog-bone and via-in-pad patterns generated from the ball map, before the router runs.
- **Panelization** for assembly (rails, fiducials, mouse bites) as a pipeline step after the fab set.
- **Price check**: query the fab's quote for 2 / 4 / 6 layers at the build quantity so the layer-count trade is a number.
