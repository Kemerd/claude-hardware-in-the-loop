# pcb/example_board - the pipeline's end-to-end example

`gen_example_board.py` declares a small, real board and runs it through `pcb/tools/board_pipeline.py`: a 2-layer
34 x 26 mm 3.3 V LDO breakout (JST PH 5 V input, SMAJ6.0CA TVS, 250 mA resettable PTC, AP2112K-3.3 LDO with its
datasheet capacitors, power LED, a VIN sense divider to the output header, a 2 x 3 output header with two 3.3 V and three
GND contacts, four M2 holes). It is small on purpose: every step of the pipeline runs in about 20 seconds.

```
"C:\Program Files\KiCad\10.0\bin\python.exe" pcb\example_board\gen_example_board.py place          # up to the placement DRC
set HIL_FREEROUTING_JAR=<tools dir>\freerouting\freerouting-2.1.0.jar
"C:\Program Files\KiCad\10.0\bin\python.exe" pcb\example_board\gen_example_board.py                # full run
"C:\Program Files\KiCad\10.0\bin\python.exe" pcb\example_board\gen_example_board.py reroute        # ignore the frozen route
"C:\Program Files\KiCad\10.0\bin\python.exe" pcb\example_board\gen_example_board.py --out <dir>    # outputs elsewhere
```

Exit 0 only with ERC 0 errors / 0 warnings, DRC 0 errors / 0 warnings / 0 unconnected / 0 schematic-parity items, and every
distributor code resolved live to the MPN the schematic names. Anything else stops the run before a fab file is written and
the reason is in `ldo_breakout_results.json` ("error").

## Outputs

| Path | What |
|---|---|
| `kicad/` | schematic, symbol library, project, lib tables, board, ERC / DRC reports (JSON + text), schematic PDF |
| `ldo_breakout_lib.pretty/` | the board's own footprint library (stock footprints localized, silkscreen on the fab layers) |
| `ldo_breakout_route.ses` | the frozen route (see docs/PCB.md, "Determinism") |
| `fab/` | Gerbers, Excellon drill, `CPL_ldo_breakout.csv`, `BOM_ldo_breakout.csv` (JLCPCB assembly columns), gerber zip, STEP |
| `ldo_breakout_results.json` | every count of every step, SHA-256 and content hash of every fab file, the verdict |
| `ldo_breakout_cad_fit.json` | outline, holes, per-part courtyard box and height for the mechanical CAD |

In your own project these outputs are committed together with the generator (they are the reviewed design). In the
template repository they are ignored by `example_board/.gitignore`, so a test run in place leaves nothing behind.

## Before ordering

The CPL uses KiCad's footprint rotations. Assembly houses define 0 degrees per package differently, so check every part's
orientation in the assembly preview of the order page before paying; correct it in the board script (a rotation offset
per footprint), never by editing the CSV by hand.
