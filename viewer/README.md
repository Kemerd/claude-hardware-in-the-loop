# Web CAD viewer

A single-page three.js viewer for CadQuery builds: section planes, x-ray, parts by mass, a live mass / CG panel, a
materials panel, optional PCB and wiring-harness overlays, and review notes pinned on parts. It runs from any static
host, or locally through `serve.py`, which also stores the notes in the repo where the next CAD session reads them.

| File | What it does |
|---|---|
| `index.html` | the viewer (three.js 0.160 from jsDelivr, no build step) |
| `pack_mesh.py` | packs a build's per-part STLs + `parts.json` into `mesh.json` + `mesh_<k>.json` |
| `serve.py` | local host for the page, the payload, the overlays and the notes API (stdlib only) |
| `pcb_to_json.py` | exports a `.kicad_pcb` to the board JSON the PCB overlay draws (KiCad 10 python) |

## Run it

```
# 1. pack a build (CadQuery venv or any python with trimesh + numpy)
python viewer/pack_mesh.py cad/<assembly>/out .tmp/viewer --label "Build 7" --about "lid ribs, new standoffs" \
       --baseline <previous build>/parts.json --pcb .tmp/viewer_in/main_board.json --harness cad/<assembly>/out/wires.json

# 2a. look at it locally, notes go to docs/cad_feedback/notes.jsonl
python viewer/serve.py --mesh .tmp/viewer --port 8765          # then open http://127.0.0.1:8765/

# 2b. or publish index.html + mesh.json + mesh_*.json + the overlay JSON files to any static host
#     (notes then stay in the browser's localStorage, marked "this device only")
```

Board overlay (run under KiCad's python; it isolates KiCad's per-user folders in `<repo>/.tmp/kicad_home` or `--kicad-home`):

```
"C:\Program Files\KiCad\10.0\bin\python.exe" viewer\pcb_to_json.py pcb\main\kicad\main.kicad_pcb ^
        --out .tmp\viewer_in\main_board.json --place "0,0,12.5,90,top"
```

`--place x,y,z,rot_deg,side`: the board's origin (outline bounding-box centre, top copper face) goes to `x,y,z` in the
model frame, the board turns `rot_deg` about model Z, and `side` (`top` / `bottom`) faces +Z. Without `--place` the
board sits at the model origin and the Overlays panel says "unplaced".

Overlays can also be named in the URL: `index.html?pcb=a.json,b.json&harness=wires.json` (plain file names next to the
page; `serve.py` only serves names that `mesh.json` lists or that are passed with `--overlay`).

## What the panels do

* **Parts**: every part, heaviest first; filter by name or material; quick-filter buttons are the heaviest materials of
  this build; shift-click or double-click hides a part; "new in ..." glows parts that are new (orange) or reshaped (blue)
  against `--baseline` / `--changed`.
* **Materials**: part count and summed mass per material, with a bar for its share of the mass; a tap filters the parts.
* **Sections**: three clip planes along the model axes (ranges fit the model), x-ray, fullbright, reset, show all.
* **Mass**: total mass, CG (x, y, z) and massless count of the VISIBLE parts, recomputed on every visibility change, next
  to the all-parts figure and the builder's own `totals` from `parts.json`; a red CG marker is drawn in the scene. Hide
  a sub-assembly to read the CG of the rest.
* **Overlays**: PCB on/off and top / bottom footprint sides; harness on/off with every run's spec and length. Tapping a
  footprint shows ref, value, footprint, side, position and courtyard size; tapping a wire shows its spec, ends, colour,
  diameter and length.
* **Notes**: a note stores the text, the part, the tapped point (model mm) and the whole view (camera, planes, hidden
  parts, toggles). Numbered pins mark notes in 3-D; a tap on a note restores its view.

On a phone the panels open one at a time above the bottom tab bar; on desktop several stay open in two columns.

## Data formats

### parts.json (input of pack_mesh.py, written by the CadQuery builder)

```json
{"parts": {"cover": {"mass_g": 293.4, "material": "PETG", "com_mm": [0, 0, 25], "color": [0.2, 0.4, 0.8], "link": "body"}},
 "totals": {"mass_g": 401.5, "cg_mm": [0, 0, 18.9]},
 "frame": "model frame: X forward, Y left, Z up, mm"}
```

`mass_g` null = massless (keep-outs, context geometry). One STL per part at `<build>/stl/<name>.stl` (or
`--stl-subdir stl/<pose>`), optional `colors.json` there (`{name: [r, g, b]}`).

### Board JSON (pcb_to_json.py output, schema `hil-pcb/1`)

```json
{"schema": "hil-pcb/1", "board": "main", "thickness_mm": 1.6, "copper_layers": 4,
 "frame": "board mm, x right, y up seen from the top (F.Cu) side, origin = outline bounding-box centre",
 "origin_kicad_mm": [150.0, 100.0],
 "outline": [{"outer": [[-40, -25], [40, -25], [40, 25], [-40, 25]], "holes": []}],
 "footprints": [{"ref": "U1", "value": "MCU", "fpid": "QFN-48", "side": "top", "layer": "F.Cu", "x": 3.2, "y": -1.0,
                 "rot_deg": 90, "box": [0.1, -4.1, 6.3, 2.1], "box_source": "courtyard", "board_only": false,
                 "height_mm": 0.9}],
 "holes": [{"ref": "H1", "x": -36, "y": -21, "drill_mm": 3.2, "plated": false}],
 "place": {"origin_mm": [0, 0, 12.5], "rot_deg": 90, "side_up": "top"}}
```

`height_mm` per footprint is optional (a board pipeline's CAD-fit export has it); with it the courtyard becomes a box of
that height, without it a flat outline on the copper face.

### Wires JSON (harness overlay)

```json
{"runs": [{"id": "W1", "from": "J1.1", "to": "P2.3", "spec": "22 AWG ETFE", "colour": "red", "diameter_mm": 1.6,
           "points_mm": [[-50, 0, 6], [-20, 10, 8], [40, -5, 6]]}],
 "ties": [{"at_mm": [-20, 10, 8], "run_ids": ["W1"]}]}
```

`colour` takes CSS colours (`#c03030`, `red`) or the short wire codes `blk wht red grn yel blu org brn vio gry pnk`
(a striped `wht/blu` uses the first colour). Length is the polyline length through `points_mm` (the cut length basis
before service loops); the tube follows a centripetal Catmull-Rom curve through the same points. A tie ring is sized
to the bundle it holds and turned to the first run's tangent.

## Notes store

`serve.py` appends to `docs/cad_feedback/notes.jsonl` (in the folder above `viewer/`, or `--notes <file>`): one JSON
object per line, `{"op": "add", ...}` or `{"op": "delete", "id": ...}`, fsynced per note, never rewritten. The live set
is adds minus deletes. The CAD session reads it before the next build and answers each note in its report.

## Roadmap

These are designed, not built:

* **Copper layer by layer**: tracks, pads, vias and zones per layer from a pcbnew export (or the Gerbers through a
  Gerber-to-polygon step), drawn as thin extrusions with a layer picker, so an enclosure fit check sees real copper,
  not courtyard boxes.
* **Clearance checks in the page**: harness tubes against part meshes (BVH ray / sphere sweeps along each run), with
  the minimum gap per run listed and clashes painted red; the same for board courtyards against enclosure walls.
* **Bend-radius check** per run from the curve's curvature against the wire spec's minimum bend radius.
* **STEP import** through OCP in `pack_mesh.py` (tessellate per solid with a chord tolerance), so vendor STEP parts pack
  without an STL step.
* **Measure tool**: point-to-point, point-to-plane and edge length on the picked geometry, written into notes.
* **Exploded view** per sub-assembly (the `link` field), animated with a spring.
* **Build diff**: mass-property diff between two packed builds (mass, CG and inertia per part, the shift of the total
  CG), shown in the Mass panel, matching the before/after table the CAD method asks for.
