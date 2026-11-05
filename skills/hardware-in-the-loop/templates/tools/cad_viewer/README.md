# Web CAD viewer

A single-page three.js viewer for CadQuery builds, copied into a project by `scripts/scaffold.py` (answer `cad_viewer`
yes in the interview; it is on by default). Section planes with a stepper, X-ray, PCB X-ray, Wire X-ray, parts by mass,
a live mass / CG panel, a materials panel, per-part material looks, KiCad board pictures laid on the board parts,
display-surface pictures, optional PCB and wiring-harness overlays, and review notes pinned on parts. It runs from any
static host, or locally through `windows/cad_viewer.ps1`, which also stores the notes in the repo where the next CAD
session reads them. The UI follows Apple's Human Interface Guidelines: translucent glass panels, a bottom tab bar, spring
animations, safe-area insets, larger touch targets on a phone, and it lays out on desktop (two columns), tablet (narrower
columns) and phone (one sheet at a time above the tab bar).

| File | What it does |
|---|---|
| `index.html` | the viewer (three.js 0.160 from jsDelivr, no build step) |
| `viewer.config.json` | everything project-specific, one small file (see "The config") |
| `viewer_config.py` | loads and validates the config; `python tools/cad_viewer/viewer_config.py` checks it |
| `pack_mesh.py` | packs a build's per-part STLs + `parts.json` into `mesh.json` + `mesh_<k>.json` |
| `render_boards.py` | optional: KiCad renders of the boards (needs KiCad 10 and Pillow) |
| `board_textures.py` | optional: lays those renders on the board parts, with the outline and silhouette gates |
| `serve.py` | local host for the page, the payload, the overlays and the notes API (stdlib only) |
| `pcb_to_json.py` | optional: exports a `.kicad_pcb` to the board JSON the courtyard overlay draws (KiCad 10 python) |
| `windows/cad_viewer.ps1` | `pack`, `start`, `stop`, `status`: one command each |

A mesh-only build is the minimum: `parts.json` + STLs, nothing else. No KiCad, no Pillow, no scipy, no boards and no screen
image are needed for the viewer to come up. Each extra switches on only when you give it its input.

## Run it

```
# 1. pack a build (CadQuery venv or any python with trimesh + numpy)
pwsh -File windows\cad_viewer.ps1 pack cad\<assembly>\out "Build 7" --about "lid ribs, new standoffs" --baseline <previous build>\parts.json

# 2. look at it locally, notes go to docs/cad_feedback/notes.jsonl
pwsh -File windows\cad_viewer.ps1 start            # http://127.0.0.1:8765/ ; stop and status do what they say
```

Without PowerShell: `python tools/cad_viewer/pack_mesh.py <build> <out> --label "Build 7"` then
`python tools/cad_viewer/serve.py --mesh <out>`. To publish, put `index.html` + `mesh.json` + `mesh_*.json` + the overlay
JSON files on any static host (notes then stay in the browser's localStorage, marked "this device only").

Add the extras to `pack` as flags:

```
# board pictures: render once (cached by file time and size), then pack with them
python tools/cad_viewer/render_boards.py --out .tmp/viewer_boards [--max-px 2048]
pwsh -File windows\cad_viewer.ps1 pack <build> "Build 7" --boards .tmp/viewer_boards [--boards-report r.json --boards-sheet sheet.png]

# a screen with a picture on it
pwsh -File windows\cad_viewer.ps1 pack <build> "Build 7" --screens docs/screens.json

# the courtyard overlay of a board (KiCad python) and a harness
"C:\Program Files\KiCad\10.0\bin\python.exe" tools\cad_viewer\pcb_to_json.py pcb\main\kicad\main.kicad_pcb --out .tmp\viewer_in\main_board.json --place "0,0,12.5,90,top"
pwsh -File windows\cad_viewer.ps1 pack <build> "Build 7" --pcb .tmp/viewer_in/main_board.json --harness cad/<assembly>/out/wires.json
```

`--place x,y,z,rot_deg,side`: the board's origin (outline bounding-box centre, top copper face) goes to `x,y,z` in the model
frame, the board turns `rot_deg` about model Z, and `side` (`top` / `bottom`) faces +Z. Without `--place` the board sits
at the model origin and the Overlays panel says "unplaced". Overlays can also be named in the URL:
`index.html?pcb=a.json,b.json&harness=wires.json` (plain file names next to the page).

## The config

`viewer.config.json` holds every project-specific fact; the scripts and the page know no part name. Keys that start with an
underscore are comments, so the shipped file documents itself. Check yours with `python tools/cad_viewer/viewer_config.py`.

| Key | What it decides |
|---|---|
| `title` | the page title |
| `skip_parts` | regexes of parts left out of the pack (and of its mass / CG) |
| `look`, `look_defaults` | rules `{class, name?, material?, text?}` that give a part its look; yours first, then the built-in material words |
| `coat_prefix` | conformal-coat shells are parts named `<prefix><board part>`; they render clear |
| `board_name_regex` | which parts PCB X-ray keeps solid, with or without a KiCad file |
| `wire_name_regex`, `wire_not_regex` | which parts Wire X-ray keeps solid, and the false hits to drop (screws, clamps) |
| `boards` | CAD part name to its repo-relative `.kicad_pcb`; parts of one file share one render |
| `board_no_file` | board-like parts with no KiCad file, listed in the pack with the reason |
| `board_frames` | the CAD builder's own rule for how a board picture sits on its part; breaks ties on symmetric boards |
| `board_gate` | outline tolerance, minimum silhouette IoU and tie width a render must pass |

Look classes: `coat fr4 copper alu ti steel print cf soft film glass wire plain`. A rule matches when every regex it names
matches; `text` is searched in `"<name> <material>"`.

Display surfaces (`--screens`): `{"surfaces": [{"name", "part"?, "centre_mm", "normal", "u_axis", "v_axis", "size_mm",
"image", "panel": {"active_area_mm", "active_area_centre_mm"}?}]}`; `image` is a PNG next to the JSON (over 2 MB it is
refused and the surface stays a dark panel).

## What the panels do

* **Parts**: every part, heaviest first; filter by name or material; quick-filter buttons are the heaviest materials of
  this build; shift-click or double-click hides a part; "new in ..." glows parts that are new (orange) or reshaped (blue)
  against `--baseline` / `--changed`.
* **Materials**: part count and summed mass per material, with a bar for its share of the mass; a tap filters the parts.
* **Sections**: three clip planes along the model axes. Each plane has a value field, a stepper and a slider. Stepper or
  wheel over the slider: 0.1 mm, Shift 1 mm, Alt 0.01 mm. Holding the stepper runs at 12 steps a second after 0.38 s. The
  up and down arrows work in the value field. Nudging a plane that is off turns it on. Values keep two decimals.
  Also here: X-ray, fullbright, reset, show all.
* **PCB X-ray** leaves every board solid with its KiCad render and takes the rest to glass; conformal-coat shells go fully
  clear so they do not film the copper. A board with no `.kicad_pcb` stays a plain CAD box. `render_boards.py --max-px 2048`
  gives renders that read tracks when zoomed in. **Wire X-ray** does the same for every wire, cable, flex tail and harness
  tie, by part name (`wire_name_regex` / `wire_not_regex`). X-ray, PCB X-ray and Wire X-ray are one choice: pressing one
  releases the others. A button is dimmed when the pack holds no part of its kind.
* **Board pictures**: both faces of a board part carry the KiCad render, laid 0.03 mm over the substrate, clipped by the
  section planes, hidden with the part, lit by fullbright. A render is laid on a part only if the part's STL outline matches
  the board file's Edge.Cuts size and the silhouette IoU of the best placement reaches `min_iou`; a CAD board that differs
  from its PCB stays plain and the pack lists the numbers. A symmetric board fits several poses; the pick panel then says
  "orientation unverified" unless `board_frames` settles it. The images decode lazily after the first frame.
* **Mass**: total mass, CG (x, y, z) and massless count of the VISIBLE parts, recomputed on every visibility change, next
  to the all-parts figure and the builder's own `totals` from `parts.json`; a red CG marker is drawn in the scene. Hide
  a sub-assembly to read the CG of the rest.
* **Overlays**: PCB on/off and top / bottom footprint sides; harness on/off with every run's spec and length. Tapping a
  footprint shows ref, value, footprint, side, position and courtyard size; tapping a wire shows its spec, ends, colour,
  diameter and length.
* **Notes**: a note stores the text, the part, the tapped point (model mm) and the whole view (camera, planes, hidden
  parts, toggles). Numbered pins mark notes in 3-D; a tap on a note restores its view.
  `index.html#view=<url-encoded JSON in the view format of a note>` opens the page on a saved state.

Part looks come from the packer (`cls` per part), so a metal part reflects a generated studio room, a printed part gets a
fine grain and a lens is glass, without a single image file. If the room add-on cannot load, the viewer falls back to flat
metals.

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

`serve.py` appends to `docs/cad_feedback/notes.jsonl` (two folders above `tools/cad_viewer/`, or `--notes <file>`): one JSON
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
