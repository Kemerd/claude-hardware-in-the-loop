#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 build_enclosure.py - a parametric sealed electronics enclosure, written as a reference builder for the method:
                      parameters at the top, one function per part, plug-ins from parts/, mass / CG / checks, and the
                      exports the twin, the viewer and the decision renders read (STEP + STL + parts.json + cad.json).

 Interpreter: the CadQuery virtual environment (HIL_CQ_PYTHON from scripts/setup_tools.ps1).
 Run: <cq python> -I cad/examples/enclosure/build_enclosure.py [--out DIR]   (default out: this folder's out/)

 The design (each choice is a project decision from the onboarding interview, recorded in DESIGN below):
   - base tray with a lip on its top edge and a ledge inside the lip; the lid sits INSIDE the lip on the ledge;
   - the gasket lives in a groove on the ledge (added by parts/gasket.py as a plug-in, so the builder core stays
     untouched when the seal changes);
   - M2.5 heat-set inserts at the four corners: bosses are grown to the insert's length plus a floor;
   - one cable gland hole through the end wall (a keep-out in front of it checks that nothing blocks the gland nut).
 Frame: X along the length, Y across, Z up, origin at the centre of the base floor's underside, mm.
=======================================================================================================================
"""
import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))   # cad/ for cad_plugins

import cadquery as cq                                                  # noqa: E402
import cad_plugins as cp                                               # noqa: E402

# ---------------------------------------------------------------------------------------------------------------------
#  Parameters (mm) and the project decisions they come from
# ---------------------------------------------------------------------------------------------------------------------
DESIGN = {
    "material": "PETG",                 # interview Q5 default for a first prototype
    "fastening": "M2.5 brass heat-set inserts, thread locker",   # Q6
    "enclosure": "sealed, gasket in a groove on the ledge, lid inside the lip",   # Q7
}
L, W, H = 110.0, 70.0, 38.0             # outside of the base tray
WALL, FLOOR = 2.4, 2.0                  # wall and floor thickness
LIP_H, LEDGE_W = 4.0, 2.2               # the lip above the ledge, the ledge width the lid rests on
LID_T = 2.4                             # lid thickness (sits inside the lip on the ledge)
INSERT_D, INSERT_L = 3.6, 5.7           # M2.5 heat-set insert: install hole diameter and length (vendor drawing class)
BOSS_D = INSERT_D + 2 * 2.0             # 2 mm of wall around the insert hole
BOSS_FLOOR = 1.0                        # material under the insert
GLAND_D = 12.5                          # M12 cable gland clearance hole
CORNER = 6.0                            # boss centre inset from the outside corner
# densities in g/cm^3 (datasheet class values for the named materials)
DENS = {"PETG": 1.27, "EPDM": 1.15, "brass": 8.5}


def base():
    """Tray: outer box, hollowed to the ledge height, then the lip pocket above the ledge, bosses, gland hole."""
    ledge_z = H - LIP_H
    body = cq.Workplane("XY").box(L, W, H, centered=(True, True, False)).edges("|Z").fillet(4.0)
    # inner cavity up to the ledge (walls WALL + LEDGE_W thick below the ledge)
    cav = (cq.Workplane("XY").workplane(offset=FLOOR)
           .rect(L - 2 * (WALL + LEDGE_W), W - 2 * (WALL + LEDGE_W)).extrude(ledge_z - FLOOR))
    # lip pocket above the ledge (walls WALL thick): the lid drops into it and rests on the ledge
    lip = cq.Workplane("XY").workplane(offset=ledge_z).rect(L - 2 * WALL, W - 2 * WALL).extrude(LIP_H + 1.0)
    body = body.cut(cav).cut(lip)
    # corner bosses from the floor to the ledge, each with the insert hole from the top
    pts = [(sx * (L / 2 - CORNER), sy * (W / 2 - CORNER)) for sx in (-1, 1) for sy in (-1, 1)]
    boss = cq.Workplane("XY").workplane(offset=FLOOR).pushPoints(pts).circle(BOSS_D / 2).extrude(ledge_z - FLOOR)
    body = body.union(boss)
    holes = cq.Workplane("XY").workplane(offset=ledge_z - INSERT_L).pushPoints(pts).circle(INSERT_D / 2).extrude(INSERT_L + 0.1)
    body = body.cut(holes)
    # cable gland hole through the +X end wall, centred at mid height of the cavity
    gland = (cq.Workplane("YZ").workplane(offset=L / 2 - WALL - LEDGE_W - 1.0)
             .center(0, FLOOR + (ledge_z - FLOOR) / 2).circle(GLAND_D / 2).extrude(WALL + LEDGE_W + 2.0))
    return body.cut(gland)


def lid():
    """Lid plate that sits inside the lip on the ledge, with clearance holes over the inserts."""
    ledge_z = H - LIP_H
    gap = 0.25                                                   # fit clearance per side inside the lip
    plate = (cq.Workplane("XY").workplane(offset=ledge_z)
             .rect(L - 2 * WALL - 2 * gap, W - 2 * WALL - 2 * gap).extrude(LID_T))
    pts = [(sx * (L / 2 - CORNER), sy * (W / 2 - CORNER)) for sx in (-1, 1) for sy in (-1, 1)]
    return plate.faces(">Z").workplane().pushPoints(pts).hole(2.9)   # M2.5 clearance


def mass_row(name, wp, mat, color):
    """parts.json row: mass from volume x density (or a catalogue mass), centre of mass in mm."""
    solid = wp.val()
    vol_mm3 = solid.Volume()
    com = solid.Center()
    mass = mat if isinstance(mat, float) else vol_mm3 / 1000.0 * DENS[mat]
    return {"mass_g": round(mass, 3), "material": mat if isinstance(mat, str) else "catalogue",
            "com_mm": [round(com.x, 3), round(com.y, 3), round(com.z, 3)], "volume_mm3": round(vol_mm3, 1),
            "color": list(color)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reference enclosure builder")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    a = ap.parse_args(argv)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.join(out, "stl"), exist_ok=True)
    os.makedirs(os.path.join(out, "step"), exist_ok=True)

    # ---- the builder's own parts
    own = [("base", base(), "PETG", (0.30, 0.32, 0.36)), ("lid", lid(), "PETG", (0.55, 0.57, 0.62))]
    ctx = {"L": L, "W": W, "H": H, "WALL": WALL, "LIP_H": LIP_H, "LEDGE_W": LEDGE_W, "LID_T": LID_T,
           "ledge_z": H - LIP_H, "gland_d": GLAND_D, "parts": {n: wp for n, wp, _, _ in own}}

    # ---- plug-ins: cuts into builder parts first, then their parts, keep-outs, contacts, checks
    mods = cp.load(os.path.join(HERE, "parts"), "enclosure_parts")
    names = {n for n, _, _, _ in own}
    for stem, target, wp in cp.cuts(mods, ctx):
        if target not in names:
            raise SystemExit(f"plug-in {stem}: cut target {target!r} is not a builder part")
        own = [(n, w.cut(wp) if n == target else w, m, c) for n, w, m, c in own]
    ctx["parts"] = {n: wp for n, wp, _, _ in own}
    extra = cp.parts(mods, ctx, existing=names)
    allparts = own + extra
    contacts = cp.contacts(mods)

    # ---- checks: keep-outs clear of every part, no unplanned interference between parts
    problems = []
    for stem, kname, kwp in cp.keepouts(mods, ctx):
        for n, wp, _, _ in allparts:
            v = kwp.intersect(wp).val().Volume() if kwp.intersect(wp).solids().size() else 0.0
            if v > 1e-3:
                problems.append(f"keep-out {kname} ({stem}) overlaps {n} by {v:.2f} mm3")
    for i in range(len(allparts)):
        for j in range(i + 1, len(allparts)):
            a_n, a_w = allparts[i][0], allparts[i][1]
            b_n, b_w = allparts[j][0], allparts[j][1]
            if frozenset((a_n, b_n)) in contacts:
                continue
            inter = a_w.intersect(b_w)
            v = inter.val().Volume() if inter.solids().size() else 0.0
            if v > 1e-3:
                problems.append(f"{a_n} intersects {b_n} by {v:.2f} mm3")
    plug = cp.checks(mods, {**ctx, "built": {n: wp for n, wp, _, _ in allparts}})
    ok = not problems and all(r["pass"] for r in plug.values())

    # ---- exports: STEP + STL per part, parts.json, enclosure_cad.json
    rows = {}
    for n, wp, mat, col in allparts:
        cq.exporters.export(wp, os.path.join(out, "stl", f"{n}.stl"), tolerance=0.05, angularTolerance=0.2)
        cq.exporters.export(wp, os.path.join(out, "step", f"{n}.step"))
        rows[n] = mass_row(n, wp, mat, col)
    total = sum(r["mass_g"] for r in rows.values())
    cg = [round(sum(r["mass_g"] * r["com_mm"][k] for r in rows.values()) / total, 3) for k in range(3)]
    with open(os.path.join(out, "parts.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"frame": "X length, Y across, Z up, origin floor underside centre, mm", "parts": rows,
                   "totals": {"mass_g": round(total, 3), "cg_mm": cg}}, fh, indent=1)
    with open(os.path.join(out, "enclosure_cad.json"), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"design": DESIGN, "outline_mm": [L, W, H], "ledge_z_mm": H - LIP_H,
                   "inserts": {"hole_d_mm": INSERT_D, "length_mm": INSERT_L},
                   "checks": {"pass": ok, "problems": problems, "plugins": plug}}, fh, indent=1)
    print(f"[enclosure] {len(allparts)} parts, {total:.1f} g, CG {cg} mm, checks {'PASS' if ok else 'FAIL'}")
    for p in problems:
        print(f"  {p}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
