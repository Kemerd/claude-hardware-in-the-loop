#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 render.py - decision images for fit / clash reviews: shaded multi-view PNGs of STL parts, clash parts in red,
             dimension lines with labels, a scale bar, readable at 2400 x 1500.

 Interpreter: the CadQuery virtual environment or any Python >= 3.9 with numpy, trimesh and matplotlib (Agg backend,
 no display needed). Run: python cad/render.py --help
 Paths: command line (every input and the output PNG are arguments; nothing is read relative to the working directory)

 Usage examples
   python cad/render.py --stl cad/enclosure/stl/base.stl:#8c8c94 --stl cad/enclosure/stl/lid.stl:#c8c8d0 \
          --clash cad/enclosure/stl/connector.stl --dim "0,-40,0:60,-40,0:60.0 mm wall to wall" \
          --views iso,front,top --title "Connector vs lid lip, 2026-11-03" --out docs/cad_decisions/2026-11-03/lid/clash.png
   python cad/render.py --parts-json cad/enclosure/parts.json --stl-dir cad/enclosure/stl --clash-names lid,usb_c \
          --out docs/cad_decisions/2026-11-03/lid/assembly.png

 Inputs
   --stl PATH[:COLOR]        a part; COLOR = #rrggbb or r,g,b floats 0..1 (default grey). Repeatable.
   --clash PATH              a part drawn in red (the clashing one). Repeatable.
   --parts-json + --stl-dir  every part of a builder export (parts.json "parts": {name: {"color": [r, g, b], ...}})
   --clash-names a,b         parts from --parts-json drawn in red
   --dim "x,y,z:x,y,z:label" a dimension line between two points (model mm) with its label. Repeatable.
   --views iso,front,side,top,bottom,back   or explicit elev/azim pairs "20/-60"
 Large meshes are decimated for drawing only (never for any measurement).
=======================================================================================================================
"""
import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------------------------------------------------
#  Console safety and a headless backend before pyplot is imported
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("MPLCONFIGDIR", os.path.join(HERE, "..", ".tmp", "matplotlib"))    # never the user profile

import numpy as np                                                    # noqa: E402
import matplotlib                                                     # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                       # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection               # noqa: E402
import trimesh                                                        # noqa: E402

NAMED_VIEWS = {"iso": (22, -55), "front": (0, -90), "back": (0, 90), "side": (0, 0), "top": (90, -90), "bottom": (-90, -90)}
CLASH_RGB = (0.86, 0.12, 0.12)
DEFAULT_RGB = (0.62, 0.63, 0.66)
MAX_FACES_DRAWN = 40000
LIGHT = np.array([0.4, -0.5, 0.8]) / np.linalg.norm([0.4, -0.5, 0.8])


def parse_color(text):
    """'#rrggbb' or 'r,g,b' (0..1) -> tuple of floats; anything else raises ValueError with the bad text."""
    t = (text or "").strip()
    if t.startswith("#") and len(t) == 7:
        return tuple(int(t[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    parts = [float(x) for x in t.split(",")]
    if len(parts) != 3 or not all(0.0 <= v <= 1.0 for v in parts):
        raise ValueError(f"bad colour {text!r}: use #rrggbb or r,g,b in 0..1")
    return tuple(parts)


def split_spec(spec):
    """'path:color' -> (path, color); a Windows drive letter ('C:\\...') is not mistaken for a colour separator."""
    head, sep, tail = spec.rpartition(":")
    if sep and head and (tail.startswith("#") or "," in tail):
        return head, parse_color(tail)
    return spec, None


def parse_dim(text):
    """'x,y,z:x,y,z:label' -> (p0, p1, label)."""
    bits = text.split(":", 2)
    if len(bits) != 3:
        raise ValueError(f"bad --dim {text!r}: expected x,y,z:x,y,z:label")
    p0 = np.array([float(v) for v in bits[0].split(",")])
    p1 = np.array([float(v) for v in bits[1].split(",")])
    if p0.shape != (3,) or p1.shape != (3,) or not (np.all(np.isfinite(p0)) and np.all(np.isfinite(p1))):
        raise ValueError(f"bad --dim points in {text!r}")
    return p0, p1, bits[2]


def parse_views(text):
    """Comma list of named views or elev/azim pairs."""
    out = []
    for v in (text or "iso").split(","):
        v = v.strip()
        if v in NAMED_VIEWS:
            out.append((v, NAMED_VIEWS[v]))
        elif "/" in v:
            e, a = v.split("/", 1)
            out.append((v, (float(e), float(a))))
        elif v:
            raise ValueError(f"unknown view {v!r} (named: {', '.join(NAMED_VIEWS)} or elev/azim)")
    return out


def load_mesh(path):
    """A non-empty trimesh from an STL / OBJ / PLY; raises with the path on any problem."""
    if not os.path.isfile(path):
        raise FileNotFoundError(f"mesh not found: {path}")
    m = trimesh.load(path, force="mesh")
    if m is None or m.is_empty or len(m.faces) == 0:
        raise ValueError(f"empty mesh: {path}")
    return m


def drawable(mesh):
    """A copy decimated to MAX_FACES_DRAWN when needed (drawing only); falls back to the full mesh if decimation fails."""
    if len(mesh.faces) <= MAX_FACES_DRAWN:
        return mesh
    try:
        return mesh.simplify_quadric_decimation(face_count=MAX_FACES_DRAWN)
    except Exception as exc:                                       # optional dependency missing: draw everything
        print(f"[render] decimation unavailable ({exc}); drawing {len(mesh.faces)} faces")
        return mesh


def render(items, dims, views, title, out, size_px):
    """items = [(label, mesh, rgb, is_clash)]; writes one PNG with one panel per view."""
    w_in, h_in = size_px[0] / 100.0, size_px[1] / 100.0
    fig = plt.figure(figsize=(w_in, h_in), dpi=100)
    allv = np.vstack([m.vertices for _, m, _, _ in items])
    lo, hi = allv.min(0), allv.max(0)
    centre, radius = (lo + hi) / 2.0, max((hi - lo).max() / 2.0 * 1.08, 1e-6)
    for k, (vname, (elev, azim)) in enumerate(views):
        ax = fig.add_subplot(1, len(views), k + 1, projection="3d")
        for _, mesh, rgb, clash in items:
            m = drawable(mesh)
            shade = 0.35 + 0.65 * np.clip(m.face_normals @ LIGHT, 0.0, 1.0)
            col = np.outer(shade, np.array(rgb))
            alpha = 1.0 if clash or not any(c for *_, c in items) else 0.55   # with a clash, the rest goes half-glass
            ax.add_collection3d(Poly3DCollection(m.vertices[m.faces], facecolors=np.clip(col, 0, 1), linewidths=0, alpha=alpha))
        # ---- dimension lines: line, end ticks, label at the midpoint
        for p0, p1, label in dims:
            ax.plot(*zip(p0, p1), color="#0a58ca", lw=2.2)
            ax.scatter(*zip(p0, p1), color="#0a58ca", s=14)
            mid = (p0 + p1) / 2.0
            ax.text(mid[0], mid[1], mid[2], f" {label}", color="#0a58ca", fontsize=13, fontweight="bold")
        # ---- scale bar: 10 mm or 100 mm depending on the model size
        bar = 10.0 if radius < 150 else 100.0
        x0, y0, z0 = centre[0] - radius * 0.9, centre[1] - radius * 0.9, lo[2]
        ax.plot([x0, x0 + bar], [y0, y0], [z0, z0], color="black", lw=3)
        ax.text(x0, y0, z0 - radius * 0.1, f"{bar:.0f} mm", color="black", fontsize=11)
        ax.set_xlim(centre[0] - radius, centre[0] + radius)
        ax.set_ylim(centre[1] - radius, centre[1] + radius)
        ax.set_zlim(centre[2] - radius, centre[2] + radius)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev, azim)
        ax.set_axis_off()
        ax.set_title(vname, fontsize=13)
    # ---- legend: every part with its colour, clash parts marked
    handles = [plt.Line2D([0], [0], marker="s", color="w", markerfacecolor=rgb, markersize=12,
                          label=(f"{lab}  (CLASH)" if clash else lab)) for lab, _, rgb, clash in items[:24]]
    fig.legend(handles=handles, loc="lower center", ncol=min(6, max(1, len(handles))), fontsize=11, frameon=False)
    fig.suptitle(title, fontsize=16)
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    fig.savefig(out, dpi=100)
    plt.close(fig)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Fit / clash decision renders (see the module docstring)")
    ap.add_argument("--stl", action="append", default=[], help="PATH[:COLOR] (repeatable)")
    ap.add_argument("--clash", action="append", default=[], help="PATH of a clashing part, drawn red (repeatable)")
    ap.add_argument("--parts-json", default=None, help="builder export parts.json")
    ap.add_argument("--stl-dir", default=None, help="folder with <part>.stl for --parts-json")
    ap.add_argument("--clash-names", default="", help="comma list of --parts-json parts to draw red")
    ap.add_argument("--dim", action="append", default=[], help="x,y,z:x,y,z:label (repeatable)")
    ap.add_argument("--views", default="iso,front,top")
    ap.add_argument("--title", default="")
    ap.add_argument("--size", default="2400x1500", help="pixels WxH")
    ap.add_argument("--out", required=True, help="output PNG")
    a = ap.parse_args(argv)

    try:
        size = tuple(int(v) for v in a.size.lower().split("x"))
        if len(size) != 2 or min(size) < 200:
            raise ValueError
    except ValueError:
        print(f"[render] bad --size {a.size!r}", file=sys.stderr)
        return 2

    items = []
    try:
        # ---- explicit meshes
        for spec in a.stl:
            path, rgb = split_spec(spec)
            items.append((os.path.splitext(os.path.basename(path))[0], load_mesh(path), rgb or DEFAULT_RGB, False))
        for path in a.clash:
            items.append((os.path.splitext(os.path.basename(path))[0], load_mesh(path), CLASH_RGB, True))
        # ---- a whole builder export
        if a.parts_json:
            if not a.stl_dir:
                raise ValueError("--parts-json needs --stl-dir")
            with open(a.parts_json, "r", encoding="utf-8") as fh:
                pj = json.load(fh)
            red = {n.strip() for n in a.clash_names.split(",") if n.strip()}
            for name, row in sorted((pj.get("parts") or {}).items()):
                stl = os.path.join(a.stl_dir, name + ".stl")
                if not os.path.isfile(stl):
                    print(f"[render] no STL for part {name} (skipped in the image, listed here)")
                    continue
                rgb = tuple(row.get("color") or DEFAULT_RGB)
                items.append((name, load_mesh(stl), CLASH_RGB if name in red else rgb, name in red))
        dims = [parse_dim(d) for d in a.dim]
        views = parse_views(a.views)
    except (OSError, ValueError) as exc:
        print(f"[render] ERROR: {exc}", file=sys.stderr)
        return 2
    if not items:
        print("[render] ERROR: nothing to draw (give --stl, --clash or --parts-json)", file=sys.stderr)
        return 2

    render(items, dims, views, a.title, a.out, size)
    print(f"[render] wrote {a.out} ({len(items)} parts, {len(views)} views, {size[0]}x{size[1]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
