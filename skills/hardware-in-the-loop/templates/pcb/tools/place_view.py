#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
place_view.py - draw a board's placement on a grid, both faces, so a person or a model can read it and place parts by eye.

Why: a stuck router reports a conflict count, and the count does not say why. The picture does: a connector in the middle of the board, a wall of tall parts,
decoupling pushed away from its chip. See docs/PCB.md, "When the router gets stuck".

Two steps, because pcbnew and matplotlib usually do not live in one Python:
    KiCad python   place_view.py dump <board.kicad_pcb> <view.json>
    any python     place_view.py draw <view.json> <view.png> [--grid 5]

Coordinates are sheet millimetres from the board's centre, y up. The bottom face is drawn as seen from above, through the board, so a part keeps its own x, y on both panels.
Colours: orange connectors, dark blue ICs, grey-blue crystals and inductors, light green passives. Every part big enough to read carries its reference.
Windows console safe (ASCII output).
"""
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def dump(board, out):
    """Write every footprint's box, face and nets, and the board outline, to JSON."""
    import pcbnew
    if not os.path.isfile(board):
        raise SystemExit(f"place_view: {board} not found")
    b = pcbnew.LoadBoard(board)
    to = pcbnew.ToMM
    bb = b.GetBoardEdgesBoundingBox()
    cx, cy = to(bb.GetCenter().x), to(bb.GetCenter().y)
    rows = []
    for fp in b.GetFootprints():
        layer = pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd
        cy_shape = fp.GetCourtyard(layer)
        box = cy_shape.BBox() if cy_shape.OutlineCount() else fp.GetBoundingBox(False, False)
        rows.append(dict(ref=fp.GetReference(), side="B" if fp.IsFlipped() else "F", x0=to(box.GetLeft()) - cx, x1=to(box.GetRight()) - cx, y0=cy - to(box.GetBottom()), y1=cy - to(box.GetTop()),
                         fp=fp.GetFPIDAsString(), val=fp.GetValue(), nets=sorted({p.GetNetname() for p in fp.Pads() if p.GetNetname()})))
    edges = []
    for d in b.GetDrawings():
        if d.GetLayer() == pcbnew.Edge_Cuts and hasattr(d, "GetStart"):
            s, e = d.GetStart(), d.GetEnd()
            edges.append([to(s.x) - cx, cy - to(s.y), to(e.x) - cx, cy - to(e.y)])
    with open(out, "w", encoding="utf-8") as f:
        json.dump(dict(parts=rows, edges=edges), f)
    print(f"place_view: {len(rows)} parts, {len(edges)} edge segments -> {out}")


def colour(r):
    """Fill colour of a part by what it is."""
    ref = r["ref"]
    if ref.startswith(("J", "P", "CN")):
        return "tab:orange"
    if ref.startswith(("U", "IC")):
        return "#3182bd"
    if ref.startswith(("Y", "X", "L", "T")):
        return "#9ecae1"
    return "#a1d99b"


def draw(src, png, grid=5):
    """Both faces side by side on a grid."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = json.load(open(src, encoding="utf-8"))
    parts, edges = d.get("parts", []), d.get("edges", [])
    if not parts:
        raise SystemExit(f"place_view: {src} holds no parts")
    xs = [e[0] for e in edges] + [e[2] for e in edges] or [p["x0"] for p in parts] + [p["x1"] for p in parts]
    ys = [e[1] for e in edges] + [e[3] for e in edges] or [p["y0"] for p in parts] + [p["y1"] for p in parts]
    x0, x1, y0, y1 = min(xs) - 2, max(xs) + 2, min(ys) - 2, max(ys) + 2
    fig, axs = plt.subplots(1, 2, figsize=(26, 26 * (y1 - y0) / (2.1 * (x1 - x0)) + 1))
    for ax, side in zip(axs, "FB"):
        for e in edges:
            ax.plot([e[0], e[2]], [e[1], e[3]], c="k", lw=1.6)
        used = 0.0
        for r in parts:
            if r["side"] != side:
                continue
            w, h = r["x1"] - r["x0"], r["y1"] - r["y0"]
            used += w * h
            ax.add_patch(plt.Rectangle((r["x0"], r["y0"]), w, h, fc=colour(r), ec="k", lw=0.25, alpha=0.85))
            if w * h > 6.0 or r["ref"].startswith(("U", "J", "Y")):
                ax.text(r["x0"] + w / 2, r["y0"] + h / 2, r["ref"], ha="center", va="center", fontsize=7, weight="bold")
        step = max(1, int(grid))
        ax.set_xlim(x0, x1); ax.set_ylim(y0, y1); ax.set_aspect("equal")
        ax.set_xticks(range(int(x0 / step) * step, int(x1) + 1, step)); ax.set_yticks(range(int(y0 / step) * step, int(y1) + 1, step))
        ax.grid(alpha=0.35, lw=0.5); ax.tick_params(labelsize=7)
        ax.set_title(f"{'TOP (F)' if side == 'F' else 'BOTTOM (B), seen from above'}: {used:.0f} mm2 of courtyard. Orange connectors, dark blue ICs, light green passives", fontsize=10)
    plt.tight_layout()
    plt.savefig(png, dpi=80)
    print(f"place_view: wrote {png}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 3 and a[0] == "dump":
        dump(a[1], a[2])
    elif len(a) >= 3 and a[0] == "draw":
        draw(a[1], a[2], int(a[a.index("--grid") + 1]) if "--grid" in a else 5)
    else:
        raise SystemExit(__doc__)
