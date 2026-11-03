#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 mass_cg_table.py - the weight-and-balance table every part change carries: mass, station and CG before and after.

 Interpreter: any Python >= 3.9 (stdlib only). Run: python cad/mass_cg_table.py <before parts.json> <after parts.json>
 Paths: command line (both exports and the optional --out file are arguments)

 Input: two builder exports, schema {"parts": {name: {"mass_g": float, "com_mm": [x, y, z], ...}}}. Parts without a
 finite mass or a 3-number COM are listed as "no mass data" and left out of the totals (never guessed).
 Output (Markdown, to stdout or --out):
   1. totals: mass before / after / delta, CG before / after, CG shift (mm, per axis and magnitude),
      inertia about the CG (point-mass approximation, kg mm^2) before / after;
   2. one row per changed, added or removed part: mass before / after / delta, station before / after.
 Options: --all lists unchanged parts too; --tol-g 0.01 and --tol-mm 0.01 set what counts as "changed".
=======================================================================================================================
"""
import argparse
import json
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_parts(path):
    """{name: (mass_g, (x, y, z)) or None} from a parts.json; raises ValueError with the path on a bad file."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    rows = data.get("parts") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        raise ValueError(f"{path}: no 'parts' object")
    out = {}
    for name, row in rows.items():
        m = row.get("mass_g") if isinstance(row, dict) else None
        c = row.get("com_mm") if isinstance(row, dict) else None
        ok = (isinstance(m, (int, float)) and not isinstance(m, bool) and math.isfinite(m) and m >= 0
              and isinstance(c, (list, tuple)) and len(c) == 3
              and all(isinstance(v, (int, float)) and math.isfinite(v) for v in c))
        out[str(name)] = (float(m), tuple(float(v) for v in c)) if ok else None
    return out


def totals(parts):
    """(mass_g, cg_mm, inertia_kgmm2 [Ixx, Iyy, Izz] about the CG, point masses) over the parts with data."""
    good = [v for v in parts.values() if v is not None]
    mass = sum(m for m, _ in good)
    if mass <= 0:
        return 0.0, (float("nan"),) * 3, (float("nan"),) * 3
    cg = tuple(sum(m * c[i] for m, c in good) / mass for i in range(3))
    inertia = []
    for i in range(3):
        j, k = [a for a in range(3) if a != i]
        inertia.append(sum((m / 1000.0) * ((c[j] - cg[j]) ** 2 + (c[k] - cg[k]) ** 2) for m, c in good))
    return mass, cg, tuple(inertia)


def fmt3(v):
    """(x, y, z) as a compact string."""
    return "(" + ", ".join(f"{x:.2f}" for x in v) + ")"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mass / station / CG table between two builder exports")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", default=None, help="write the Markdown here instead of stdout")
    ap.add_argument("--all", action="store_true", help="list unchanged parts too")
    ap.add_argument("--tol-g", type=float, default=0.01)
    ap.add_argument("--tol-mm", type=float, default=0.01)
    a = ap.parse_args(argv)
    try:
        b, n = load_parts(a.before), load_parts(a.after)
    except (OSError, ValueError) as exc:
        print(f"[mass_cg_table] ERROR: {exc}", file=sys.stderr)
        return 2

    mb, cgb, ib = totals(b)
    ma, cga, ia = totals(n)
    shift = tuple(cga[i] - cgb[i] for i in range(3))
    lines = [
        f"## Weight and balance: `{os.path.basename(a.before)}` -> `{os.path.basename(a.after)}`",
        "",
        "| Quantity | Before | After | Delta |",
        "|---|---|---|---|",
        f"| Total mass (g) | {mb:.2f} | {ma:.2f} | {ma - mb:+.2f} |",
        f"| CG (mm) | {fmt3(cgb)} | {fmt3(cga)} | {fmt3(shift)}, magnitude {math.sqrt(sum(s * s for s in shift)):.2f} |",
        f"| Inertia about CG, point masses (kg mm^2) | {fmt3(ib)} | {fmt3(ia)} | {fmt3(tuple(ia[i] - ib[i] for i in range(3)))} |",
        "",
        "| Part | Mass before (g) | Mass after (g) | Delta (g) | Station before (mm) | Station after (mm) | Change |",
        "|---|---|---|---|---|---|---|",
    ]
    nodata = []
    for name in sorted(set(b) | set(n)):
        vb, va = b.get(name), n.get(name)
        if (name in b and vb is None) or (name in n and va is None):
            nodata.append(name)
            continue
        if vb is None:
            lines.append(f"| {name} | - | {va[0]:.2f} | {va[0]:+.2f} | - | {fmt3(va[1])} | added |")
        elif va is None:
            lines.append(f"| {name} | {vb[0]:.2f} | - | {-vb[0]:+.2f} | {fmt3(vb[1])} | - | removed |")
        else:
            dm = va[0] - vb[0]
            moved = max(abs(va[1][i] - vb[1][i]) for i in range(3))
            changed = abs(dm) > a.tol_g or moved > a.tol_mm
            if changed or a.all:
                lines.append(f"| {name} | {vb[0]:.2f} | {va[0]:.2f} | {dm:+.2f} | {fmt3(vb[1])} | {fmt3(va[1])} | "
                             f"{'changed' if changed else 'same'} |")
    if nodata:
        lines += ["", "No mass data (left out of the totals): " + ", ".join(sorted(nodata))]
    text = "\n".join(lines) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"[mass_cg_table] wrote {a.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
