#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 place_labels.py - reference designators on the silkscreen, beside their part, one size per board, nothing overlapping
=======================================================================================================================
 Interpreter: KiCad 10's bundled python (it has pcbnew and numpy). shapely is the one extra package; the bundled python
              does not ship it. Install it into a folder of your own and point HIL_KICAD_SITE at that folder:

                  "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" -m pip install --target <dir> shapely

              (pip installs shapely's numpy requirement only if it is missing; KiCad already carries numpy.)

 Run:         python place_labels.py <board.kicad_pcb>       writes the board in place, exit 0 only when nothing overlaps

 What it does
   * Every reference designator (R1, C2, U3 ...) stays on F.SilkS / B.SilkS, centred, mirrored on the bottom face.
   * Stroke is fixed at LABEL_STROKE_MM, letters are LABEL_WIDTH_RATIO of their height wide.
   * The height is the largest of LABEL_HEIGHTS_MM at which silk_solver.py finds a place for every label with no
     overlap (bisection over the sizes, a few shuffled seeds per size). A roomy board keeps big text, a dense one shrinks.
   * A board-only footprint (a mounting hole, a fiducial) gets no label.
   * The project's min_text_height follows the size used, so KiCad's own DRC judges the board at what it was built to.
   * One line is printed per height tried; the exit code is nonzero when overlaps remain at the smallest size.

 Why the sizes are what they are (fab numbers as published; check the fab's page before an order)
   JLCPCB   its capabilities guide asks for 1.0 mm text at 0.15 mm stroke; its blog says 0.8 mm text prints.
   Eurocircuits   1.0 mm text and 0.17 mm stroke, quoted in a forum post, not in a data sheet.
   PCBWay, OSH Park   unofficial figures only, in the same range.
   Text under 0.8 mm may print soft: the strokes close up, 8, B and R go first. Treat the 0.6-0.75 mm steps as the
   last resort of a dense board, look at the fab's preview, and give the part more room if you can.

 The measure of success is what a person sees on the board, not the solver's score: the audit at the end is a plain
 box test of every printed label against every pad, hole, part body, other label and the board edge, run on the file
 as it was saved.
=======================================================================================================================
"""
from __future__ import annotations

import math
import os
import re
import sys
from typing import Any

# ---------------------------------------------------------------------------------------------------------------------
#  Windows consoles (cp1252) must never crash on a part name or path
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------------------------------------------------
#  Helper modules sit beside this file. kicad_env.isolate() must run BEFORE pcbnew is imported.
# ---------------------------------------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import kicad_env  # noqa: E402
kicad_env.isolate()

# shapely lives outside KiCad's python: HIL_KICAD_SITE names the folder it was installed into
_SITE = os.environ.get("HIL_KICAD_SITE", "")
if _SITE and os.path.isdir(_SITE) and _SITE not in sys.path:
    sys.path.insert(0, _SITE)

try:
    import numpy as np  # noqa: E402
    import pcbnew  # noqa: E402
    from shapely.geometry import Polygon, box  # noqa: E402
    from shapely.ops import unary_union  # noqa: E402
except ImportError as _e:                                   # a clear message beats a bare traceback in a CI log
    sys.exit(f"place_labels: missing module ({_e}). Run under KiCad's python; for shapely see the header "
             f"and set HIL_KICAD_SITE.")
import silk_solver as sls  # noqa: E402

# ---------------------------------------------------------------------------------------------------------------------
#  Sizes. Largest first; the board takes the largest that fits. See the header for the fab numbers behind them.
# ---------------------------------------------------------------------------------------------------------------------
LABEL_STROKE_MM = 0.17                                      # the largest of the minimums the fabs quote, so any can print it
LABEL_HEIGHTS_MM = (1.0, 0.9, 0.8, 0.75, 0.7, 0.65, 0.6)
LABEL_WIDTH_RATIO = 0.7                                     # letter width to height: narrower letters saved more room than a cleverer search
PAD_GAP_MM = 0.15                                           # label to exposed copper
EDGE_INSET_MM = 0.35                                        # label to board edge
SEEDS = (1, 2, 3, 4)                                        # repair order is shuffled per seed; a few leftovers often vanish on another shuffle
BODY_MAX_FRACTION = 0.65                                    # an outline spanning most of the board is a frame, not a body
MM_SCALE = 1e6


def mm(val: float) -> int:
    """Millimetres to KiCad internal units."""
    return pcbnew.FromMM(float(val))


def to_mm(val: int) -> float:
    """KiCad internal units to millimetres."""
    return pcbnew.ToMM(int(val))


def box_mm(bb: Any) -> tuple[float, float, float, float]:
    """A pcbnew BOX2I as (x0, y0, x1, y1) in millimetres."""
    if bb is None:
        return (0.0, 0.0, 0.0, 0.0)
    return (to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom()))


def grow(b: tuple, g: float) -> tuple[float, float, float, float]:
    """A box grown by g on every side."""
    return (b[0] - g, b[1] - g, b[2] + g, b[3] + g)


def touches(a: tuple, b: tuple, g: float = 0.0) -> bool:
    """True when box a, grown by g, intersects box b."""
    return a[0] - g < b[2] and a[2] + g > b[0] and a[1] - g < b[3] and a[3] + g > b[1]


def poly_of(sps: Any) -> Any:
    """A KiCad SHAPE_POLY_SET as a shapely (Multi)Polygon, holes kept."""
    if sps is None:
        return Polygon()
    polys = []
    for i in range(sps.OutlineCount()):
        ring = sps.Outline(i)
        shell = [(ring.CPoint(j).x / MM_SCALE, ring.CPoint(j).y / MM_SCALE) for j in range(ring.PointCount())]
        holes = []
        for h in range(sps.HoleCount(i)):
            hole = sps.Hole(i, h)
            holes.append([(hole.CPoint(j).x / MM_SCALE, hole.CPoint(j).y / MM_SCALE) for j in range(hole.PointCount())])
        if len(shell) >= 3:
            polys.append(Polygon(shell, holes))
    return unary_union(polys) if polys else Polygon()


def safe_outline(board: Any) -> Any:
    """The Edge.Cuts outline pulled in by EDGE_INSET_MM as a shapely shape; a plain box when the outline cannot be read."""
    eb = box_mm(board.GetBoardEdgesBoundingBox())
    fallback = box(eb[0] + EDGE_INSET_MM, eb[1] + EDGE_INSET_MM, eb[2] - EDGE_INSET_MM, eb[3] - EDGE_INSET_MM)
    sps = pcbnew.SHAPE_POLY_SET()
    if board.GetBoardPolygonOutlines(sps, True) and sps.OutlineCount() > 0:
        inset = poly_of(sps).buffer(-EDGE_INSET_MM)
        if not inset.is_empty:
            return inset
    return fallback


def outside_grid(safe: Any, bounds: tuple) -> Any:
    """Boolean raster, True where a label may not stand (outside the inset outline). Even-odd crossing test over every
    ring so cut-outs count as outside."""
    x0, y0, x1, y1 = bounds
    nx, ny = int(math.ceil((x1 - x0) / sls.CELL)) + 1, int(math.ceil((y1 - y0) / sls.CELL)) + 1
    xs = x0 + (np.arange(nx) + 0.5) * sls.CELL
    ys = y0 + (np.arange(ny) + 0.5) * sls.CELL
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    inside = np.zeros(X.shape, bool)
    polys = list(safe.geoms) if hasattr(safe, "geoms") else [safe]
    for poly in polys:
        for ring in [poly.exterior] + list(poly.interiors):
            pts = np.array(ring.coords)
            for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]):
                if ay == by:
                    continue
                inside ^= ((ay > Y) != (by > Y)) & (X < (bx - ax) * (Y - ay) / (by - ay) + ax)
    return ~inside


def side_obstacles(board: Any, side: str) -> tuple[list, list, list, list]:
    """What a label on one face must stay clear of: (pad boxes, (ref, body box) pairs, board-level text boxes, silk
    shape boxes). Pads count on both faces when they are through-hole or exposed on this face; bodies come from the pad
    extent plus the part's fab-layer outline."""
    is_b = side == "B"
    mask_l = pcbnew.B_Mask if is_b else pcbnew.F_Mask
    cu_l = pcbnew.B_Cu if is_b else pcbnew.F_Cu
    silk_l = pcbnew.B_SilkS if is_b else pcbnew.F_SilkS
    fab_l = pcbnew.B_Fab if is_b else pcbnew.F_Fab
    eb = box_mm(board.GetBoardEdgesBoundingBox())
    bw, bh = max(1.0, eb[2] - eb[0]), max(1.0, eb[3] - eb[1])
    pads, bodies, texts, shapes = [], [], [], []
    # board-level text and drawings already on this face's silkscreen
    for d in board.GetDrawings():
        if d.GetLayer() != silk_l:
            continue
        if isinstance(d, pcbnew.PCB_TEXT):
            texts.append(box_mm(d.GetBoundingBox()))
        elif isinstance(d, pcbnew.PCB_SHAPE):
            shapes.append(box_mm(d.GetBoundingBox()))
    for fp in board.GetFootprints():
        on_side = fp.IsFlipped() == is_b
        own = []
        for pad in fp.Pads():
            pb = box_mm(pad.GetBoundingBox())
            if on_side:
                own.append(pb)
            if pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH) or pad.GetDrillSize().x > 0 \
                    or pad.IsOnLayer(mask_l) or pad.IsOnLayer(cu_l):
                pads.append(pb)
        if not on_side:
            continue
        pos = (to_mm(fp.GetPosition().x), to_mm(fp.GetPosition().y))
        x0 = min((b[0] for b in own), default=pos[0] - 0.5)
        y0 = min((b[1] for b in own), default=pos[1] - 0.5)
        x1 = max((b[2] for b in own), default=pos[0] + 0.5)
        y1 = max((b[3] for b in own), default=pos[1] + 0.5)
        # footprint text already on silk, and graphics: silk is an obstacle, the fab outline widens the body
        for gi in fp.GraphicalItems():
            lay = gi.GetLayer()
            if isinstance(gi, pcbnew.PCB_TEXT) and lay == silk_l and gi.IsVisible():
                texts.append(box_mm(gi.GetBoundingBox()))
            elif isinstance(gi, pcbnew.PCB_SHAPE):
                gb = box_mm(gi.GetBoundingBox())
                if lay == silk_l:
                    shapes.append(gb)
                elif lay == fab_l and (gb[2] - gb[0]) < bw * BODY_MAX_FRACTION and (gb[3] - gb[1]) < bh * BODY_MAX_FRACTION:
                    x0, y0, x1, y1 = min(x0, gb[0]), min(y0, gb[1]), max(x1, gb[2]), max(y1, gb[3])
        if (x1 - x0) < bw * BODY_MAX_FRACTION and (y1 - y0) < bh * BODY_MAX_FRACTION:
            bodies.append((fp.GetReference(), (x0, y0, x1, y1)))
    return pads, bodies, texts, shapes


def style_labels(board: Any, height: float) -> None:
    """Every reference to the silk layer of its face at this size; values stay hidden on the fab layer."""
    for fp in board.GetFootprints():
        is_b = fp.IsFlipped()
        silk = pcbnew.B_SilkS if is_b else pcbnew.F_SilkS
        fab = pcbnew.B_Fab if is_b else pcbnew.F_Fab
        ref = fp.Reference()
        ref.SetLayer(silk)
        ref.SetVisible(True)
        ref.SetMirrored(is_b)
        ref.SetKeepUpright(True)
        ref.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
        ref.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
        ref.SetTextSize(pcbnew.VECTOR2I(mm(height * LABEL_WIDTH_RATIO), mm(height)))
        ref.SetTextThickness(mm(LABEL_STROKE_MM))
        val = fp.Value()
        if val.GetLayer() in (pcbnew.F_SilkS, pcbnew.B_SilkS):
            val.SetLayer(fab)
        val.SetMirrored(is_b)
        val.SetVisible(False)
        # the board-only items (holes, fiducials) carry no label
        if fp.GetAttributes() & pcbnew.FP_BOARD_ONLY:
            ref.SetVisible(False)


def place_all(board: Any, height: float, outside: Any, bounds: tuple) -> dict:
    """Every label of the board at one size, through silk_solver. Writes the positions onto the board."""
    style_labels(board, height)
    res: dict[str, Any] = dict(height=height, left=0, no_slot=[], slanted=0)
    for side in ("F", "B"):
        is_b = side == "B"
        pads, bodies, texts, shapes = side_obstacles(board, side)
        hard = [grow(b, PAD_GAP_MM) for b in pads] + [grow(b, 0.02) for _r, b in bodies] \
            + [grow(b, 0.05) for b in texts] + [grow(b, 0.03) for b in shapes]
        # a via under a letter is only a dimple: a small price, not a wall
        soft = []
        for trk in board.GetTracks():
            if trk.Type() == pcbnew.PCB_VIA_T:
                vx, vy = to_mm(trk.GetPosition().x), to_mm(trk.GetPosition().y)
                vr = to_mm(trk.GetDrillValue()) * 0.5 + 0.02
                soft.append((vx - vr, vy - vr, vx + vr, vy + vr))
        grid = sls.Side(bounds, hard, soft, outside)
        body_of = dict(bodies)
        labels, by_ref = [], {}
        for fp in board.GetFootprints():
            if fp.IsFlipped() != is_b or not fp.Reference().IsVisible():
                continue
            ref = fp.Reference()
            # the text box is measured at both angles on the board: the angle turns with the footprint
            ref.SetTextAngleDegrees(0.0)
            b0 = ref.GetBoundingBox()
            ref.SetTextAngleDegrees(-90.0 if is_b else 90.0)
            b90 = ref.GetBoundingBox()
            pbs = [box_mm(q.GetBoundingBox()) for q in fp.Pads()]
            pos = (to_mm(fp.GetPosition().x), to_mm(fp.GetPosition().y))
            pbox = (min(q[0] for q in pbs), min(q[1] for q in pbs), max(q[2] for q in pbs), max(q[3] for q in pbs)) \
                if pbs else (pos[0] - 0.5, pos[1] - 0.5, pos[0] + 0.5, pos[1] + 0.5)
            bd = body_of.get(fp.GetReference())
            if bd:
                pbox = (min(pbox[0], bd[0]), min(pbox[1], bd[1]), max(pbox[2], bd[2]), max(pbox[3], bd[3]))
            w0, h0, w90, h90 = to_mm(b0.GetWidth()), to_mm(b0.GetHeight()), to_mm(b90.GetWidth()), to_mm(b90.GetHeight())
            labels.append(dict(ref=fp.GetReference(), box=pbox, w0=w0, h0=h0, w90=w90, h90=h90,
                               w=max(w0, h0), h=min(w0, h0), chars=len(fp.GetReference())))
            by_ref[fp.GetReference()] = fp
        out = sls.solve_side(labels, grid, bounds)
        for seed in SEEDS[1:]:
            if out["left"] == 0:
                break
            again = sls.solve_side(labels, grid, bounds, seed=seed)
            if again["left"] < out["left"]:
                out = again
        res["left"] += out["left"]
        res["no_slot"] += out["no_slot"]
        res["slanted"] += out["slanted"]
        for name, (_cost, cx, cy, ang) in out["chosen"].items():
            ref = by_ref[name].Reference()
            ref.SetTextAngleDegrees(-ang if is_b else ang)
            ref.SetPosition(pcbnew.VECTOR2I(mm(cx), mm(cy)))
    return res


def audit(board: Any) -> dict[str, int]:
    """What a person would see on the saved board: printed labels against pads and holes, other parts' bodies, other
    silk and the board edge. Plain box tests, independent of the solver's own bookkeeping."""
    safe = safe_outline(board)
    eb = box_mm(board.GetBoardEdgesBoundingBox())
    safe_box = (eb[0] + EDGE_INSET_MM, eb[1] + EDGE_INSET_MM, eb[2] - EDGE_INSET_MM, eb[3] - EDGE_INSET_MM)
    n = dict(label_label=0, label_pad=0, label_body=0, label_edge=0)
    for side in ("F", "B"):
        is_b = side == "B"
        silk = pcbnew.B_SilkS if is_b else pcbnew.F_SilkS
        pads, bodies, texts, _shapes = side_obstacles(board, side)
        refs = []
        for fp in board.GetFootprints():
            r = fp.Reference()
            if fp.IsFlipped() != is_b or not r.IsVisible() or r.GetLayer() != silk:
                continue
            refs.append((fp.GetReference(), box_mm(r.GetBoundingBox())))
        for i, (name, rb) in enumerate(refs):
            if not (rb[0] >= safe_box[0] - 1e-4 and rb[1] >= safe_box[1] - 1e-4 and rb[2] <= safe_box[2] + 1e-4
                    and rb[3] <= safe_box[3] + 1e-4 and safe.contains(box(*rb))):
                n["label_edge"] += 1
            if any(touches(rb, p, 0.08) for p in pads):
                n["label_pad"] += 1
            if any(own != name and touches(rb, b) for own, b in bodies):
                n["label_body"] += 1
            n["label_label"] += sum(1 for _o, ob in refs[i + 1:] if touches(rb, ob, 0.06))
            n["label_label"] += sum(1 for t in texts if touches(rb, t, 0.06))
    n["total"] = sum(n.values())
    return n


def set_min_text_height(pcb_path: str, height: float) -> None:
    """The project's min_text_height follows the size in use. No project file, nothing to do."""
    pro = os.path.splitext(pcb_path)[0] + ".kicad_pro"
    if not os.path.isfile(pro):
        return
    with open(pro, encoding="utf-8", newline="") as fh:
        txt = fh.read()
    new = re.sub(r'("min_text_height":\s*)[0-9.]+', lambda m: m.group(1) + str(height), txt)
    if new != txt:
        with open(pro, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)


def place_labels(pcb_path: str) -> int:
    """Find the largest height that fits, save the board at it, print one line per height tried. Returns an exit code."""
    if not os.path.isfile(pcb_path):
        print(f"place_labels: no such board: {pcb_path}")
        return 2
    board = pcbnew.LoadBoard(pcb_path)
    if board is None:
        print(f"place_labels: pcbnew could not open {pcb_path}")
        return 2
    eb = box_mm(board.GetBoardEdgesBoundingBox())
    if eb[2] - eb[0] <= 0 or eb[3] - eb[1] <= 0:
        print(f"place_labels: {pcb_path} has no Edge.Cuts outline")
        return 2
    bounds = (eb[0] - 1.0, eb[1] - 1.0, eb[2] + 1.0, eb[3] + 1.0)
    outside = outside_grid(safe_outline(board), bounds)
    name = os.path.basename(pcb_path)

    def trial(height: float) -> dict:
        """A fresh copy of the board at this height."""
        b = pcbnew.LoadBoard(pcb_path)
        r = place_all(b, height, outside, bounds)
        r["board"] = b
        print(f"  {name} at {height} mm: {r['left']} overlap cells, {len(r['no_slot'])} labels with no slot, "
              f"{r['slanted']} slanted", flush=True)
        return r

    # bisection: sizes run large to small and fitting is monotone enough that the first fit from the left is the answer
    lo, hi, best = 0, len(LABEL_HEIGHTS_MM) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        r = trial(LABEL_HEIGHTS_MM[mid])
        if r["left"] == 0 and not r["no_slot"]:
            best, hi = r, mid - 1
        else:
            lo = mid + 1
    if best is None:
        best = trial(LABEL_HEIGHTS_MM[-1])
    if not pcbnew.SaveBoard(pcb_path, best["board"]):
        print(f"place_labels: SaveBoard failed for {pcb_path}")
        return 2
    set_min_text_height(pcb_path, best["height"])
    # the saved file is what gets measured
    seen = audit(pcbnew.LoadBoard(pcb_path))
    print(f"place_labels: {name} labels {best['height']} x {round(best['height'] * LABEL_WIDTH_RATIO, 3)} mm, stroke "
          f"{LABEL_STROKE_MM} mm; overlaps {seen['total']} {seen}; no slot {best['no_slot']}")
    if best["height"] < 0.8:
        print("place_labels: text under 0.8 mm may print soft; check the fab's preview")
    return 0 if seen["total"] == 0 and best["left"] == 0 and not best["no_slot"] else 1


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] in ("-h", "--help"):
        print("usage: place_labels.py <board.kicad_pcb>")
        return 2
    return place_labels(os.path.abspath(argv[1]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
