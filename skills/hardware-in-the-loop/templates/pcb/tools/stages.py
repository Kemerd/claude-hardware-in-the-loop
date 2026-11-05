#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stages.py - a board's stages on the silkscreen: one box and one title per stage on each face, and a report on how far the board really is built in stages.

The table says what each stage is; this
tool draws what the placement gives. The boxes come from the parts, never by hand, so a box cannot drift away from the board it describes. And: "We want stages, but there will always be those weird
things that don't fit ... FOLLOW THE RULE as much as you can, not NEVER deviate." So this reports and counts, it does not block: only a table that names something the board does not have is an error.

    python pcb/tools/stages.py draw  <board.kicad_pcb> [--table t.json]                  boxes and titles onto the board, replacing the ones drawn before
    python pcb/tools/stages.py check <board.kicad_pcb> [--table t.json] [--json out]     the report: exit 0 with warnings counted, 1 errors, 2 no table or a broken one

Order of a board's last steps: place, route, `stages.py draw`, `place_labels.py`, `stages.py check`. Labels go last: boxes are drawn as four lines and titles as text, and place_labels treats
both as obstacles. A closed rectangle would count as one solid block the size of the stage and push every label out of it, which is why this draws lines.

The table, JSON, found beside the board as <board>.stages.json or stages.json, or stages.json one folder up (only the first three keys of a stage are required):
    {"grid": 0.5, "margin": 0.2, "title": 1.0,                                            box edges on this grid, gap from the courtyards to the box, title height, mm (all optional)
     "exceptions": {"R12": "add-on pull-up, stands where the wire passes"},               a part that does not fit, and why: it stretches no box and counts as no stray. Board-only footprints never need listing
     "stages": [{"name": "PUSH-PULL DRIVER",                                              the title on the silkscreen
                 "job": "turns the inverter's logic into gate current",                   one line: a stage nobody can say this about is not a stage
                 "parts": ["U1", "Q1-Q4", "R7", "C?"],                                    exact refs, ranges, or globs
                 "kind": "real",                                                          real = a circuit with its own way in and out; partial = grouped, but forced in part, say why in "note"
                 "in": ["GATE_[AB]"], "out": ["DRV_[AB]"],                                full-match patterns over the nets of its parts; each must match, or the table is out of date
                 "note": "...", "faces": "F", "box": false,                               faces that get a box (default both); box false = parts stay listed, no box (a stage that turned out a fiction)
                 "title_out": true,                                                       title against the outside of the box, not in it: the spot inside is worth more to the labels (small boards)
                 "title_edge": "W"}]}                                                     of N, S, E, W on the sheet: the edge it stands against when outside, when another edge is the only home of the labels beside it

Errors, exit 1: a name in the table that is not on the board, a part in two stages. Warnings, counted: a part in no stage, a part of another stage with its middle inside a box, two boxes overlapping past overlap_ok mm
(default 1.0: a shared edge or a small overlap is fine), a box off the board, a box line over a pad, no room for a title on any of its faces, a title that could not stand in or against its own box (title_far: the
floorplan has to leave it room), an in or out that matches no net, a drawn box that no longer matches the parts.

A title has to read as naming its own box and no other, so place_title takes the first of these that exists: inside at a corner, or against an edge as near a corner as the parts allow; outside against one edge of its
box, touching distance, nearer to that box than to any other; inside anywhere; the closest free spot, flagged title_far.
Interpreter: KiCad's bundled python (pcbnew, numpy); started again under it from plain python, HIL_KICAD_PY names it and HIL_KICAD_HOME optionally moves its settings folders. Nothing board-specific here.
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
CELL = 0.05                              # mm, raster of the title search: it decides how close "touching" can come
TITLE_RATIO = 0.8                        # letter width over height
TITLE_REACH = 3.0                        # mm, furthest a title is looked for from its box
TITLE_CLEAR = 0.3                        # mm, how much further from any other box than from its own: a title merely 0.01 nearer its own box still reads as heading the other one
T_CORNER, T_EDGE, T_INSIDE, T_FAR = 0.0, 100.0, 200.0, 1000.0      # place_title's order of preference: each one beats everything after it
T_ROT, T_ENDWAYS = 3.0, 8.0              # a title on its side; one that meets its edge with its short end, which reads as a word beside a box and not as the box's name
DEFAULTS = dict(grid=0.5, margin=0.2, line=0.15, title=1.0, title_stroke=0.15, title_gap=0.05, title_pad=0.15, edge=0.2, overlap_ok=1.0)
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
        if s.get("title_edge", "N") not in ("N", "S", "E", "W"):
            raise ValueError("%s: %s: title_edge must be N, S, E or W" % (path, s["name"]))
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


def within(p, b):
    """A part stands in a box when its middle does: a courtyard the box line clips is a tight board, not a stray."""
    x, y = (p[0] + p[2]) / 2, (p[1] + p[3]) / 2
    return b[0] < x < b[2] and b[1] < y < b[3]


def snap_out(box, g):
    """Grow a box out to the grid: round numbers, so neighbouring boxes come out in line."""
    return (math.floor(box[0] / g + 1e-6) * g, math.floor(box[1] / g + 1e-6) * g, math.ceil(box[2] / g - 1e-6) * g, math.ceil(box[3] / g - 1e-6) * g)


def settle(b, inner, pads, half):
    """A rounded edge that lands on a pad steps back toward the parts, 0.05 mm at a time, down to the courtyards: a line in the clear beats a round number."""
    b = list(b)
    for i, sign in ((0, -1.0), (1, -1.0), (2, 1.0), (3, 1.0)):
        while (b[i] - inner[i]) * sign > 0.05:
            ln = [b[0] - half, b[1] - half, b[2] + half, b[3] + half]
            ln[(i + 2) % 4] = b[i] + half if sign > 0 else b[i] - half
            ln[i] = b[i] - half if sign > 0 else b[i] + half
            ln = (min(ln[0], ln[2]), min(ln[1], ln[3]), max(ln[0], ln[2]), max(ln[1], ln[3]))
            if not any(overlaps(ln, q, 0.0) for _r, _n, q in pads):
                break
            b[i] = round(b[i] - sign * 0.05, 3)
    return tuple(b)


def box_lines(b):
    """The four lines of a box."""
    return ((b[0], b[1], b[2], b[1]), (b[2], b[1], b[2], b[3]), (b[2], b[3], b[0], b[3]), (b[0], b[3], b[0], b[1]))


def read_board(board):
    """{ref: part}, the pads of each face as (ref, number, box), sheet mm, and the boxes among them that are round. A through pad counts on both."""
    import pcbnew
    to = pcbnew.ToMM
    parts, pads, discs = {}, {"F": [], "B": []}, set()
    for fp in board.GetFootprints():
        face = "B" if fp.IsFlipped() else "F"
        nets = set()
        for p in fp.Pads():
            b = p.GetBoundingBox()
            q = (to(b.GetLeft()), to(b.GetTop()), to(b.GetRight()), to(b.GetBottom()))
            drilled = p.GetDrillSize().x > 0
            if p.GetShape(pcbnew.F_Cu) == pcbnew.PAD_SHAPE_CIRCLE:
                discs.add(q)
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
    return parts, pads, discs


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

    def mark_disc(self, b):
        """A round pad or hole. Marking its box would throw away the four corners, and a mounting hole's corners are where a title fits on a small board."""
        np = self.np
        i0, j0, i1, j1 = self.idx(b)
        if i1 <= i0 or j1 <= j0:
            return
        cx, cy, rad = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, (b[2] - b[0]) / 2 + 0.75 * CELL    # a cell's half diagonal over, so a cell the rim crosses always counts as taken
        x = self.x0 + (np.arange(i0, i1) + 0.5) * CELL - cx
        y = self.y0 + (np.arange(j0, j1) + 0.5) * CELL - cy
        self.g[i0:i1, j0:j1] |= x[:, None] ** 2 + y[None, :] ** 2 <= rad * rad

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


def letters_wide(t):
    """Width of a text's letters, mm. KiCad's own box adds a margin past each end, 0.27 mm on a 1.5 mm word, and a title on a small board needs that room."""
    import pcbnew
    ink = pcbnew.SHAPE_POLY_SET()
    t.TransformTextToPolySet(ink, 0, pcbnew.FromMM(0.005), pcbnew.ERROR_INSIDE)
    return pcbnew.ToMM(ink.BBox().GetWidth())


def gap_to(np, X, Y, a, b, box):
    """Gap, mm, from every a x b spot with its corner at (X, Y) to a box: 0 where they touch or overlap."""
    return np.hypot(np.maximum(np.maximum(box[0] - X - a, X - box[2]), 0.0), np.maximum(np.maximum(box[1] - Y - b, Y - box[3]), 0.0))


def place_title(board, r, st, face, box, boxes, cfg):
    """Where a stage's title goes on this face. The letters are measured here, the spot is found by title_spot."""
    t = make_title(board, st["name"], face, cfg)
    # the height is the letters too: KiCad's box adds a line's spacing above them
    wide, tall = letters_wide(t) + 2 * cfg["title_gap"], cfg["title"] + cfg["title_stroke"] + 2 * cfg["title_gap"]
    return title_spot(r, wide, tall, face, box, boxes, cfg["line"], bool(st.get("title_out")), st.get("title_edge", ""))


def title_spot(r, wide, tall, face, box, boxes, line, title_out=False, edge=""):
    """The best free spot of wide x tall mm for a title, in the order at the head of this file. Returns None when nothing is free within reach of the box; a spot that breaks the rule comes back
    with far set, for the report."""
    np = r.np
    touch = line / 2 + 2 * CELL              # half the line, and the raster's rounding on each side of it: nothing can stand in a gap this small
    best = None
    for ang, (a, b) in ((0, (wide, tall)), (90, (tall, wide))):
        free = r.free_spots(a, b)
        if free is None:
            continue
        X, Y = np.meshgrid(r.x0 + np.arange(free.shape[0]) * CELL, r.y0 + np.arange(free.shape[1]) * CELL, indexing="ij")
        # how far each spot stands in from the box's nearer sides: both positive = inside
        in_x, in_y = np.minimum(X - box[0], box[2] - X - a), np.minimum(Y - box[1], box[3] - Y - b)
        inside = (in_x >= 0) & (in_y >= 0)
        own = gap_to(np, X, Y, a, b, box)
        other = np.full(X.shape, 1e9)
        for o in boxes:
            other = np.minimum(other, gap_to(np, X, Y, a, b, o))
        # reading starts at the top left, which on a mirrored face is the top right of the sheet. A small price, it only breaks ties
        start = (X - box[0]) if face == "F" else (box[2] - X - a)
        tie = 0.02 * start + 0.02 * (Y - box[1]) + (T_ROT if ang else 0.0)
        in_ok = inside & (other > 0) & (not title_out)
        # ---- inside: a corner is worth nothing, sliding off it along the edge costs its distance ----
        hug = in_ok & (np.minimum(in_x, in_y) <= touch)
        slide = np.maximum(in_x - touch, 0.0) + np.maximum(in_y - touch, 0.0)
        cost = np.where(hug, T_CORNER + slide + tie, 1e9)
        # ---- outside: against exactly one edge, the title's span over that edge, and no other box as near ----
        over_x, over_y = (X < box[2]) & (X + a > box[0]), (Y < box[3]) & (Y + b > box[1])
        beside = ~inside & (own <= touch) & (over_x != over_y) & (other >= own + TITLE_CLEAR)
        if edge:
            beside &= {"N": Y + b <= box[1] + touch, "S": Y >= box[3] - touch, "W": X + a <= box[0] + touch, "E": X >= box[2] - touch}[edge]
        past = np.where(over_x, np.maximum(np.maximum(box[0] - X, X + a - box[2]), 0.0), np.maximum(np.maximum(box[1] - Y, Y + b - box[3]), 0.0))
        along = over_x if ang == 0 else over_y          # the title's long side lies on the edge it stands against
        cost = np.where(beside, T_EDGE + 2.0 * past + 2.0 * np.maximum(1.0 - other, 0.0) + tie + np.where(over_x & (Y < box[1]), 0.0, 0.5) + np.where(along, 0.0, T_ENDWAYS), cost)
        # ---- what is left: floating inside still names the right box, a far spot does not ----
        cost = np.where(in_ok & ~hug, T_INSIDE + slide + tie, cost)
        far = ~inside & ~beside & (other > 0) & (own <= TITLE_REACH)
        cost = np.where(far, T_FAR + 10.0 * own + tie + np.where(other > own, 0.0, 100.0), cost)      # past that, at least not nearer to a box it does not name
        cost = np.where(free, cost, 1e9)
        i, j = np.unravel_index(int(np.argmin(cost)), cost.shape)
        if cost[i, j] < 1e8 and (best is None or cost[i, j] < best[0]):
            best = (float(cost[i, j]), float(X[i, j]), float(Y[i, j]), a, b, ang, float(own[i, j]))
    if best is None:
        return None
    c, x, y, a, b, ang, own_gap = best
    r.mark((x, y, x + a, y + b))
    return dict(x=round(x + a / 2, 3), y=round(y + b / 2, 3), angle=ang, box=(x, y, x + a, y + b), far=c >= T_FAR, gap=round(own_gap, 2))


# ---- the stages on a board -------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def layout(board, table):
    """Boxes, titles and the problems of a board against its table. Changes nothing on the board."""
    cfg = {k: float(table.get(k, v)) for k, v in DEFAULTS.items()}
    parts, pads, discs = read_board(board)
    refs = list(parts)
    power = re.compile(table.get("power", POWER))
    bad = dict(unknown=[], twice=[], no_stage=[], stray=[], overlap=[], off_board=[], line_on_pad=[], no_title=[], title_far=[], bad_io=[], stale=[])
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
        stages.append(dict(name=st["name"], kind=st.get("kind", "real"), job=st["job"], parts=sorted(set(got)), boxes={}, titles={}, title_out=st.get("title_out", False), title_edge=st.get("title_edge", "")))
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
    inset = ei + cfg["line"] / 2         # edge is what stands between the board edge and the line, not its centre: a line 0.1 mm in has half of itself left and clips the outline
    lim = (round(edge[0] + inset, 2), round(edge[1] + inset, 2), round(edge[2] - inset, 2), round(edge[3] - inset, 2))
    for st, s in zip(table["stages"], stages):
        if st.get("box", True) is False:
            continue
        for face in st.get("faces", "FB"):
            mem = [parts[r]["box"] for r in s["parts"] if parts[r]["face"] == face and r not in excused]
            if not mem:
                continue
            inner = (min(q[0] for q in mem), min(q[1] for q in mem), max(q[2] for q in mem), max(q[3] for q in mem))
            b = snap_out((inner[0] - m, inner[1] - m, inner[2] + m, inner[3] + m), g)
            b = (max(b[0], lim[0]), max(b[1], lim[1]), min(b[2], lim[2]), min(b[3], lim[3]))
            s["boxes"][face] = settle(b, inner, pads[face], cfg["line"] / 2)
    # ---- what a box may not do ----
    import pcbnew
    for s in stages:
        mine = set(s["parts"])
        for face, b in s["boxes"].items():
            bad["stray"] += ["%s %s: %s" % (s["name"], face, r) for r, p in parts.items() if r not in mine and r not in excused and p["face"] == face and within(p["box"], b)]
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
        gap = max(cfg["title_pad"] - cfg["title_gap"], 0.0)       # the title's own size already holds title_gap: together they make the fab's pad gap, not twice it
        for p in parts.values():
            if p["face"] == face:
                ras.mark(p["box"])
        for _r, _n, q in pads[face]:
            (ras.mark_disc if q in discs else ras.mark)((q[0] - gap, q[1] - gap, q[2] + gap, q[3] + gap))
        for s in stages:
            if face in s["boxes"]:
                for x0, y0, x1, y1 in box_lines(s["boxes"][face]):
                    t = cfg["line"] / 2
                    ras.mark((min(x0, x1) - t, min(y0, y1) - t, max(x0, x1) + t, max(y0, y1) + t))
        for s in stages:
            if face in s["boxes"]:
                others = [o["boxes"][face] for o in stages if o is not s and face in o["boxes"]]
                got = place_title(board, ras, s, face, s["boxes"][face], others, cfg)
                if got is not None:
                    s["titles"][face] = got
                    if got["far"]:
                        bad["title_far"].append("%s %s: %.1f mm from its box" % (s["name"], face, got["gap"]))
    # ---- a stage boxed on both faces needs its title on one of them ----
    bad["no_title"] = [s["name"] for s in stages if s["boxes"] and not s["titles"]]
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
