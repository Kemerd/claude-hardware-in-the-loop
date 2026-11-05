#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 board_textures.py - lay KiCad board renders on the PCB parts of a build (used by pack_mesh.py --boards)
=======================================================================================================================
 Interpreter: any Python >= 3.9 with numpy, trimesh (already needed by the packer), Pillow and scipy. Pillow and scipy
              are imported only when a board is actually solved, so a mesh-only pack never needs them.
 Paths: every path arrives as an argument from pack_mesh.py; nothing is read relative to the working directory.

 The problem: a CAD board is a slab (often with component blocks standing on it) and a KiCad render is a flat picture of
 the real board. To paint the picture on the slab the packer must know which face is the top, how the picture is
 rotated, and whether the CAD part really is that board at all. It finds out from the part's own STL:

   1. thin axis of the mesh = the board normal; the largest flat up / down face levels = substrate top / bottom (a PCBA
      part carries component bodies above it, so the substrate is the biggest flat level, not the extreme);
   2. minimum-area rectangle of the footprint = the in-plane axes u / v and the extents;
   3. outline gate: the rectangle must match the KiCad Edge.Cuts size (sorted long / short) within the configured
      tolerance, else the part stays plain and the pack lists the numbers;
   4. the eight ways to lay the render on the slab (2 faces x 4 quarter turns) are scored by the silhouette IoU between
      the STL footprint and the render's alpha (top, and the mirrored bottom); the best must reach `min_iou`;
   5. placements within `tie` of the best are a tie: a symmetric board has several. A builder frame from the config
      ("board_frames") breaks the tie; without one the part is flagged `ambiguous` and the viewer says "orientation
      unverified" instead of claiming the silkscreen sits the right way round.

 Frame convention (same as display surfaces): image-right = u, image-up = v, facing +normal, mm, model frame.
=======================================================================================================================
"""
import base64
import io
import json
import os
import sys

import numpy as np

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import viewer_config  # noqa: E402  (shipped beside this file; pack_mesh.py puts this folder on sys.path)

BOARD_MASK_PX = 256                          # long side of the rasterised footprint masks used for the IoU search
QUARTER = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # image-right axis of a placement in the (u0, v0) plane for k = 0..3 quarter turns
BOARD_BUDGET = 5_900_000                     # bytes of data URLs held in the manifest (about 8 MB on the wire after base64)


def _pil():
    """Pillow modules, or a clear stop saying which option needs them."""
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        raise SystemExit("pack_mesh: board textures need Pillow (pip install pillow); leave out --boards for a mesh-only pack")
    return Image, ImageDraw, ImageFilter


def _min_area_rect(P):
    """Minimum-area bounding rectangle of 2-D points (rotating over the convex hull edges): (u, v, (u_lo, u_hi), (v_lo, v_hi))."""
    try:
        from scipy.spatial import ConvexHull
    except ImportError:
        raise SystemExit("pack_mesh: board textures need scipy (pip install scipy); leave out --boards for a mesh-only pack")
    H = P[ConvexHull(P).vertices]
    best = None
    for i in range(len(H)):
        e = H[(i + 1) % len(H)] - H[i]
        L = float(np.hypot(e[0], e[1]))
        if L < 1e-9:
            continue
        u = e / L
        v = np.array([-u[1], u[0]])
        a, b = H @ u, H @ v
        area = float(np.ptp(a) * np.ptp(b))
        if best is None or area < best[0]:
            best = (area, u, v, (float(a.min()), float(a.max())), (float(b.min()), float(b.max())))
    return best[1:]


def _board_geometry(m):
    """
    Frame of a PCB part from its mesh: normal n (largest component positive), in-plane axes u0 / v0 (u0 x v0 = n), centre c,
    footprint extents a x b, substrate top / bottom heights and the full height range along n. None when not slab-like.
    """
    V = np.asarray(m.vertices, dtype=np.float64)
    if len(V) < 8:
        return None
    c = V.mean(0)
    n0 = np.linalg.eigh(np.cov((V - c).T))[1][:, 0]                                # thin axis = smallest PCA axis
    fn, fa = np.asarray(m.face_normals), np.asarray(m.area_faces)
    sel = np.abs(fn @ n0) > 0.98
    if not sel.any():
        return None
    n1 = (fn[sel] * (fa[sel] * np.sign(fn[sel] @ n0))[:, None]).sum(0)             # area-weighted flat-face normal refines the PCA axis
    n1 /= np.linalg.norm(n1)
    if n1[np.argmax(np.abs(n1))] < 0:
        n1 = -n1
    e1 = np.cross(n1, [1, 0, 0] if abs(n1[0]) < 0.9 else [0, 1, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(n1, e1)                                                          # (e1, e2, n1) right-handed
    P = np.c_[(V - c) @ e1, (V - c) @ e2]
    u2, _, (ulo, uhi), (vlo, vhi) = _min_area_rect(P)
    u0 = u2[0] * e1 + u2[1] * e2
    v0 = np.cross(n1, u0)                                                          # (u0, v0, n1) right-handed
    h = (V - c) @ n1
    hh = (np.asarray(m.triangles_center) - c) @ n1
    lev = {}
    for sgn, nm in ((1, "top"), (-1, "bot")):
        f = (fn @ n1) * sgn > 0.98
        if f.any():
            q = np.round(hh[f] / 0.02).astype(np.int64)                            # 0.02 mm height bins, area-weighted: the substrate is the biggest flat level
            _, inv = np.unique(q, return_inverse=True)
            area = np.bincount(inv, weights=fa[f])
            lev[nm] = float(np.median(hh[f][inv == int(np.argmax(area))]))
    return dict(c=c, n=n1, u0=u0, v0=v0, a=uhi - ulo, b=vhi - vlo, mid_u=0.5 * (ulo + uhi), mid_v=0.5 * (vlo + vhi),
                top=lev.get("top", float(h.max())), bot=lev.get("bot", float(h.min())), hmin=float(h.min()), hmax=float(h.max()))


def _tri_xy(m, g):
    """Footprint triangles in the (u0, v0) frame, mm, relative to the footprint rectangle centre: (F, 3, 2)."""
    T = np.asarray(m.triangles) - g["c"]
    return np.stack([T @ g["u0"] - g["mid_u"], T @ g["v0"] - g["mid_v"]], axis=-1)


def _mask(tri, k, s, ext_u, ext_v, W, H, bottom=False):
    """Boolean H x W mask of the footprint on a picture laid on face s (+-1 = +-n) with k quarter turns; bottom=True is the mirrored back view."""
    Image, ImageDraw, _ = _pil()
    ux, uy = QUARTER[k]
    u = tri[..., 0] * ux + tri[..., 1] * uy
    v = (tri[..., 0] * -uy + tri[..., 1] * ux) * s                                 # image-up = n_out x image-right: the sign follows the face
    if bottom:
        u = -u                                                                     # the back of a board is seen mirrored left-right
    px = (u / ext_u + 0.5) * W
    py = (0.5 - v / ext_v) * H
    img = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(img)
    for t in np.stack([px, py], axis=-1):
        dr.polygon([tuple(p) for p in t], fill=255)
    return np.asarray(img) > 127


def _iou(a, b):
    """Intersection over union of two equal-size boolean masks."""
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


def _alpha_mask(png, W, H):
    """Opaque pixels of a render PNG resampled to the mask grid."""
    Image, _, _ = _pil()
    return np.asarray(Image.open(png).getchannel("A").resize((W, H), Image.BILINEAR)) > 127


def solve_board(name, m, rel, side_info, edge, boards_dir, gate, frames):
    """Place the KiCad render of `rel` on the part's mesh. Returns (entry dict, debug dict); the entry says why when a gate fails."""
    g = _board_geometry(m)
    if g is None:
        return dict(part=name, pcb=rel, textured=False, reason="mesh is not slab-like (no flat face along the thin axis)"), None
    a, b = g["a"], g["b"]
    ent = dict(part=name, pcb=rel, textured=False, obb_mm=[round(a, 3), round(b, 3)], thickness_mm=round(g["hmax"] - g["hmin"], 3))
    if not edge or "w" not in edge:
        ent["reason"] = "no Edge.Cuts size for the board file"
        return ent, None
    ent["edge_mm"] = [round(edge["w"], 3), round(edge["h"], 3)]
    for x, y in zip(sorted([a, b]), sorted([edge["w"], edge["h"]])):                # outline gate: the file must be the board this part was drawn from
        if abs(x - y) > max(gate["size_abs_mm"], gate["size_rel"] * y):
            ent["reason"] = f"outline mismatch: STL {a:.2f} x {b:.2f} mm vs {os.path.basename(rel)} Edge.Cuts {edge['w']:.2f} x {edge['h']:.2f} mm"
            return ent, None
    tw, th = side_info["top"]["out_px"]
    W, H = max(8, round(BOARD_MASK_PX * tw / max(tw, th))), max(8, round(BOARD_MASK_PX * th / max(tw, th)))
    top_a = _alpha_mask(os.path.join(boards_dir, side_info["key"] + "_top.png"), W, H)
    bot_a = _alpha_mask(os.path.join(boards_dir, side_info["key"] + "_bottom.png"), W, H)
    tri = _tri_xy(m, g)
    cands = []
    for s in (1, -1):
        for k in range(4):
            ext_u, ext_v = (a, b) if k % 2 == 0 else (b, a)
            if abs(np.log((ext_u / ext_v) / (tw / th))) > 0.12:                     # the render's aspect must fit this quarter turn
                continue
            it = _iou(_mask(tri, k, s, ext_u, ext_v, W, H), top_a)
            ib = _iou(_mask(tri, k, s, ext_u, ext_v, W, H, bottom=True), bot_a)
            cands.append(dict(s=s, k=k, ext=(ext_u, ext_v), iou_top=it, iou_bot=ib, score=0.5 * (it + ib)))
    if not cands:
        ent["reason"] = f"render aspect {tw}x{th} fits no placement of the {a:.2f} x {b:.2f} mm footprint"
        return ent, None
    # component-side hint: only when the KiCad file has footprints on one copper side and the STL protrudes clearly on exactly one face
    prot_pos, prot_neg = g["hmax"] - g["top"], g["bot"] - g["hmin"]
    hint = 0
    if abs(prot_pos - prot_neg) > 0.3:
        if edge.get("fps_bot", 0) == 0 and edge.get("fps_top", 0) > 0:
            hint = 1 if prot_pos > prot_neg else -1
        elif edge.get("fps_top", 0) == 0 and edge.get("fps_bot", 0) > 0:
            hint = -1 if prot_pos > prot_neg else 1
    best = max(c["score"] for c in cands)
    ties = [c for c in cands if best - c["score"] <= gate["tie"]]
    if hint and any(c["s"] == hint for c in ties):
        ties = [c for c in ties if c["s"] == hint]
    ties.sort(key=lambda c: (-c["s"], c["k"]))                                     # prior: +n face (largest normal component positive), fewest turns
    pick = ties[0]
    # builder frame from the config: the candidate whose top-face normal and image-right axis equal the builder's wins when its score is
    # within `tie` of the best; a frame the STL contradicts is reported (builder_conflict) and the silhouette pick stays
    bf = frames.get(name)
    frame_source, conflict = "silhouette", False
    if bf:
        bu, bn = np.asarray(bf["u"], dtype=float), np.asarray(bf["n"], dtype=float)
        bu, bn = bu / np.linalg.norm(bu), bn / np.linalg.norm(bn)
        match = [c for c in cands if float((c["s"] * g["n"]) @ bn) > 0.99
                 and float((QUARTER[c["k"]][0] * g["u0"] + QUARTER[c["k"]][1] * g["v0"]) @ bu) > 0.99]
        if len(match) == 1 and best - match[0]["score"] <= gate["tie"]:
            pick, ties, frame_source = match[0], [match[0]], "builder"
        else:
            conflict = True
            print(f"  board {name}: builder frame {bf['source']} not among the best silhouette fits (candidates matched: {len(match)}); keeping the silhouette pick")
    rest = [c["score"] for c in cands if c not in ties]
    ent.update(iou=[round(pick["iou_top"], 4), round(pick["iou_bot"], 4)], margin=round(pick["score"] - (max(rest) if rest else 0.0), 4),
               ties=len(ties), ambiguous=len(ties) > 1, side_hint=hint, frame_source=frame_source,
               **(dict(builder_frame=bf["source"], builder_conflict=conflict) if bf else {}))
    if pick["score"] < gate["min_iou"]:
        ent["reason"] = f"best silhouette IoU {pick['score']:.3f} < {gate['min_iou']} (top {pick['iou_top']:.3f}, bottom {pick['iou_bot']:.3f})"
        return ent, None
    # frame: the top picture on the substrate face on the pick's side, the bottom picture on the opposite one
    s, k = pick["s"], pick["k"]
    ux, uy = QUARTER[k]
    n_out = s * g["n"]
    u_t = ux * g["u0"] + uy * g["v0"]
    v_t = np.cross(n_out, u_t)
    h_top, h_bot = (g["top"], g["bot"]) if s == 1 else (g["bot"], g["top"])
    base = g["c"] + g["mid_u"] * g["u0"] + g["mid_v"] * g["v0"]
    r5 = lambda x: [round(float(t), 5) + 0.0 for t in x]                       # + 0.0 turns -0.0 into 0.0
    ent.update(textured=True, img=side_info["key"], size=[round(pick["ext"][0], 3), round(pick["ext"][1], 3)], normal=r5(n_out), u=r5(u_t), v=r5(v_t),
               centre=r5(base + h_top * g["n"]), substrate_mm=round(g["top"] - g["bot"], 3),
               bottom=dict(normal=r5(-n_out), u=r5(-u_t), v=r5(v_t), centre=r5(base + h_bot * g["n"])))
    return ent, dict(tri=tri, k=k, s=s, ext=pick["ext"])


def encode_board_images(keys, boards_dir, budget):
    """WebP data URLs {key: {top, bottom, px_top, px_bottom}}; quality, then size, drop until the URLs fit `budget` bytes."""
    Image, _, _ = _pil()
    src = {(key, side): Image.open(os.path.join(boards_dir, f"{key}_{side}.png")).convert("RGBA") for key in keys for side in ("top", "bottom")}
    total = 0
    for scale in (1.0, 0.8, 0.64, 0.5, 0.4):
        for q in (82, 74, 66, 58, 50):
            out, total = {}, 0
            for (key, side), im in src.items():
                if scale < 1.0:
                    im = im.resize((max(8, round(im.width * scale)), max(8, round(im.height * scale))), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "WEBP", quality=q, method=6, alpha_quality=90)
                url = "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
                total += len(url)
                out.setdefault(key, {})[side] = url
                out[key]["px_" + side] = [im.width, im.height]
            if total <= budget:
                return out, total, dict(scale=scale, quality=q)
    raise SystemExit(f"pack_mesh: board textures do not fit the {budget} byte budget even at 40 % size / q50 ({total} bytes)")


def board_sheet(path, entries, dbg, boards_dir, title):
    """2400 x 1500 review sheet: each textured board's top render with the STL footprint outline (red) laid on it and a caption."""
    Image, ImageDraw, ImageFilter = _pil()
    from PIL import ImageFont
    font = small = None
    for face in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            font, small = ImageFont.truetype(face, 22), ImageFont.truetype(face, 17)
            break
        except OSError:
            continue
    if font is None:
        font = small = ImageFont.load_default()
    sheet = Image.new("RGB", (2400, 1500), (24, 27, 34))
    dr = ImageDraw.Draw(sheet)
    dr.text((24, 14), title, fill=(232, 234, 240), font=font)
    cols, cw, ch = 5, 480, 460
    for i, e in enumerate([e for e in entries if e.get("textured")][:cols * 3]):
        x0, y0 = (i % cols) * cw, 56 + (i // cols) * ch
        d = dbg[e["part"]]
        im = Image.open(os.path.join(boards_dir, e["img"] + "_top.png")).convert("RGBA")
        k = min((cw - 24) / im.width, 360 / im.height)
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
        tile = Image.new("RGBA", im.size, (70, 74, 84, 255))
        tile.alpha_composite(im)
        tw, th = tile.size
        # STL footprint outline at tile resolution: mask minus its erosion, painted red
        mk = Image.fromarray((_mask(d["tri"], d["k"], d["s"], d["ext"][0], d["ext"][1], tw, th) * 255).astype(np.uint8))
        edge = (np.asarray(mk) > 0) & ~(np.asarray(mk.filter(ImageFilter.MinFilter(3))) > 0)
        px = np.asarray(tile).copy()
        px[edge] = (255, 40, 40, 255)
        sheet.paste(Image.fromarray(px).convert("RGB"), (x0 + 12, y0 + 6))
        dr.text((x0 + 12, y0 + 372), e["part"], fill=(232, 234, 240), font=small)
        dr.text((x0 + 12, y0 + 394), f"{e['size'][0]:.2f} x {e['size'][1]:.2f} mm  IoU {e['iou'][0]:.3f} / {e['iou'][1]:.3f}  ties {e['ties']}"
                f"{'  AMBIGUOUS' if e['ambiguous'] else ''}", fill=(255, 176, 112) if e["ambiguous"] else (156, 192, 255), font=small)
        dr.text((x0 + 12, y0 + 416), f"normal {e['normal']}", fill=(139, 145, 161), font=small)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    sheet.save(path, optimize=True)


def collect(by_name, cfg, boards_dir):
    """
    Everything the manifest needs about boards: (entries, images, stats, debug).
    `by_name` maps part name -> trimesh. Parts the build does not have are skipped; parts with a mapped file but no render
    in `boards_dir` are listed as untextured with the reason.
    """
    entries, dbg = [], {}
    if not os.path.isdir(boards_dir):
        print("pack_mesh: no boards dir at", boards_dir, ": parts stay untextured")
        return entries, {}, {}, dbg
    edges = {}
    try:
        with open(os.path.join(boards_dir, "edges.json"), encoding="utf-8") as fh:
            edges = json.load(fh)
    except (ValueError, OSError):
        print("pack_mesh: boards dir has no edges.json: every board fails the outline check")
    for name, rel in cfg["boards"].items():
        if name not in by_name:
            continue                                                               # this build does not have the part
        side_json = os.path.join(boards_dir, viewer_config.pcb_key(rel) + ".json")
        if not os.path.exists(side_json):
            entries.append(dict(part=name, pcb=rel, textured=False, reason="no render in the boards dir (run render_boards.py)"))
            continue
        with open(side_json, encoding="utf-8") as fh:
            side_info = json.load(fh)
        ent, d = solve_board(name, by_name[name], rel, side_info, edges.get(rel, {}), boards_dir, cfg["board_gate"], cfg["board_frames"])
        entries.append(ent)
        if d:
            dbg[name] = d
    for name in by_name:
        for rx, why in cfg["_no_file"]:
            if rx.search(name):
                entries.append(dict(part=name, textured=False, reason=why))
    # conformal-coat shells: how far each one stands off the board faces (the viewer draws the shell clear, so the picture shows through)
    pre = cfg["coat_prefix"]
    for ent in entries:
        coat = by_name.get(pre + ent["part"]) if pre else None
        if not ent.get("textured") or coat is None:
            continue
        cv = np.asarray(coat.vertices)
        lift = lambda f: round(float(max(0.0, ((cv - np.array(f["centre"])) @ np.array(f["normal"])).max())), 4)
        ent["coat"] = dict(part=pre + ent["part"], lift_top_mm=lift(ent), lift_bottom_mm=lift(ent["bottom"]))
    used = sorted({e["img"] for e in entries if e.get("textured")})
    images, stats = {}, {}
    if used:
        images, total, fit = encode_board_images(used, boards_dir, BOARD_BUDGET)
        stats = dict(images=len(used) * 2, data_url_bytes=total, fit=fit, budget=BOARD_BUDGET)
    return entries, images, stats, dbg
