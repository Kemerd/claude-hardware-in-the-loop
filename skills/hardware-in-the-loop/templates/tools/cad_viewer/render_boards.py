#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 render_boards.py - render the project's KiCad boards to top / bottom pictures for the viewer's board textures
=======================================================================================================================
 Interpreter: any Python >= 3.9 with Pillow (pip install pillow). KiCad 10 (kicad-cli, and its bundled python for the
              outline sizes) is needed only here; the viewer and the packer run fine without it.
 Run: python tools/cad_viewer/render_boards.py --out .tmp/viewer_boards [--only <part>] [--force] [--max-px 1024]
 Paths: --out and --root (default: two folders above this file); the board files come from viewer.config.json "boards".

 What it does
   For every CAD part named in viewer.config.json "boards" (part -> repo-relative .kicad_pcb) it runs
   `kicad-cli pcb render` (orthographic, transparent background, side top and side bottom), crops the transparent margin
   so the picture is exactly the board silhouette (outline plus any component overhang), caps the long side at --max-px
   and writes

       <out>/<key>_top.png  <out>/<key>_bottom.png  <out>/<key>.json   (one render per board FILE, shared by every part
                                                                          that maps to it)
       <out>/edges.json   Edge.Cuts size, footprint count per side and board thickness, read with KiCad's own python

   `pack_mesh.py --boards <out>` then checks the part -> file map by outline size and silhouette, finds the face frame
   from the part's STL and embeds the pictures. Both the renders and the edge table are cached by the board file's mtime
   and size plus RENDER_VERSION, so a second run with unchanged boards renders nothing.

 Finding KiCad: pcb/tools/kicad_env.py when the project has it (HIL_KICAD_ROOT / HIL_KICAD_CLI, per-user folders kept
 out of the profile); otherwise HIL_KICAD_CLI, HIL_KICAD_ROOT or kicad-cli on PATH. HIL_KICAD_PY names the python that
 has pcbnew when it is not <KiCad root>/bin/python. Software rendering, seconds per board; jobs run below normal
 priority on Windows. Hundreds of DPI are not needed: raise --max-px (2048) when tracks must read zoomed in.
 Exit codes: 0 ok, 1 some board failed, 2 nothing to do or no KiCad / config problem.
=======================================================================================================================
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import viewer_config  # noqa: E402  (shipped beside this file)

RENDER_VERSION = 1                       # bump when the render arguments or the crop logic change (invalidates the cache)
MAX_PX = 1024                            # long side of a finished picture
RENDER_PX = 1800                         # square render canvas the board is cropped out of
BELOW_NORMAL = 0x00004000 if os.name == "nt" else 0   # Windows BELOW_NORMAL_PRIORITY_CLASS; plain 0 elsewhere

# runs inside KiCad's python: Edge.Cuts bbox (mm), footprints on F.Cu / B.Cu, copper layers, stack-up thickness per file
_EDGE_SCRIPT = r'''
import json, sys, pcbnew
out = {}
for f in sys.argv[2:]:
    try:
        b = pcbnew.LoadBoard(f)
        bb = b.GetBoardEdgesBoundingBox()
        fps = list(b.GetFootprints())
        out[f] = dict(w=pcbnew.ToMM(bb.GetWidth()), h=pcbnew.ToMM(bb.GetHeight()), x=pcbnew.ToMM(bb.GetX()), y=pcbnew.ToMM(bb.GetY()),
                      fps_top=sum(1 for p in fps if p.GetLayer() == pcbnew.F_Cu), fps_bot=sum(1 for p in fps if p.GetLayer() == pcbnew.B_Cu),
                      layers=b.GetCopperLayerCount(), thickness=pcbnew.ToMM(b.GetDesignSettings().GetBoardThickness()))
    except Exception as e:
        out[f] = dict(error=str(e))
json.dump(out, open(sys.argv[1], 'w'))
'''


def find_kicad(root):
    """(kicad-cli path, python with pcbnew or None). KiCad's per-user folders are pointed at <root>/.tmp/kicad_home first."""
    tools = os.path.join(root, "pcb", "tools")
    env_mod = None
    if os.path.isfile(os.path.join(tools, "kicad_env.py")):
        sys.path.insert(0, tools)
        try:
            import kicad_env as env_mod  # type: ignore
        except ImportError:
            env_mod = None
    if env_mod is not None:
        env_mod.isolate()                                       # the project's own rule: nothing lands in the user profile
        cli = env_mod.cli()
        kroot = env_mod.kicad_root()
    else:
        home = os.path.join(root, ".tmp", "kicad_home")
        for sub, var in (("config", "KICAD_CONFIG_HOME"), ("documents", "KICAD_DOCUMENTS_HOME"), ("cache", "KICAD_CACHE_HOME")):
            os.makedirs(os.path.join(home, sub), exist_ok=True)
            os.environ[var] = os.path.join(home, sub)
        cli = os.environ.get("HIL_KICAD_CLI", "").strip() or None
        kroot = os.environ.get("HIL_KICAD_ROOT", "").strip() or None
        if not cli and kroot:
            cand = os.path.join(kroot, "bin", "kicad-cli.exe" if os.name == "nt" else "kicad-cli")
            cli = cand if os.path.isfile(cand) else None
        cli = cli or shutil.which("kicad-cli")
        if not cli or not os.path.isfile(cli):
            raise SystemExit("render_boards: kicad-cli not found; set HIL_KICAD_CLI or HIL_KICAD_ROOT (or install KiCad 10)")
    py = os.environ.get("HIL_KICAD_PY", "").strip()
    if not py and kroot:
        cand = os.path.join(kroot, "bin", "python.exe" if os.name == "nt" else "python3")
        py = cand if os.path.isfile(cand) else ""
    if not py:
        py = sys.executable                                     # a distro python that ships pcbnew; edge_info checks the import
    return cli, py


def _stamp(path):
    """mtime_ns + size: the cache identity of an input file (None when the file is missing)."""
    try:
        st = os.stat(path)
        return [st.st_mtime_ns, st.st_size]
    except OSError:
        return None


def edge_info(rels, root, out_dir, py, force=False):
    """Edge.Cuts size, footprint sides and thickness for every board file in `rels` (one KiCad-python call for all stale ones)."""
    cache_path = os.path.join(out_dir, "edges.json")
    cache = {}
    if os.path.exists(cache_path) and not force:
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (ValueError, OSError):
            cache = {}
    stale = [r for r in rels if cache.get(r, {}).get("stamp") != _stamp(os.path.join(root, r)) or "w" not in cache.get(r, {})]
    if not stale:
        return cache
    tmp = os.path.join(out_dir, "_edges_tmp.json")
    files = [os.path.join(root, r) for r in stale]
    try:
        r = subprocess.run([py, "-c", _EDGE_SCRIPT, tmp] + files, capture_output=True, timeout=600, creationflags=BELOW_NORMAL)
    except (OSError, subprocess.TimeoutExpired) as e:
        print("render_boards: KiCad python failed to start:", e)
        return cache
    if r.returncode != 0 or not os.path.exists(tmp):
        print("render_boards: KiCad python could not read the boards (needs pcbnew; set HIL_KICAD_PY):", r.stderr.decode("utf-8", "replace")[-400:])
        return cache
    with open(tmp, encoding="utf-8") as fh:
        got = json.load(fh)
    os.remove(tmp)
    for rel, fp in zip(stale, files):
        d = got.get(fp, {"error": "no result"})
        d["stamp"] = _stamp(fp)
        cache[rel] = d
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1)
    return cache


def _render_one(cli, pcb, side, png, zoom):
    """One kicad-cli render; the RGBA image, or None on failure or timeout."""
    from PIL import Image
    cmd = [cli, "pcb", "render", "-o", png, "--side", side, "--width", str(RENDER_PX), "--height", str(RENDER_PX),
           "--background", "transparent", "--quality", "basic", "--zoom", str(zoom), pcb]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300, creationflags=BELOW_NORMAL)
    except subprocess.TimeoutExpired:
        print("  render timeout", os.path.basename(pcb), side)
        return None
    if r.returncode != 0 or not os.path.exists(png):
        print("  kicad-cli failed", os.path.basename(pcb), side, r.stderr.decode("utf-8", "replace")[-300:])
        return None
    return Image.open(png).convert("RGBA")


def _crop_to_silhouette(img, max_px):
    """Crop to the alpha bounding box (alpha > 8) and cap the long side. Returns (image, bbox, touches_border)."""
    from PIL import Image
    alpha = img.getchannel("A").point(lambda a: 255 if a > 8 else 0)
    bb = alpha.getbbox()
    if not bb:
        return None, None, False
    touches = bb[0] <= 1 or bb[1] <= 1 or bb[2] >= img.width - 1 or bb[3] >= img.height - 1
    c = img.crop(bb)
    k = max(c.width, c.height) / float(max_px)
    if k > 1.0:
        c = c.resize((max(1, round(c.width / k)), max(1, round(c.height / k))), Image.LANCZOS)
    return c, list(bb), touches


def render_board(cli, root, rel, out_dir, max_px=MAX_PX, force=False):
    """Top + bottom picture of one board file. Returns the sidecar dict (also written to <key>.json) or None on failure."""
    pcb = os.path.join(root, rel)
    if not os.path.exists(pcb):
        print("render_boards: missing board file", rel)
        return None
    key = viewer_config.pcb_key(rel)
    side_json = os.path.join(out_dir, key + ".json")
    stamp = _stamp(pcb)
    if not force and os.path.exists(side_json):
        try:
            with open(side_json, encoding="utf-8") as fh:
                old = json.load(fh)
            if old.get("stamp") == stamp and old.get("version") == RENDER_VERSION and old.get("max_px") == max_px \
                    and all(os.path.exists(os.path.join(out_dir, key + "_" + s + ".png")) for s in ("top", "bottom")):
                old["cached"] = True
                return old
        except (ValueError, OSError):
            pass
    info = dict(key=key, pcb=rel, stamp=stamp, version=RENDER_VERSION, max_px=max_px, cached=False)
    for side in ("top", "bottom"):
        raw = os.path.join(out_dir, "_raw_" + key + "_" + side + ".png")
        zoom, got = 1.0, None
        for _ in range(4):                      # a long board can poke out of the canvas at zoom 1: shrink the view until it fits
            img = _render_one(cli, pcb, side, raw, zoom)
            if img is None:
                return None
            c, bb, touches = _crop_to_silhouette(img, max_px)
            if c is None:
                print("  empty render", rel, side)
                return None
            if not touches:
                got = (c, bb)
                break
            zoom *= 0.8
        if got is None:
            print("  board does not fit the canvas", rel, side)
            return None
        c, bb = got
        c.save(os.path.join(out_dir, key + "_" + side + ".png"), optimize=True)
        info[side] = dict(crop_px=bb, zoom=zoom, out_px=[c.width, c.height])
        try:
            os.remove(raw)
        except OSError:
            pass
    with open(side_json, "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1)
    return info


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2].strip())
    ap.add_argument("--out", required=True, help="directory for the PNGs and sidecars (created)")
    ap.add_argument("--config", help="viewer config (default: viewer.config.json beside this script)")
    ap.add_argument("--root", help="project root the board paths are relative to (default: two folders above this script)")
    ap.add_argument("--only", help="render just the file of this CAD part")
    ap.add_argument("--force", action="store_true", help="ignore the cache")
    ap.add_argument("--max-px", type=int, default=MAX_PX)
    a = ap.parse_args()

    try:
        import PIL  # noqa: F401
    except ImportError:
        print("render_boards: needs Pillow (pip install pillow)")
        return 2
    cfg = viewer_config.load(a.config)
    root = viewer_config.project_root(a.root)
    parts = [a.only] if a.only else list(cfg["boards"])
    rels = sorted({cfg["boards"][p] for p in parts if p in cfg["boards"]})
    if not rels:
        print("render_boards: nothing to render: viewer.config.json maps no board" + (f" for {a.only}" if a.only else "")
              + " (fill its \"boards\" table: CAD part name -> .kicad_pcb)")
        return 2
    if not 64 <= a.max_px <= 8192:
        print("render_boards: --max-px must be 64..8192")
        return 2
    cli, py = find_kicad(root)
    os.makedirs(a.out, exist_ok=True)
    edges = edge_info(rels, root, a.out, py, a.force)
    rendered = failed = cached = 0
    for rel in rels:
        info = render_board(cli, root, rel, a.out, a.max_px, a.force)
        if info is None:
            failed += 1
            continue
        cached += 1 if info.get("cached") else 0
        rendered += 0 if info.get("cached") else 1
        e = edges.get(rel, {})
        print(f"{info['key']:36s} edge {e.get('w', 0):6.2f} x {e.get('h', 0):6.2f} mm  top {info['top']['out_px']}  bottom {info['bottom']['out_px']}"
              f"{'  (cached)' if info.get('cached') else ''}")
    print(f"render_boards: board files {len(rels)}: rendered {rendered}, cached {cached}, failed {failed}; parts mapped {sum(1 for p in parts if p in cfg['boards'])}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
