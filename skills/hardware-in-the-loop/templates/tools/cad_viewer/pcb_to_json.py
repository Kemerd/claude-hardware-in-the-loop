#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 pcb_to_json.py - export a KiCad board to the small JSON the web viewer's PCB overlay draws
=======================================================================================================================
 Interpreter: KiCad 10's bundled python (Windows: "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe"; Linux: the system
              python that ships the pcbnew module). It is the only interpreter that has pcbnew.
 Paths: command line (the board, --out, --kicad-home); the default KiCad home resolves from __file__

 KiCad writes per-user settings, documents and caches the moment pcbnew is imported. This script points
 KICAD_CONFIG_HOME / KICAD_DOCUMENTS_HOME / KICAD_CACHE_HOME at a scratch folder BEFORE the import (default
 <repo>/.tmp/kicad_home, or --kicad-home), so nothing lands in the user profile.

 Output frame: board coordinates in mm, x right, y UP seen from the top (F.Cu) side (KiCad's y-down is flipped), origin
 at the centre of the board outline's bounding box (its KiCad position is kept as "origin_kicad_mm").

 Output JSON:
   {"schema": "hil-pcb/1", "board": <file stem>, "source": <file name>, "thickness_mm", "copper_layers",
    "frame": "...", "origin_kicad_mm": [x, y],
    "outline": [{"outer": [[x, y], ...], "holes": [[[x, y], ...], ...]}, ...],
    "footprints": [{"ref", "value", "fpid", "side": "top"|"bottom", "layer", "x", "y", "rot_deg",
                    "box": [xmin, ymin, xmax, ymax], "box_source": "courtyard"|"bbox", "board_only": bool}],
    "holes": [{"ref", "x", "y", "drill_mm", "plated": bool}],
    "place": {"origin_mm": [x, y, z], "rot_deg": r, "side_up": "top"|"bottom"} | null}
   "place" positions the board in the CAD model frame: the board's (0, 0) goes to origin_mm, it turns rot_deg about the
   model Z axis, and side_up says which copper side faces +Z.

 Usage: <kicad python> tools/cad_viewer/pcb_to_json.py <board.kicad_pcb> --out <board.json> [--place "x,y,z,rot_deg,top|bottom"]
                                             [--kicad-home <dir>]
=======================================================================================================================
"""
import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------------------------------------------------
#  Console safety first (part values may carry Greek letters), then argument parsing, THEN KiCad isolation + import
# ---------------------------------------------------------------------------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_KICAD_HOME = os.path.join(REPO, ".tmp", "kicad_home")


def die(msg):
    """Clear error, non-zero exit."""
    print(f"pcb_to_json: ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def isolate_kicad(home):
    """Redirect KiCad's per-user folders to <home>/{config,documents,cache} (must run before `import pcbnew`)."""
    home = os.path.abspath(home)
    # refuse the user profile outright: the whole point of this function is to keep KiCad out of it
    profile = os.path.abspath(os.path.expanduser("~"))
    if os.path.normcase(home).startswith(os.path.normcase(profile) + os.sep) or os.path.normcase(home) == os.path.normcase(profile):
        die(f"--kicad-home {home} is inside the user profile; use a scratch folder")
    for sub, var in (("config", "KICAD_CONFIG_HOME"), ("documents", "KICAD_DOCUMENTS_HOME"), ("cache", "KICAD_CACHE_HOME")):
        p = os.path.join(home, sub)
        os.makedirs(p, exist_ok=True)
        os.environ[var] = p


def parse_place(text):
    """'x,y,z,rot_deg,side' -> placement dict, or exit with a message."""
    bits = [b.strip() for b in str(text).split(",")]
    if len(bits) != 5:
        die("--place needs 5 comma-separated fields: x,y,z,rot_deg,top|bottom")
    try:
        x, y, z, r = (float(b) for b in bits[:4])
    except ValueError:
        die(f"--place numbers unreadable: {text!r}")
    if any(v != v or v in (float("inf"), float("-inf")) for v in (x, y, z, r)):
        die("--place numbers must be finite")
    side = bits[4].lower()
    if side not in ("top", "bottom"):
        die("--place side must be top or bottom")
    return {"origin_mm": [x, y, z], "rot_deg": r, "side_up": side}


def main(argv=None):
    ap = argparse.ArgumentParser(description="KiCad board -> viewer overlay JSON (run under KiCad's python)")
    ap.add_argument("board", help=".kicad_pcb file")
    ap.add_argument("--out", required=True, help="output JSON path")
    ap.add_argument("--place", help="placement in the model frame: x,y,z,rot_deg,top|bottom")
    ap.add_argument("--kicad-home", default=DEFAULT_KICAD_HOME, help="scratch folder for KiCad's per-user files")
    a = ap.parse_args(argv)

    board_path = os.path.abspath(a.board)
    if not os.path.isfile(board_path) or not board_path.lower().endswith(".kicad_pcb"):
        die(f"not a .kicad_pcb file: {board_path}")
    place = parse_place(a.place) if a.place else None

    isolate_kicad(a.kicad_home)
    try:
        import pcbnew  # noqa: E402  (only after isolate_kicad)
    except ImportError:
        die("pcbnew not importable: run this with KiCad's bundled python")

    board = pcbnew.LoadBoard(board_path)
    if board is None:
        die(f"KiCad could not load {board_path}")
    to_mm = pcbnew.ToMM

    # -----------------------------------------------------------------------------------------------------------------
    #  Outline: KiCad's own board polygon (it closes arcs and joins segments); fall back to raw Edge.Cuts bounds
    # -----------------------------------------------------------------------------------------------------------------
    polys_kicad = []                                         # [(outer [(x, y)], holes [[(x, y)]])] in KiCad mm, y down
    ps = pcbnew.SHAPE_POLY_SET()
    ok = False
    # KiCad 10: (outlines, aInferOutlineIfNecessary, ...) with the bool REQUIRED; KiCad 8/9: (outlines, ...) only.
    # Try the KiCad 10 form first, then the older one; infer = False so an open outline is reported, not invented.
    for args in ((ps, False), (ps,)):
        try:
            ok = bool(board.GetBoardPolygonOutlines(*args))
            break
        except TypeError:
            continue
        except Exception:
            ok = False
            break

    def chain_pts(chain):
        return [(to_mm(chain.CPoint(j).x), to_mm(chain.CPoint(j).y)) for j in range(chain.PointCount())]

    if ok and ps.OutlineCount() > 0:
        for i in range(ps.OutlineCount()):
            outer = chain_pts(ps.Outline(i))
            holes = [chain_pts(ps.Hole(i, h)) for h in range(ps.HoleCount(i))]
            if len(outer) >= 3:
                polys_kicad.append((outer, holes))
    if not polys_kicad:
        # no closed outline: use the Edge.Cuts bounding box so the overlay still shows where the board is
        bb = board.GetBoardEdgesBoundingBox()
        if bb.GetWidth() <= 0 or bb.GetHeight() <= 0:
            die("board has no Edge.Cuts outline")
        x0, y0, x1, y1 = to_mm(bb.GetX()), to_mm(bb.GetY()), to_mm(bb.GetRight()), to_mm(bb.GetBottom())
        polys_kicad.append(([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], []))
        print("pcb_to_json: outline not closed; exported the Edge.Cuts bounding box instead")

    # origin = centre of the outline's bounding box (KiCad mm); every exported point is relative to it, y flipped up
    xs = [p[0] for o, _ in polys_kicad for p in o]
    ys = [p[1] for o, _ in polys_kicad for p in o]
    ox, oy = (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0

    def tx(x, y):
        return [round(x - ox, 4), round(-(y - oy), 4)]

    outline = [{"outer": [tx(*p) for p in o], "holes": [[tx(*p) for p in h] for h in hs]} for o, hs in polys_kicad]

    # -----------------------------------------------------------------------------------------------------------------
    #  Footprints (courtyard boxes) and holes
    # -----------------------------------------------------------------------------------------------------------------
    fps, holes = [], []
    for fp in board.GetFootprints():
        ref = str(fp.GetReference())
        flipped = bool(fp.IsFlipped())
        pos = fp.GetPosition()
        fpid = str(fp.GetFPID().GetLibItemName())
        box, src = None, "bbox"
        try:
            cy = fp.GetCourtyard(pcbnew.B_CrtYd if flipped else pcbnew.F_CrtYd)
            if cy is not None and cy.OutlineCount() > 0:
                b = cy.BBox()
                box, src = (b.GetX(), b.GetY(), b.GetRight(), b.GetBottom()), "courtyard"
        except Exception:
            box = None
        if box is None:
            try:
                b = fp.GetBoundingBox(False)                 # body + pads, no text
            except TypeError:
                b = fp.GetBoundingBox()
            box = (b.GetX(), b.GetY(), b.GetRight(), b.GetBottom())
        p0, p1 = tx(to_mm(box[0]), to_mm(box[1])), tx(to_mm(box[2]), to_mm(box[3]))
        c = tx(to_mm(pos.x), to_mm(pos.y))
        try:
            board_only = bool(fp.IsBoardOnly())
        except Exception:
            board_only = False
        fps.append({"ref": ref, "value": str(fp.GetValue()), "fpid": fpid,
                    "side": "bottom" if flipped else "top", "layer": "B.Cu" if flipped else "F.Cu",
                    "x": c[0], "y": c[1], "rot_deg": round(float(fp.GetOrientationDegrees()), 2),
                    "box": [min(p0[0], p1[0]), min(p0[1], p1[1]), max(p0[0], p1[0]), max(p0[1], p1[1])],
                    "box_source": src, "board_only": board_only})
        # mounting holes: a MountingHole* footprint, or a footprint whose every pad is a non-plated hole
        pads = list(fp.Pads())
        npth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
        is_mount = fpid.lower().startswith("mountinghole") or (pads and len(npth) == len(pads))
        if is_mount:
            for p in (pads or []):
                d = p.GetDrillSize()
                if d.x <= 0:
                    continue
                pp = tx(to_mm(p.GetPosition().x), to_mm(p.GetPosition().y))
                holes.append({"ref": ref, "x": pp[0], "y": pp[1], "drill_mm": round(to_mm(min(d.x, d.y)), 4),
                              "plated": p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH})
            if not pads:                                     # a hole footprint with no pad: mark its position at least
                holes.append({"ref": ref, "x": c[0], "y": c[1], "drill_mm": None, "plated": False})

    try:
        thickness = round(to_mm(board.GetDesignSettings().GetBoardThickness()), 4)
    except Exception:
        thickness = None
    out = {"schema": "hil-pcb/1", "board": os.path.splitext(os.path.basename(board_path))[0],
           "source": os.path.basename(board_path), "thickness_mm": thickness,
           "copper_layers": int(board.GetCopperLayerCount()),
           "frame": "board mm, x right, y up seen from the top (F.Cu) side, origin = outline bounding-box centre",
           "origin_kicad_mm": [round(ox, 4), round(oy, 4)],
           "outline": outline, "footprints": sorted(fps, key=lambda f: f["ref"]), "holes": holes, "place": place}

    out_path = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    print(f"pcb_to_json: {out['board']}: {len(outline)} outline(s), {len(fps)} footprints, {len(holes)} holes, "
          f"{out['copper_layers']} copper layers, {thickness} mm -> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
