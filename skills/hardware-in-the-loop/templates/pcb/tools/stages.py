#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stages.py - a board's stages on the silkscreen: one box and one title per stage on each face, and a report on how far the board really is built in stages.

The table says what each stage is; this
tool draws what the placement gives. The boxes come from the parts, never by hand, so a box cannot drift away from the board it describes. And: "We want stages, but there will always be those weird
things that don't fit ... FOLLOW THE RULE as much as you can, not NEVER deviate." So this reports and counts, it does not block: only a table that names something the board does not have is an error.

    python pcb/tools/stages.py draw  <board.kicad_pcb> [--table t.json]                  boxes and titles onto the board, replacing the ones drawn before
    python pcb/tools/stages.py check <board.kicad_pcb> [--table t.json] [--json out]     the report: exit 0 with warnings counted, 1 errors, 2 no table or a broken one

Order of a board's last steps: place, route, `stages.py draw`, `place_labels.py`, `stages.py check`. Labels go last: boxes are drawn as four lines and titles as text, and fix_silkscreen treats
both as obstacles. A closed rectangle would count as one solid block the size of the stage and push every label out of it, which is why this draws lines.

The table, JSON, found beside the board as <board>.stages.json or stages.json, or stages.json one folder up (only the first three keys of a stage are required):
    {"grid": 0.5, "margin": 0.2, "title": 1.0,                                            box edges on this grid, gap from the courtyards to the box, title height, mm (all optional)
     "exceptions": {"R12": "add-on pull-up, stands where the wire passes"},               a part that does not fit, and why: it stretches no box and counts as no stray. Board-only footprints never need listing
     "stages": [{"name": "PUSH-PULL DRIVER",                                              the title on the silkscreen
                 "job": "turns the inverter's logic into gate current",                   one line: a stage nobody can say this about is not a stage
                 "parts": ["U1", "Q1-Q4", "R7", "C?"],                                    exact refs, ranges, or globs
                 "kind": "real",                                                          real = a circuit with its own way in and out; partial = grouped, but forced in part, say why in "note"
                 "in": ["GATE_[AB]"], "out": ["DRV_[AB]"],                                full-match patterns over the nets of its parts; each must match, or the table is out of date
                 "note": "...", "faces": "F", "box": false}]}                             faces that get a box (default both); box false = parts stay listed, no box (a stage that turned out a fiction)

Errors, exit 1: a name in the table that is not on the board, a part in two stages. Warnings, counted: a part in no stage, another stage's part inside a box, two boxes overlapping past overlap_ok mm
(default 1.0: a shared edge or a small overlap is fine), a box off the board, a box line over a pad, a title with no room, an in or out that matches no net, a drawn box that no longer matches the parts.
KiCad python, numpy for the title search. Nothing board-specific here.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
GROUP = "stages"                         # KiCad group that holds everything this tool draws, so the next run can find it
CELL = 0.1                               # mm, raster of the title search
TITLE_RATIO = 0.8                        # letter width over height
DEFAULTS = dict(grid=0.5, margin=0.2, line=0.15, title=1.0, title_stroke=0.15, title_gap=0.1, edge=0.2, overlap_ok=1.0)
ERRORS = ("unknown", "twice")            # plainly broken; everything else is a count to bring down
# Rails and ground are not a stage's inputs and outputs: they reach everything. A table can give its own pattern as "power"
POWER = r"(GND.*|[AP]GND|\+.*|VCC.*|VDD.*|VBAT.*|VSYS|VBUS|VIN.*|\d+V\d*.*|-.*)"

# ---- text pass -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
TOKEN = re.compile(r'"(?:[^"\\]|\\.)*"|[()]')


def top_items(txt):
    """(start, end) of every direct child of (kicad_pcb ...). Quoted strings may hold brackets, so they are skipped whole."""
    spans, depth, start = [], 0, 0
    for m in TOKEN.finditer(txt):
        tok = m.group(0)
        if tok == "(":
            depth += 1
            if depth == 2:
                start = m.start()
        elif tok == ")":
            depth -= 1
            if depth == 1:
                spans.append((start, m.end()))
    return spans


def own_items(txt):
    """Spans of the group and its members in a board file. Text, not pcbnew: removing items under live SWIG wrappers corrupts KiCad's heap."""
    spans = top_items(txt)
    grp = next((s for s in spans if re.match(r'\(group\s+"%s"' % GROUP, txt[s[0]:s[0] + 40])), None)
    if grp is None:
        return []
    mem = re.search(r"\(members((?:\s+\"[^\"]*\")*)\s*\)", txt[grp[0]:grp[1]])
    ids = set(re.findall(r'"([^"]*)"', mem.group(1))) if mem else set()
    own = [grp]
    for s in spans:
        if s != grp and txt.startswith(("(gr_line", "(gr_text"), s[0]):
            u = re.search(r'\(uuid\s+"([^"]+)"', txt[s[0]:s[1]])
            if u and u.group(1) in ids:
                own.append(s)
    return sorted(own)


def strip_drawn(path):
    """Take the old boxes and titles out of the file."""
    txt = open(path, encoding="utf-8", newline="").read()
    spans = own_items(txt)
    for a, b in reversed(spans):
        txt = txt[:a].rstrip(" \t") + txt[b:].lstrip("\r\n")
    if spans:
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(txt)
    return len(spans)


def drawn_items(path):
    """What is drawn now: ({(face, x0, y0, x1, y1)} of box lines, {(face, title)})."""
    txt = open(path, encoding="utf-8", newline="").read()
    lines, titles = set(), set()
    for a, b in own_items(txt):
        s = txt[a:b]
        face = "B" if '"B.SilkS"' in s else "F"
        if s.startswith("(gr_line"):
            q = re.search(r"\(start\s+([-\d.]+)\s+([-\d.]+)\)\s*\(end\s+([-\d.]+)\s+([-\d.]+)\)", s)
            if q:
                lines.add(key_line(face, *[float(v) for v in q.groups()]))
        elif s.startswith("(gr_text"):
            titles.add((face, re.match(r'\(gr_text\s+"((?:[^"\\]|\\.)*)"', s).group(1)))
    return lines, titles


def key_line(face, x0, y0, x1, y1):
    """One line whichever way round it was drawn."""
    a, b = sorted([(round(x0, 2), round(y0, 2)), (round(x1, 2), round(y1, 2))])
    return (face, a[0], a[1], b[0], b[1])


# ---- table -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def find_table(board_path):
    """The table a board declares, or None: a board without one is not gated."""
    d, name = os.path.split(os.path.abspath(board_path))
    stem = os.path.splitext(name)[0]
    for p in (os.path.join(d, stem + ".stages.json"), os.path.join(d, "stages.json"), os.path.join(os.path.dirname(d), "stages.json")):
        if os.path.isfile(p):
            return p
    return None


def load_table(path):
    """Read a table and refuse a broken one here, so the board code can trust its shape."""
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    if not isinstance(t, dict) or not isinstance(t.get("stages"), list) or not t["stages"]:
        raise ValueError("%s: no stages list" % path)
    for s in t["stages"]:
        for k in ("name", "job", "parts"):
            if not s.get(k):
                raise ValueError("%s: a stage without %s: %r" % (path, k, s.get("name")))
        if s.get("kind", "real") not in ("real", "partial"):
            raise ValueError("%s: %s: kind must be real or partial" % (path, s["name"]))
    return t


def expand(patterns, refs):
    """Refs matched by exact names, ranges (C510-C548, C510-548) and globs. Returns (matched, patterns that matched nothing)."""
    got, dead = [], []
    for pat in patterns:
        rng = re.match(r"^([A-Za-z_]+)(\d+)-(?:[A-Za-z_]+)?(\d+)$", pat)
        if rng:
            hit = [r for r in refs if re.fullmatch(rng.group(1) + r"\d+", r) and int(rng.group(2)) <= int(re.search(r"\d+$", r).group()) <= int(rng.group(3))]
        elif any(c in pat for c in "*?["):
            hit = [r for r in refs if fnmatch.fnmatchcase(r, pat)]
        else:
            hit = [pat] if pat in refs else []
        got += hit
        if not hit:
            dead.append(pat)
    return got, dead


# ---- geometry --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def overlaps(a, b, tol=0.01):
    """True when two boxes share more than a line. A shared edge lets neighbouring stages line up."""
    return min(a[2], b[2]) - max(a[0], b[0]) > tol and min(a[3], b[3]) - max(a[1], b[1]) > tol


def snap_out(box, g):
    """Grow a box out to the grid: round numbers, so neighbouring boxes come out in line."""
    return (math.floor(box[0] / g + 1e-6) * g, math.floor(box[1] / g + 1e-6) * g, math.ceil(box[2] / g - 1e-6) * g, math.ceil(box[3] / g - 1e-6) * g)


def box_lines(b):
    """The four lines of a box."""
    return ((b[0], b[1], b[2], b[1]), (b[2], b[1], b[2], b[3]), (b[2], b[3], b[0], b[3]), (b[0], b[3], b[0], b[1]))


def read_board(board):
    """{ref: part} and the pad boxes of each face, sheet mm. A through pad counts on both."""
    import pcbnew
    to = pcbnew.ToMM
    parts, pads = {}, {"F": [], "B": []}
    for fp in board.GetFootprints():
        face = "B" if fp.IsFlipped() else "F"
        nets = set()
        for p in fp.Pads():
            b = p.GetBoundingBox()
            q = (to(b.GetLeft()), to(b.GetTop()), to(b.GetRight()), to(b.GetBottom()))
            drilled = p.GetDrillSize().x > 0
            for f, cu in (("F", pcbnew.F_Cu), ("B", pcbnew.B_Cu)):
                if drilled or p.IsOnLayer(cu):
                    pads[f].append((fp.GetReference(), p.GetNumber(), q))
            if p.GetNetname():
                nets.add(p.GetNetname().rsplit("/", 1)[-1])
        if not fp.GetReference() or fp.GetAttributes() & pcbnew.FP_BOARD_ONLY:
            continue                     # holes and logos sit in no stage
        cy = fp.GetCourtyard(pcbnew.B_CrtYd if face == "B" else pcbnew.F_CrtYd)
        b = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False, False)
        parts[fp.GetReference()] = dict(face=face, box=(to(b.GetLeft()), to(b.GetTop()), to(b.GetRight()), to(b.GetBottom())), nets=nets)
    return parts, pads


def outline(board):
    """(polygon set or None, bounding box) of the board edge."""
    import pcbnew
    sps = pcbnew.SHAPE_POLY_SET()
    try:
        ok = board.GetBoardPolygonOutlines(sps, True)
    except TypeError:
        ok = board.GetBoardPolygonOutlines(sps)
    e = board.GetBoardEdgesBoundingBox()
    to = pcbnew.ToMM
    return (sps if ok and sps.OutlineCount() else None), (to(e.GetLeft()), to(e.GetTop()), to(e.GetRight()), to(e.GetBottom()))


class Raster:
    """What is taken on one face, for the title search. A summed-area table finds every free spot of a given size at once."""

    def __init__(self, bounds):
        import numpy as np
        self.np = np
        self.x0, self.y0 = bounds[0], bounds[1]
        self.nx, self.ny = int((bounds[2] - bounds[0]) / CELL) + 2, int((bounds[3] - bounds[1]) / CELL) + 2
        self.g = np.zeros((self.nx, self.ny), bool)

    def idx(self, b):
        """Cell window of a box, clipped."""
        i0, j0 = max(0, int((b[0] - self.x0) / CELL)), max(0, int((b[1] - self.y0) / CELL))
        return i0, j0, min(self.nx, int(math.ceil((b[2] - self.x0) / CELL))), min(self.ny, int(math.ceil((b[3] - self.y0) / CELL)))

    def mark(self, b):
        i0, j0, i1, j1 = self.idx(b)
        if i1 > i0 and j1 > j0:
            self.g[i0:i1, j0:j1] = True

    def mark_outside(self, sps, inset):
        """Everything past the outline, by a crossing test over the outer ring, then grown by the edge clearance."""
        import pcbnew
        np = self.np
        ring = sps.Outline(0)
        pts = [(pcbnew.ToMM(ring.CPoint(i).x), pcbnew.ToMM(ring.CPoint(i).y)) for i in range(ring.PointCount())]
        X, Y = np.meshgrid(self.x0 + (np.arange(self.nx) + 0.5) * CELL, self.y0 + (np.arange(self.ny) + 0.5) * CELL, indexing="ij")
        inside = np.zeros(X.shape, bool)
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            if ay != by:
                inside ^= ((ay > Y) != (by > Y)) & (X < (bx - ax) * (Y - ay) / (by - ay) + ax)
        self.g |= ~inside
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            if ax == bx or ay == by:       # straight edges are what a board has; a curve's tiny facets are covered by the crossing test
                self.mark((min(ax, bx) - inset, min(ay, by) - inset, max(ax, bx) + inset, max(ay, by) + inset))

    def free_spots(self, w, h):
        """Boolean map: True at (i, j) when a box of w x h mm from that cell is clear."""
        np = self.np
        a, b = int(math.ceil(w / CELL)), int(math.ceil(h / CELL))
        if a >= self.nx or b >= self.ny:
            return None
        t = np.zeros((self.nx + 1, self.ny + 1), np.int32)
        t[1:, 1:] = self.g.astype(np.int32).cumsum(0).cumsum(1)
        return (t[a:, b:] - t[:-a, b:] - t[a:, :-b] + t[:-a, :-b]) == 0


def make_title(board, s, face, cfg):
    """A title as a board text, centred on its position, mirrored on the bottom so it reads from that side."""
    import pcbnew
    t = pcbnew.PCB_TEXT(board)
    t.SetText(s)
    t.SetLayer(pcbnew.B_SilkS if face == "B" else pcbnew.F_SilkS)
    h = cfg["title"]
    t.SetTextSize(pcbnew.VECTOR2I(pcbnew.FromMM(h * TITLE_RATIO), pcbnew.FromMM(h)))
    t.SetTextThickness(pcbnew.FromMM(cfg["title_stroke"]))
    t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_CENTER)
    t.SetVertJustify(pcbnew.GR_TEXT_V_ALIGN_CENTER)
    t.SetMirrored(face == "B")
    return t


def place_title(board, r, st, face, box, boxes, cfg):
    """Where the title goes: inside its box along the edge a reader starts from, else any free spot inside, else just outside and in nobody else's box. None when there is no room."""
    import pcbnew
    t = make_title(board, st["name"], face, cfg)
    bb = t.GetBoundingBox()
    w, h = pcbnew.ToMM(bb.GetWidth()) + 2 * cfg["title_gap"], pcbnew.ToMM(bb.GetHeight()) + 2 * cfg["title_gap"]
    np = r.np
    best = None
    for ang, (a, b) in ((0, (w, h)), (90, (h, w))):
        free = r.free_spots(a, b)
        if free is None:
            continue
        xs = r.x0 + np.arange(free.shape[0]) * CELL
        ys = r.y0 + np.arange(free.shape[1]) * CELL
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        inner = (X >= box[0]) & (X + a <= box[2]) & (Y >= box[1]) & (Y + b <= box[3])
        # reading starts at the top left, which on a mirrored face is the top right of the sheet
        along = (X - box[0]) if face == "F" else (box[2] - X - a)
        cost = np.where(inner, along * 0.2 + (Y - box[1]) * 2.0, 1e9) + (3.0 if ang else 0.0)
        reach = (X + a > box[0] - 3.0) & (X < box[2] + 3.0) & (Y + b > box[1] - 3.0) & (Y < box[3] + 3.0)
        outer = reach & ~inner
        for o in boxes:              # no title of this stage inside somebody else's box, nor across this one's lines
            outer &= (X + a <= o[0]) | (X >= o[2]) | (Y + b <= o[1]) | (Y >= o[3])
        cost = np.where(outer, 200.0 + np.abs(Y + b - box[1]) * 2.0 + np.abs(along) * 0.2, cost)
        cost = np.where(free, cost, 1e9)
        i, j = np.unravel_index(int(np.argmin(cost)), cost.shape)
        if cost[i, j] < 1e8 and (best is None or cost[i, j] < best[0]):
            best = (float(cost[i, j]), float(X[i, j]), float(Y[i, j]), a, b, ang)
    if best is None:
        return None
    _c, x, y, a, b, ang = best
    r.mark((x, y, x + a, y + b))
    return dict(x=round(x + a / 2, 3), y=round(y + b / 2, 3), angle=ang, box=(x, y, x + a, y + b))


# ---- the stages on a board -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def layout(board, table):
    """Boxes, titles and the problems of a board against its table. Changes nothing on the board."""
    cfg = {k: float(table.get(k, v)) for k, v in DEFAULTS.items()}
    parts, pads = read_board(board)
    refs = list(parts)
    power = re.compile(table.get("power", POWER))
    bad = dict(unknown=[], twice=[], no_stage=[], stray=[], overlap=[], off_board=[], line_on_pad=[], no_title=[], bad_io=[], stale=[])
    owner, stages = {}, []
    excused = table.get("exceptions", {})
    bad["unknown"] += ["exceptions: " + r for r in excused if r not in parts]
    bad["unknown"] += ["exceptions: %s has no reason" % r for r, why in excused.items() if not str(why).strip()]
    for st in table["stages"]:
        got, dead = expand(st["parts"], refs)
        bad["unknown"] += ["%s: %s" % (st["name"], d) for d in dead]
        for r in got:
            if r in owner and owner[r] != st["name"]:
                bad["twice"].append("%s in %s and %s" % (r, owner[r], st["name"]))
            owner[r] = st["name"]
        stages.append(dict(name=st["name"], kind=st.get("kind", "real"), job=st["job"], parts=sorted(set(got)), boxes={}, titles={}))
    bad["no_stage"] = sorted(r for r in refs if r not in owner and r not in excused)
    # ---- a stage's in and out must be nets its parts really touch ----
    nets_of = {}
    for r, p in parts.items():
        for n in p["nets"]:
            nets_of.setdefault(n, set()).add(r)
    for st, s in zip(table["stages"], stages):
        mine = {n for r in s["parts"] for n in parts[r]["nets"]}
        said = []
        for pat in list(st.get("in", [])) + list(st.get("out", [])):
            hit = [n for n in mine if re.fullmatch(pat, n)]
            said += hit
            if not hit:
                bad["bad_io"].append("%s: %s matches no net of its parts" % (st["name"], pat))
        leave = sorted(n for n in mine if not power.fullmatch(n) and not n.startswith("unconnected") and nets_of[n] - set(s["parts"]))
        s.update(crossing=len(leave), undeclared=sorted(set(leave) - set(said)))
    # ---- boxes come from the parts ----
    sps, edge = outline(board)
    g, m, ei = cfg["grid"], cfg["margin"], cfg["edge"]
    lim = (math.ceil((edge[0] + ei) / g) * g, math.ceil((edge[1] + ei) / g) * g, math.floor((edge[2] - ei) / g) * g, math.floor((edge[3] - ei) / g) * g)
    for st, s in zip(table["stages"], stages):
        if st.get("box", True) is False:
            continue
        for face in st.get("faces", "FB"):
            mem = [parts[r]["box"] for r in s["parts"] if parts[r]["face"] == face and r not in excused]
            if not mem:
                continue
            b = snap_out((min(q[0] for q in mem) - m, min(q[1] for q in mem) - m, max(q[2] for q in mem) + m, max(q[3] for q in mem) + m), g)
            s["boxes"][face] = (max(b[0], lim[0]), max(b[1], lim[1]), min(b[2], lim[2]), min(b[3], lim[3]))
    # ---- what a box may not do ----
    import pcbnew
    for s in stages:
        mine = set(s["parts"])
        for face, b in s["boxes"].items():
            bad["stray"] += ["%s %s: %s" % (s["name"], face, r) for r, p in parts.items() if r not in mine and r not in excused and p["face"] == face and overlaps(b, p["box"], 0.02)]
            for x0, y0, x1, y1 in box_lines(b):
                t = cfg["line"] / 2
                ln = (min(x0, x1) - t, min(y0, y1) - t, max(x0, x1) + t, max(y0, y1) + t)
                bad["line_on_pad"] += ["%s %s: %s pad %s" % (s["name"], face, r, n) for r, n, q in pads[face] if overlaps(ln, q, 0.0)]
                if sps is not None:
                    n = max(2, int(max(abs(x1 - x0), abs(y1 - y0)) / 0.5))
                    if not all(sps.Contains(pcbnew.VECTOR2I(pcbnew.FromMM(x0 + (x1 - x0) * k / n), pcbnew.FromMM(y0 + (y1 - y0) * k / n))) for k in range(n + 1)):
                        bad["off_board"].append("%s %s" % (s["name"], face))
                        break
    for face in "FB":
        on = [(s["name"], s["boxes"][face]) for s in stages if face in s["boxes"]]
        bad["overlap"] += ["%s / %s on %s" % (a[0], c[0], face) for i, a in enumerate(on) for c in on[i + 1:] if overlaps(a[1], c[1], cfg["overlap_ok"])]
    # ---- titles last, round everything else ----
    for face in "FB":
        ras = Raster((edge[0] - 0.5, edge[1] - 0.5, edge[2] + 0.5, edge[3] + 0.5))
        if sps is not None:
            ras.mark_outside(sps, ei)
        gap = cfg["title_gap"]
        for p in parts.values():
            if p["face"] == face:
                ras.mark(p["box"])
        for _r, _n, q in pads[face]:
            ras.mark((q[0] - gap, q[1] - gap, q[2] + gap, q[3] + gap))
        for s in stages:
            if face in s["boxes"]:
                for x0, y0, x1, y1 in box_lines(s["boxes"][face]):
                    t = cfg["line"] / 2
                    ras.mark((min(x0, x1) - t, min(y0, y1) - t, max(x0, x1) + t, max(y0, y1) + t))
        for s in stages:
            if face in s["boxes"]:
                others = [o["boxes"][face] for o in stages if o is not s and face in o["boxes"]]
                got = place_title(board, ras, {"name": s["name"]}, face, s["boxes"][face], others, cfg)
                if got is None:
                    bad["no_title"].append("%s %s" % (s["name"], face))
                else:
                    s["titles"][face] = got
    return dict(cfg=cfg, stages=stages, problems=bad, parts=len(parts), excused=len(excused))


def stale(res, path):
    """A box drawn on the board that the parts no longer give, or a stage not drawn at all."""
    lines, titles = drawn_items(path)
    want, wt = set(), set()
    for s in res["stages"]:
        for face, b in s["boxes"].items():
            want |= {key_line(face, *ln) for ln in box_lines(b)}
            if face in s["titles"]:
                wt.add((face, s["name"]))
    out = ["box line not drawn: %s" % (q,) for q in sorted(want - lines)] + ["line with no stage: %s" % (q,) for q in sorted(lines - want)]
    return out + ["title not drawn: %s %s" % q for q in sorted(wt - titles)] + ["title with no stage: %s %s" % q for q in sorted(titles - wt)]


def check(board, path, table):
    """The whole result, for the command line and for tools/pcb_standards_check.py."""
    res = layout(board, table)
    res["problems"]["stale"] = stale(res, path)
    res["errors"] = sum(len(v) for k, v in res["problems"].items() if k in ERRORS)
    res["warnings"] = sum(len(v) for k, v in res["problems"].items() if k not in ERRORS)
    return res


def draw(path, table):
    """Replace what was drawn. The file loses the old items first, then pcbnew adds the new ones through its own writer."""
    import pcbnew
    strip_drawn(path)
    board = pcbnew.LoadBoard(path)
    res = layout(board, table)
    grp = pcbnew.PCB_GROUP(board)
    grp.SetName(GROUP)
    board.Add(grp)
    cfg = res["cfg"]
    for s in res["stages"]:
        for face, b in s["boxes"].items():
            for x0, y0, x1, y1 in box_lines(b):
                ln = pcbnew.PCB_SHAPE(board)
                ln.SetShape(pcbnew.SHAPE_T_SEGMENT)
                ln.SetLayer(pcbnew.B_SilkS if face == "B" else pcbnew.F_SilkS)
                ln.SetWidth(pcbnew.FromMM(cfg["line"]))
                ln.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
                ln.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
                board.Add(ln)
                grp.AddItem(ln)
            q = s["titles"].get(face)
            if q:
                t = make_title(board, s["name"], face, cfg)
                t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(q["x"]), pcbnew.FromMM(q["y"])))
                t.SetTextAngleDegrees(q["angle"])
                board.Add(t)
                grp.AddItem(t)
    board.Save(path)
    return res


def summary(res):
    """One line per stage, then the counts."""
    print("%-24s %-7s %5s %5s %5s  %s" % ("stage", "kind", "parts", "cross", "undcl", "boxes"))
    for s in res["stages"]:
        print("%-24s %-7s %5d %5d %5d  %s" % (s["name"][:24], s["kind"], len(s["parts"]), s["crossing"], len(s["undeclared"]), " ".join("%s %gx%g" % (f, b[2] - b[0], b[3] - b[1]) for f, b in s["boxes"].items())))
    for k, v in res["problems"].items():
        if v:
            print("  %-5s %-12s %4d  %s" % ("ERROR" if k in ERRORS else "warn", k, len(v), "; ".join(v[:6])[:200]))
    print("stages %d, parts %d, exceptions %d, errors %d, warnings %d" % (len(res["stages"]), res["parts"], res["excused"], res["errors"], res["warnings"]))


def main():
    ap = argparse.ArgumentParser(description="Stage boxes on the silkscreen, and the report on them")
    ap.add_argument("cmd", choices=("draw", "check"))
    ap.add_argument("board")
    ap.add_argument("--table", default="", help="stage table, default beside the board")
    ap.add_argument("--json", default="", help="write the full result here")
    a = ap.parse_args()
    if not os.path.isfile(a.board):
        print("no such board:", a.board, file=sys.stderr)
        return 2
    path = a.table or find_table(a.board)
    if not path:
        print("no stage table for", a.board, file=sys.stderr)
        return 2
    try:
        table = load_table(path)
    except (OSError, ValueError) as e:
        print("bad stage table:", e, file=sys.stderr)
        return 2
    import pcbnew
    if a.cmd == "draw":
        draw(a.board, table)
    res = check(pcbnew.LoadBoard(a.board), a.board, table)
    summary(res)
    if a.json:
        with open(a.json, "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, indent=1)
    return 1 if res["errors"] and a.cmd == "check" else 0


def boot():
    """Under plain python start again under KiCad's, with its per-user folders off the home directory, as the other board tools do."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    home = os.environ.get("HIL_KICAD_HOME", "")
    if home:                                  # optional: keep KiCad's settings out of the user's home folder
        for sub, var in (("config", "KICAD_CONFIG_HOME"), ("documents", "KICAD_DOCUMENTS_HOME"), ("cache", "KICAD_CACHE_HOME")):
            os.environ.setdefault(var, os.path.join(home, sub))
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        py = os.environ.get("HIL_KICAD_PY", r"C:\Program Files\KiCad\10.0\bin\python.exe")
        if not os.path.isfile(py) or os.path.abspath(py).lower() == os.path.abspath(sys.executable).lower():
            print("stages.py: pcbnew not found", file=sys.stderr)
            return 2
        import subprocess
        return subprocess.call([py, "-X", "utf8", os.path.abspath(__file__)] + sys.argv[1:])
    return main()


if __name__ == "__main__":
    sys.exit(boot())
