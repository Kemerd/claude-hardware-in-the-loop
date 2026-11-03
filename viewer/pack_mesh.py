#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 pack_mesh.py - pack an assembly's per-part STLs into one compact payload for the web CAD viewer (index.html)
=======================================================================================================================
 Interpreter: any Python >= 3.9 with numpy + trimesh (the CadQuery venv has both; so does a plain `pip install trimesh`)
 Paths: command line (the build dir, the out dir and every optional file are named on the command line)

 Input: a build directory written by a CadQuery builder
     <build>/parts.json                 {"parts": {name: {"mass_g", "material", "com_mm", "color", "link"?}},
                                         "totals"?: {...}, "frame"?: "...", "note"?: "...", "source"?: "..."}
           mass_g    float grams or null (null = massless, e.g. a keep-out shown for context)
           material  free text key ("PETG", "6061-T6", "catalogue", ...); the viewer groups by it
           com_mm    [x, y, z] centre of mass in the model frame (mm); when absent the mesh centroid is used
           color     [r, g, b] floats 0..1 (optional; colors.json wins when it names the part)
     <build>/<stl subdir>/<name>.stl    one STL per part, model frame, mm (default subdir "stl"; a pose
                                         subfolder such as "stl/level" is selected with --stl-subdir stl/level)
     <build>/<stl subdir>/colors.json   optional {name: [r, g, b]}

 Output (in <out dir>):
     mesh.bin        'HILM' + uint32 manifest length + JSON manifest, then per part (manifest order):
                         uint16[nv*3] quantized positions (mm = origin + q * scale, per axis)   uint32[nt*3] indices
     mesh_<k>.json   {"b64": ...} pieces of mesh.bin (static hosts often serve only web extensions and cap file size)
     mesh.json       {"chunks", "bytes", "build", "about", "pcb": [file, ...], "harness": file | null}
     plus copies of the overlay files given with --pcb / --harness (the viewer fetches them by the names listed)

 Change marks: --baseline <parts.json of the previous build> flags parts whose name is not in it as "new";
               --changed <text file, one part name per line> flags "changed". --label names the build, --about adds a
               one-paragraph blurb under the title.

 Usage: python pack_mesh.py <build dir> <out dir> [--stl-subdir stl] [--baseline parts.json] [--changed names.txt]
                            [--label "Build 7"] [--about "text"] [--pcb board.json ...] [--harness wires.json]
=======================================================================================================================
"""
import argparse
import base64
import json
import os
import re
import shutil
import struct
import sys

# ---------------------------------------------------------------------------------------------------------------------
#  Windows console safety: part names may carry any Unicode; a cp1252 console must never kill the run
# ---------------------------------------------------------------------------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))          # viewer/ (only used to point the user at index.html)
MAGIC = b"HILM"
CHUNK = 12 * 1024 * 1024                                     # 12 MiB raw per piece = 16 MiB of base64, under a 20 MiB cap
OVERLAY_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}\.json$")   # the names serve.py will agree to serve


def die(msg):
    """Print a clear error and exit non-zero (never a half-written payload)."""
    print(f"pack_mesh: ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def read_json(path, what):
    """Load a JSON file or exit with a message naming what it was supposed to be."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        die(f"{what} {path} unreadable: {e}")


def finite3(v):
    """[x, y, z] of finite floats, or None for anything garbled."""
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or x != x or x in (float("inf"), float("-inf")):
            return None
        out.append(float(x))
    return out


def rgb_of(v):
    """[r, g, b] clamped to 0..1, or None."""
    t = finite3(v)
    return None if t is None else [min(1.0, max(0.0, c)) for c in t]


def copy_overlay(src, out_dir, kind):
    """Copy an overlay JSON (board / harness) into the out dir under a served-safe name; returns that name."""
    if not os.path.isfile(src):
        die(f"--{kind} file not found: {src}")
    read_json(src, f"--{kind}")                               # must at least parse: a broken overlay fails here, not in the browser
    name = os.path.basename(src)
    if not OVERLAY_NAME_RE.match(name) or re.match(r"^mesh(_\d+)?\.json$", name):
        die(f"--{kind} file name {name!r} must be plain [A-Za-z0-9_.-]+.json and not mesh*.json")
    dst = os.path.join(out_dir, name)
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    return name


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pack per-part STLs + parts.json into the web viewer payload.")
    ap.add_argument("build", help="build dir holding parts.json and the STL subdir")
    ap.add_argument("out", help="output dir (created)")
    ap.add_argument("--stl-subdir", default="stl", help="STL folder relative to the build dir (default: stl)")
    ap.add_argument("--baseline", help="parts.json of the previous build (new-part marks)")
    ap.add_argument("--changed", help="text file, one changed part name per line")
    ap.add_argument("--label", default="", help="build name shown in the title")
    ap.add_argument("--about", default="", help="one paragraph shown under the title")
    ap.add_argument("--pcb", action="append", default=[], help="board JSON from pcb_to_json.py (repeatable)")
    ap.add_argument("--harness", help="wires JSON (harness runs + ties)")
    a = ap.parse_args(argv)

    # ---- heavy imports after argument parsing so --help works without numpy / trimesh
    try:
        import numpy as np
        import trimesh
    except ImportError as e:
        die(f"needs numpy + trimesh ({e}); run it with the CadQuery venv or `pip install trimesh numpy`")

    build = os.path.abspath(a.build)
    out = os.path.abspath(a.out)
    stl_dir = os.path.join(build, a.stl_subdir)
    pj_path = os.path.join(build, "parts.json")
    if not os.path.isfile(pj_path):
        die(f"no parts.json in {build}")
    if not os.path.isdir(stl_dir):
        die(f"no STL folder {stl_dir} (use --stl-subdir)")
    os.makedirs(out, exist_ok=True)

    pj = read_json(pj_path, "parts.json")
    rows = pj.get("parts") if isinstance(pj, dict) else None
    if not isinstance(rows, dict) or not rows:
        die("parts.json has no 'parts' object")
    colors_path = os.path.join(stl_dir, "colors.json")
    colors = read_json(colors_path, "colors.json") if os.path.isfile(colors_path) else {}
    if not isinstance(colors, dict):
        colors = {}

    # ---- change marks (both optional; a missing file is reported and simply yields no marks)
    baseline_names, changed_names = set(), set()
    if a.baseline:
        if os.path.isfile(a.baseline):
            bl = read_json(a.baseline, "--baseline")
            baseline_names = set((bl.get("parts") or {}).keys()) if isinstance(bl, dict) else set()
        else:
            print(f"pack_mesh: no baseline at {a.baseline}: no 'new' marks")
    if a.changed:
        if os.path.isfile(a.changed):
            with open(a.changed, encoding="utf-8") as fh:
                changed_names = {ln.strip() for ln in fh if ln.strip()}
        else:
            print(f"pack_mesh: no changed list at {a.changed}: no 'changed' marks")

    # ---- load every part mesh (index the triangle soup; skip, and say so, anything missing or empty)
    meshes = []
    for name, row in rows.items():
        if not isinstance(name, str) or not name or not isinstance(row, dict):
            print(f"pack_mesh: skipping malformed part row {name!r}")
            continue
        stl = os.path.join(stl_dir, name + ".stl")
        if not os.path.isfile(stl):
            print(f"pack_mesh: no STL for {name}")
            continue
        try:
            m = trimesh.load(stl, force="mesh", process=False)
        except Exception as e:                                # a corrupt STL names itself, the others still pack
            print(f"pack_mesh: unreadable STL {stl}: {e}")
            continue
        if m.is_empty or len(m.faces) == 0:
            print(f"pack_mesh: empty mesh {name}")
            continue
        m.merge_vertices()
        meshes.append((name, row, m))
    if not meshes:
        die("no part had a usable STL")

    # ---- one quantization frame for all parts: origin + per-axis scale so 0..65000 spans the assembly
    lo = np.min([m.bounds[0] for _, _, m in meshes], axis=0)
    hi = np.max([m.bounds[1] for _, _, m in meshes], axis=0)
    scale = np.maximum((hi - lo) / 65000.0, 1e-6)

    parts, blobs, off = [], [], 0
    # heaviest first: the list reads like a mass budget (massless parts sink to the bottom)
    for name, row, m in sorted(meshes, key=lambda t: -(t[1].get("mass_g") or 0)):
        q = np.clip(np.round((m.vertices - lo) / scale), 0, 65535).astype("<u2")
        idx = m.faces.astype("<u4")
        b = q.tobytes() + idx.tobytes()
        com = finite3(row.get("com_mm")) or [float(v) for v in m.centroid]
        mass = row.get("mass_g")
        mass = float(mass) if isinstance(mass, (int, float)) and not isinstance(mass, bool) and mass == mass else None
        parts.append(dict(
            name=name, mass_g=mass, material=str(row.get("material") or ""), link=row.get("link"),
            rgb=rgb_of(colors.get(name)) or rgb_of(row.get("color")) or [0.62, 0.64, 0.68],
            com=[round(v, 3) for v in com],
            zmin=round(float(m.bounds[0][2]), 3), zmax=round(float(m.bounds[1][2]), 3),
            nv=int(len(q)), nt=int(len(idx)), off=off, nbytes=len(b),
            new=bool(baseline_names) and name not in baseline_names, changed=name in changed_names))
        blobs.append(b)
        off += len(b)

    # ---- overlays: copied beside the payload, listed by name in mesh.json
    pcb_names = [copy_overlay(p, out, "pcb") for p in a.pcb]
    harness_name = copy_overlay(a.harness, out, "harness") if a.harness else None

    manifest = dict(origin=[float(v) for v in lo], scale=[float(v) for v in scale], parts=parts,
                    totals=pj.get("totals"), frame=pj.get("frame") or "model frame: X forward, Y left, Z up, mm",
                    note=pj.get("note"), source=pj.get("source"), build=a.label,
                    n_new=sum(1 for p in parts if p["new"]), n_changed=sum(1 for p in parts if p["changed"]))
    mj = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    bin_path = os.path.join(out, "mesh.bin")
    with open(bin_path, "wb") as fh:
        fh.write(MAGIC + struct.pack("<I", len(mj)) + mj)
        for b in blobs:
            fh.write(b)
    print(f"pack_mesh: parts {len(parts)} tris {sum(p['nt'] for p in parts)} verts {sum(p['nv'] for p in parts)} "
          f"bytes {os.path.getsize(bin_path)} new {manifest['n_new']} changed {manifest['n_changed']}")

    # ---- web wrapper: base64 pieces + an index file; stale pieces of an earlier, bigger pack are removed by name
    with open(bin_path, "rb") as fh:
        raw = fh.read()
    pieces = [raw[i:i + CHUNK] for i in range(0, len(raw), CHUNK)] or [b""]
    for fn in os.listdir(out):
        mm = re.match(r"^mesh_(\d+)\.json$", fn)
        if mm and int(mm.group(1)) >= len(pieces):
            os.remove(os.path.join(out, fn))
    for k, piece in enumerate(pieces):
        with open(os.path.join(out, f"mesh_{k}.json"), "w", encoding="ascii") as fh:
            fh.write('{"b64":"' + base64.b64encode(piece).decode("ascii") + '"}')
    with open(os.path.join(out, "mesh.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(chunks=len(pieces), bytes=len(raw), build=a.label, about=a.about,
                       pcb=pcb_names, harness=harness_name), fh)
    print(f"pack_mesh: mesh.json chunks {len(pieces)}; overlays pcb={pcb_names} harness={harness_name}")
    print(f"pack_mesh: view with  python {os.path.join(HERE, 'serve.py')} --mesh {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
