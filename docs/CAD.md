# CAD: parametric builders, plug-ins, weight and balance, decision images

## 1. Builders

One CadQuery builder per assembly: `cad/<assembly>/build_<assembly>.py`. Parameters at the top with their source,
one function per part, then checks and exports. The builder is the design; nobody edits an exported STEP by hand.

Exports (all read 1:1 by the twin, the sims and the viewer):

| File | Contents |
|---|---|
| `stl/<part>.stl`, `step/<part>.step` | geometry per part, assembly frame, mm |
| `parts.json` | `{"parts": {name: {"mass_g", "material", "com_mm", "volume_mm3", "color"}}, "totals": {"mass_g", "cg_mm"}, "frame"}` |
| `<assembly>_cad.json` | design decisions, key dimensions, outline, checks with their results |
| `articulation.json` | joints (parent, child, axis, origin, limits) for the twin and USD (when the assembly moves) |

Reference: `skills/hardware-in-the-loop/templates/cad/examples/enclosure/build_enclosure.py` builds a sealed enclosure
(tray with lip and ledge, lid inside the lip, heat-set insert bosses, gland hole), loads two plug-ins, checks
interference and keep-outs, and exports everything above. It runs in about two seconds:

```
<cq python> -I cad/examples/enclosure/build_enclosure.py --out .tmp/enclosure
[enclosure] 3 parts, 110.2 g, CG [-0.343, 0.0, 18.815] mm, checks PASS
```

## 2. The plug-in contract (`cad/cad_plugins.py`)

When several people or agents add parts to one assembly, editing the builder core turns every addition into a merge
conflict. A part is a FILE dropped into `cad/<assembly>/parts/`:

```python
def build(ctx):            # required: [(name, workplane, material, color)]
    return [("lid_gasket", ring, "EPDM", (0.08, 0.08, 0.09))]
def keepouts(ctx):         # optional: [(name, workplane)] that must stay clear; never exported, no mass
def cuts(ctx):             # optional: [(builder_part, workplane)] material removed from a builder part
CONTACTS = [("lid_gasket", "base")]   # optional: designed contacts the interference check accepts
def checks(ctx):           # optional: {"pass": bool, ...} AND-ed into the builder's pass
```

- `material` is a key of the builder's density table (mass = volume x density) or a catalogue mass in grams.
- Modules load in sorted file-name order (deterministic); files starting with `_` are helpers.
- A broken plug-in fails the build loudly: import error, a malformed row, a name collision with a builder part or
  another plug-in. A plug-in can never silently replace a real part.
- `ctx["plugin_parts"]` lets a later plug-in see the parts of earlier ones (for example a fastener plug-in that adds
  thread locker to every screw).

## 3. No stand-ins

A block presented as a part is the most common way CAD lies. The audit (`tools/audit_placeholders.py`) flags plug-in
parts whose geometry is one bare primitive with only transforms, and part names that announce a stand-in (`_ref`,
`proxy`, `envelope`, ...). A bought part is modelled from its vendor drawing to the level that matters for fit, mass
and the sims (envelope, mounting features, connector positions); a keep-out volume is declared as a keep-out, never
as a part.

## 4. Weight and balance on every part change

Every part change comes with a table before and after, written by `cad/mass_cg_table.py`:

```
python cad/mass_cg_table.py <before parts.json> <after parts.json> --out docs/data/mass/<date>/<topic>.md
```

It lists total mass, CG and its shift (per axis and magnitude), point-mass inertia about the CG, and one row per added,
removed or changed part with mass and station before and after. Parts without mass data are listed, never guessed. Add
the builder's `checks()` result and the BOM mass. Then re-sync the twin parameters (they are generated from
`parts.json`) and commit the regenerated file with the CAD change that moved it.

## 5. Fit and clash decisions come with pictures

`cad/render.py` draws decision images: shaded multi-view PNGs (2400 x 1500 by default), clash parts in red with the
rest half-transparent, dimension lines with labels, a scale bar, a legend. Images go to
`docs/cad_decisions/<date>/<topic>/` with a short `README.md`: the question, the options, the numbers, the decision.

```
python cad/render.py --parts-json .tmp/enclosure/parts.json --stl-dir .tmp/enclosure/stl --clash-names lid_gasket \
       --dim "-55,-35,34:55,-35,34:110.0 mm" --title "Gasket seat" --out docs/cad_decisions/2026-11-03/gasket/seat.png
```

Renders use trimesh + matplotlib with the Agg backend: no display, no GPU, runs in CI and in detached night chains.

## 6. Materials and fastening are project decisions

The onboarding interview asks, and the answers go into the standing decisions:

| Decision | Typical options | Consequences to state |
|---|---|---|
| Printed material | carbon-fibre nylon (PA12-CF, PA612-CF): stiff, heat resistant, needs a hardened nozzle or a print service; PETG: tough and cheap; PLA: prototypes only (creeps under load and heat); machined 6061 | density for the mass model, wall thickness rules, insert hole sizes, cost per part |
| Fastening into plastic | brass heat-set inserts with thread locker (re-usable, strongest); thread-forming screws (cheap, few cycles); through-bolts and nuts | boss diameter and length (grow the boss outward for the insert length), assembly steps |
| Enclosure | sealed (gasket in a lip, lid inside the lip on a ledge, thermal path designed); vented; open frame | thermal budget, ingress rating, gasket compression check |
| Clearance | static gap, moving-part gap, cable bend room | interference check thresholds |

Write the decision into the builder's `DESIGN` dict so it travels with the geometry and shows up in `<asm>_cad.json`.

## 7. Viewing and review

The web viewer (`viewer/`) shows a builder export with section planes, x-ray, a parts list by mass, a live mass / CG
panel of the visible parts, a materials panel, a PCB overlay (from `pcb_to_json.py`) and a harness overlay (wire runs as
tubes with zip-tie points). `serve.py` adds review notes pinned on parts, stored in `docs/cad_feedback/notes.jsonl`
for the next agent to read. See [viewer/README.md](../viewer/README.md).

## 8. Running CAD safely

CadQuery chains can take minutes and gigabytes: run them through `slot.sh` (kind `cad`), detached when long, with the
output in `.tmp/` and only the exports committed.
