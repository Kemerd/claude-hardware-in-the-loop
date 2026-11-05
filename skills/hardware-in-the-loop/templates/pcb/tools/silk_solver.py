#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
silk_solver.py - put every reference designator beside its part, as big as the board has room for, overlapping nothing.

Interpreter: any Python >= 3.9 with numpy (imported by place_labels.py under KiCad's python); it reads no files.

Labels as close to their part as possible, as small as they must be, overlapping nothing, with both rotations and every edge of the part tried. Pure numpy, no KiCad here:
place_labels.py hands in the rectangles and writes the answer back, so this file works with any board tool that can produce boxes.

How it works
    Slots       every free spot around every part on a 0.1 mm lattice, at 0 and 90 degrees, and on the diagonals at +-45 as a last resort, plus every position where the letters just touch
                something.  A lattice steps over a gap the letters fit by less than its pitch, and a gap between two parts that fits them by a few hundredths is the only room a small board has.
                A slot is a chain of boxes, one for a straight label, one per character for a slanted one, so a slanted label does not claim the whole square around it.
    Legal       the letters may not touch a pad, a hole, a part body, silk that is already there, or the board edge.  Summed-area tables answer that for thousands of slots at once.  They round
                every edge outward, so what they call blocked but would pass one cell in goes to the rectangles themselves.  The rules are the same numbers either way.
    Cost        distance from the letters to its own part first, then a small price for 90 degrees, for sliding off the edge's middle, for sitting over a via, and a large one for 45 degrees.
                Standing nearer to another part than to its own costs a step and then by the mm: with no leader line a label is read as naming whatever it stands beside.
    Assign      hardest labels first, closest free slot, then ejection chains for what still clashes, then a pull-in pass that never makes an overlap, then small groups taken up and re-seated
                together, which is what brings home a label whose spot is held by a neighbour with no better one of its own.  A plain annealing run from an overlapping start was tried first: it
                pushed labels far out and left them there (41 clashing pairs where this leaves none).
    Size        place_labels.py searches the heights for the largest that leaves no overlap, so a roomy board keeps big text and only a dense one shrinks.
"""
from __future__ import annotations

import math
import random

import numpy as np

CELL = 0.05                      # mm, static obstacle raster: fine enough to hold the 0.15 mm pad rule
OCC_CELL = 0.05                  # mm, label occupancy grid: every edge rounds outward by up to this, so at 0.1 two labels that fit lost up to 0.2 mm of the gap between them
LABEL_GAP = 0.08                 # mm, kept between two labels
GAPS = (0.16, 0.22, 0.30, 0.40, 0.52, 0.66, 0.82, 1.0, 1.25, 1.55, 1.95, 2.5, 3.2, 4.0, 5.0, 6.5, 8.0)     # mm, label box to the part's pads
LATTICE = 0.1                    # mm, slot lattice
REACH = 7.0                      # mm, furthest a label is looked for from its part
MAX_SLOTS = 1800                 # cheapest lattice slots a label keeps
MAX_SNUG = MAX_SLOTS             # cheapest touching slots it keeps on top of those. A quota of their own: they mostly stand beside the part, and sharing one quota they pushed out the far slots a dense board lives on (a dense board went from 0.75 mm down to 0.6). As large as the lattice's, or the far ones are cut again: the only way into a narrow column 2.6 mm out is a touching slot
SEEDS = 24                       # repair orders tried on a face that does not clear at the first
RESEATS, RESEAT_MAX = 40, 1500   # group re-seats tried per label, and per face, so a dense board pays a few seconds and not minutes
RESEAT_GROUP = 6                 # labels taken up at once
POLISH_SEEDS, POLISH_LABELS = 16, 400       # orders tried for the cheapest answer once the size is settled, fewer as a face fills: labels times orders stays near this
W_OVERLAP = 400.0                # cost of one overlapping cell: always worth paying a long way to clear
C_GAP, C_GAP2 = 10.0, 6.0        # cost per mm of distance, and its square: a far label is much worse than a near one
C_ROT90, C_ROT45 = 1.5, 30.0     # 90 degrees is normal on a board, 45 is the last thing to try
C_SLIDE = 2.0
C_VIA = 0.05                     # per 0.05 mm cell of via under a letter, about 2 for a whole via: a dimple, worth a fifth of a mm. At 2.5 a cell a label fled 3 mm from each via and the via fields emptied
C_OTHER, C_OTHER_MM = 12.0, 10.0  # the label stands nearer to another part than to its own: the step, then per mm of how much nearer


def raster(boxes, x0, y0, nx, ny, cell=CELL, grid=None):
    """Mark rectangles (x0, y0, x1, y1) in a boolean grid indexed [ix, iy]."""
    g = np.zeros((nx, ny), bool) if grid is None else grid
    for a, b, c, d in boxes:
        i0, i1 = max(0, int((a - x0) / cell)), min(nx, int(math.ceil((c - x0) / cell)))
        j0, j1 = max(0, int((b - y0) / cell)), min(ny, int(math.ceil((d - y0) / cell)))
        if i1 > i0 and j1 > j0:
            g[i0:i1, j0:j1] = True
    return g


def sat(grid):
    """Summed-area table with a zero row and column in front."""
    t = np.zeros((grid.shape[0] + 1, grid.shape[1] + 1), np.int32)
    t[1:, 1:] = grid.astype(np.int32).cumsum(0).cumsum(1)
    return t


def box_sum(t, i0, j0, i1, j1):
    """Cells set inside index boxes, vectorised."""
    return t[i1, j1] - t[i0, j1] - t[i1, j0] + t[i0, j0]


class Side:
    """One face of the board: where nothing may go, and what costs a little."""

    def __init__(self, bounds, hard_boxes, soft_boxes, outside):
        self.x0, self.y0, x1, y1 = bounds
        self.nx, self.ny = int(math.ceil((x1 - self.x0) / CELL)) + 1, int(math.ceil((y1 - self.y0) / CELL)) + 1
        g = raster(hard_boxes, self.x0, self.y0, self.nx, self.ny)
        self.out = None
        if outside is not None:
            g |= outside
            self.out = sat(outside)
        self.hard = sat(g)
        self.soft = sat(raster(soft_boxes, self.x0, self.y0, self.nx, self.ny))
        self.rects = np.array(hard_boxes, float).reshape(-1, 4)         # the same boxes unrounded, for what the raster cannot call

    def cells(self, table, x0, y0, x1, y1):
        """Cells set in a summed-area table under boxes given as arrays, mm."""
        i0 = np.clip(((x0 - self.x0) / CELL).astype(int), 0, self.nx)
        j0 = np.clip(((y0 - self.y0) / CELL).astype(int), 0, self.ny)
        i1 = np.clip(np.ceil((x1 - self.x0) / CELL).astype(int), 0, self.nx)
        j1 = np.clip(np.ceil((y1 - self.y0) / CELL).astype(int), 0, self.ny)
        return box_sum(table, i0, j0, i1, j1)

    def query(self, x0, y0, x1, y1):
        """(hard cells, soft cells) under boxes given as arrays, mm. A box past the grid counts as blocked."""
        off = (x0 < self.x0) | (y0 < self.y0) | (x1 >= self.x0 + (self.nx - 1) * CELL) | (y1 >= self.y0 + (self.ny - 1) * CELL)
        return self.cells(self.hard, x0, y0, x1, y1) + off * 1000, self.cells(self.soft, x0, y0, x1, y1)

    def legal(self, x0, y0, x1, y1):
        """True where a box touches nothing hard. The raster settles nearly all of them; one it blocks but would pass a cell in from each side stands within a cell of something, and
        only the rectangles can tell whether it really touches. The board outline stays a raster: its rounding errs to the inside of the edge gap."""
        ok = self.query(x0, y0, x1, y1)[0] == 0
        todo = np.flatnonzero(~ok & (self.query(x0 + CELL, y0 + CELL, x1 - CELL, y1 - CELL)[0] == 0))
        if self.out is not None and len(todo):
            todo = todo[self.cells(self.out, x0[todo], y0[todo], x1[todo], y1[todo]) == 0]
        for at in range(0, len(todo), 4096):                 # in chunks: a dense board has thousands of these against hundreds of rectangles
            t = todo[at:at + 4096]
            a, b, c, d = x0[t], y0[t], x1[t], y1[t]
            r = self.rects[(self.rects[:, 2] > a.min()) & (self.rects[:, 0] < c.max()) & (self.rects[:, 3] > b.min()) & (self.rects[:, 1] < d.max())]
            hit = ((a[:, None] < r[:, 2]) & (c[:, None] > r[:, 0]) & (b[:, None] < r[:, 3]) & (d[:, None] > r[:, 1])).any(axis=1)
            ok[t[~hit]] = True
        return ok

    def snug(self, ink, win):
        """Positions, inside win (x0, y0, x1, y1), where letters of extent ink (left, top, right, bottom, from the position) just touch a hard box. Rounded away from the box onto 0.01 mm,
        which also folds a row of pads into one line."""
        il, it, ir, ib = ink
        r = self.rects[(self.rects[:, 2] > win[0] + il) & (self.rects[:, 0] < win[2] + ir) & (self.rects[:, 3] > win[1] + it) & (self.rects[:, 1] < win[3] + ib)]
        xs = np.concatenate([np.ceil((r[:, 2] - il) * 100) / 100, np.floor((r[:, 0] - ir) * 100) / 100])
        ys = np.concatenate([np.ceil((r[:, 3] - it) * 100) / 100, np.floor((r[:, 1] - ib) * 100) / 100])
        return np.unique(xs[(xs >= win[0]) & (xs <= win[2])]), np.unique(ys[(ys >= win[1]) & (ys <= win[3])])


def slots_of(lab, others, side, slant=True):
    """Every legal spot around a part, cheapest first: list of (cost, cx, cy, angle, boxes, touching). A 0.1 mm lattice out to REACH mm, both rotations: that covers each edge, each corner and the gaps
    between neighbours, where a fixed set of offsets along the edges leaves free board unused. The lattice alone cannot enter a gap narrower than itself plus the letters, so every position where
    the letters touch something joins it."""
    px0, py0, px1, py1 = lab["box"]
    w, h = lab["w"], lab["h"]
    mx, my = (px0 + px1) / 2.0, (py0 + py1) / 2.0
    out = []
    # the two box sizes are measured on the board at each text angle: a text angle turns with its footprint, so "0" lies along x only on an unrotated part
    for ang, hw, hh, ink in ((0.0, lab["w0"] / 2, lab["h0"] / 2, lab.get("ink0")), (90.0, lab["w90"] / 2, lab["h90"] / 2, lab.get("ink90"))):
        # pads, lines and parts are measured against the letters; labels against each other keep the full box, which is what a design-rule check reads
        if ink is None:                      # a caller that did not measure the letters: the box, no taller than the text
            tw, th = (hw, min(hh, lab.get("across", 1e9) / 2)) if hw >= hh else (min(hw, lab.get("across", 1e9) / 2), hh)
            ink = (-tw, -th, tw, th)
        il, it, ir, ib = ink
        xs = np.arange(px0 - REACH - hw, px1 + REACH + hw + 1e-6, LATTICE)
        ys = np.arange(py0 - REACH - hh, py1 + REACH + hh + 1e-6, LATTICE)
        sx, sy = side.snug(ink, (xs[0], ys[0], xs[-1], ys[-1]))
        # fine beside the part, coarser further out, so the slots a label keeps reach across the board and do not all crowd the same millimetre as its neighbours' slots. A touching line is
        # never thinned on its own axis: it is the only way into its gap, and the label can still slide along it at the coarse step
        every = lambda n, snug, step: np.concatenate([np.arange(n) % step == 0, np.ones(len(snug), bool)])
        by2, by4 = np.outer(every(len(xs), sx, 2), every(len(ys), sy, 2)).ravel(), np.outer(every(len(xs), sx, 4), every(len(ys), sy, 4)).ravel()
        X, Y = np.meshgrid(np.concatenate([xs, sx]), np.concatenate([ys, sy]), indexing="ij")
        cx, cy = X.ravel(), Y.ravel()
        snug = ~np.outer(np.arange(len(xs) + len(sx)) < len(xs), np.arange(len(ys) + len(sy)) < len(ys)).ravel()
        gap = _gap(cx + il, cy + it, cx + ir, cy + ib, lab["box"])
        keep = ((gap < 0.8) | by2) & ((gap < 2.5) | by4)
        cx, cy, gap, snug = cx[keep], cy[keep], gap[keep], snug[keep]
        keep = side.legal(cx + il, cy + it, cx + ir, cy + ib)
        cx, cy, gap, snug = cx[keep], cy[keep], gap[keep], snug[keep]
        if not len(cx):
            continue
        soft = side.query(cx + il, cy + it, cx + ir, cy + ib)[1]
        # centred on an edge reads best: a small price for sitting off the part's axes
        off = np.minimum(np.abs(cx - mx) / max(w, 0.5), np.abs(cy - my) / max(w, 0.5))
        cost = C_GAP * gap + C_GAP2 * gap * gap + (0.0 if hw >= hh else C_ROT90) + C_SLIDE * np.minimum(off, 1.5) + C_VIA * soft
        order = np.argsort(cost)
        top = np.concatenate([order[~snug[order]][:MAX_SLOTS], order[snug[order]][:MAX_SNUG]])
        cx, cy, gap, cost, snug = cx[top], cy[top], gap[top], cost[top], snug[top]
        other = _nearest_other(cx + il, cy + it, cx + ir, cy + ib, others)
        cost = cost + np.where(other < gap, C_OTHER + C_OTHER_MM * (gap - other), 0.0)
        out += [(float(c), float(x), float(y), ang, [(float(x) - hw, float(y) - hh, float(x) + hw, float(y) + hh)], bool(t)) for c, x, y, t in zip(cost, cx, cy, snug)]
    if slant:
        out += _slanted(lab, side, others)
    out.sort(key=lambda q: q[0])
    return sorted([q for q in out if not q[5]][:MAX_SLOTS] + [q for q in out if q[5]][:MAX_SNUG], key=lambda q: q[0])


def _gap(x0, y0, x1, y1, box):
    """Distance from boxes given as arrays to one box, mm: 0 where they touch."""
    return np.hypot(np.maximum(np.maximum(box[0] - x1, x0 - box[2]), 0.0), np.maximum(np.maximum(box[1] - y1, y0 - box[3]), 0.0))


def _nearest_other(x0, y0, x1, y1, others):
    """Distance from each box given as arrays to the nearest box of another part."""
    if not len(others):
        return np.full(x0.shape, 1e9)
    dx = np.maximum(np.maximum(others[:, 0][None, :] - x1[:, None], x0[:, None] - others[:, 2][None, :]), 0.0)
    dy = np.maximum(np.maximum(others[:, 1][None, :] - y1[:, None], y0[:, None] - others[:, 3][None, :]), 0.0)
    return np.hypot(dx, dy).min(axis=1)


def _slanted(lab, side, others):
    """+-45 degree slots off the corners and edge midpoints: the label as a chain of character squares."""
    px0, py0, px1, py1 = lab["box"]
    w, h, n = lab["w"], lab["h"], max(1, lab["chars"])
    step = w / n
    out = []
    c45 = math.sqrt(0.5)
    half = 0.5 * h * c45 + 0.5 * step * c45
    for g in GAPS[:12]:
        for sx, sy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            for ang in (45.0, -45.0):
                ux, uy = c45, (-c45 if ang > 0 else c45)       # text direction in board frame: KiCad angles run counter-clockwise on screen, and y points down
                # the label's near end at the corner, running away from the part
                dirx, diry = (ux, uy) if ux * sx + uy * sy > 0 else (-ux, -uy)
                ex = (px1 if sx > 0 else px0) + sx * (g + half)
                ey = (py1 if sy > 0 else py0) + sy * (g + half)
                pts = [(ex + dirx * (k + 0.5) * step, ey + diry * (k + 0.5) * step) for k in range(n)]
                bx0 = np.array([p[0] - half for p in pts]); by0 = np.array([p[1] - half for p in pts])
                bx1 = np.array([p[0] + half for p in pts]); by1 = np.array([p[1] + half for p in pts])
                hard, soft = side.query(bx0, by0, bx1, by1)
                if hard.sum() > 0:
                    continue
                mx, my = float(np.mean([p[0] for p in pts])), float(np.mean([p[1] for p in pts]))
                d = g * math.sqrt(2.0)                         # g runs along both axes off the corner: this is the real distance
                c = C_GAP * d + C_GAP2 * d * d + C_ROT45 + C_VIA * float(soft.sum())
                out.append((c, mx, my, ang, list(zip(bx0.tolist(), by0.tolist(), bx1.tolist(), by1.tolist())), False))
    return out


class Occ:
    """Label occupancy: how many labels claim each cell, and one label that claims it."""

    def __init__(self, bounds):
        self.x0, self.y0, x1, y1 = bounds
        self.nx, self.ny = int(math.ceil((x1 - self.x0) / OCC_CELL)) + 2, int(math.ceil((y1 - self.y0) / OCC_CELL)) + 2
        self.g = np.zeros((self.nx, self.ny), np.int16)
        self.own = np.full((self.nx, self.ny), -1, np.int16)

    def idx(self, b):
        """Index window of a box, grown by half the label gap on each side."""
        p = LABEL_GAP / 2
        return (max(0, int((b[0] - p - self.x0) / OCC_CELL)), max(0, int((b[1] - p - self.y0) / OCC_CELL)),
                min(self.nx, int(math.ceil((b[2] + p - self.x0) / OCC_CELL))), min(self.ny, int(math.ceil((b[3] + p - self.y0) / OCC_CELL))))

    def put(self, win, who):
        for i0, j0, i1, j1 in win:
            self.g[i0:i1, j0:j1] += 1
            self.own[i0:i1, j0:j1] = who

    def take(self, win, who):
        for i0, j0, i1, j1 in win:
            self.g[i0:i1, j0:j1] -= 1
            part = self.own[i0:i1, j0:j1]
            part[part == who] = -1

    def hits(self, win):
        """Cells of this window that a label already claims."""
        n = 0
        for i0, j0, i1, j1 in win:
            n += int(np.count_nonzero(self.g[i0:i1, j0:j1]))
        return n

    def who(self, win):
        """The labels standing in this window."""
        out = set()
        for i0, j0, i1, j1 in win:
            out.update(int(v) for v in np.unique(self.own[i0:i1, j0:j1]) if v >= 0)
        return out


def assign(slots, bounds, seed=1, pull=False):
    """Choose a slot per label. Returns (chosen index per label, overlapping cells left). Step 3 runs on an answer that clears, or when pull asks for it: a repair order that does not clear is
    usually thrown away, and on a tight face it was most of the run time.

    1. Hardest first: the label with the fewest slots near its part takes the closest free one, so a label in a tight cluster is not shut out by one that had room to go elsewhere.
    2. Repair: a clashing label takes any free slot; if none, the labels in the way of one of its slots step aside to free slots of their own (an ejection chain).
    3. Pull in: labels furthest out first, each takes a closer free slot, or a closer slot whose occupants can step aside for less than it gains. No step makes an overlap.
    4. Re-seat: a label, far ones most often, and the labels in the way of its better slots are all taken up and put back in shuffled order. Kept only if the group's cost falls.
    """
    rng = random.Random(seed)
    n = len(slots)
    occ = Occ(bounds)
    wins = [[[occ.idx(b) for b in s[4]] for s in ss] for ss in slots]
    k = [0] * n
    near_cost = C_GAP + C_GAP2
    order = sorted(range(n), key=lambda i: (sum(1 for s in slots[i] if s[0] < near_cost), slots[i][0][0]))
    for i in order:
        best, best_h = 0, 1 << 30
        for j in range(len(slots[i])):
            h = occ.hits(wins[i][j])
            if h < best_h:
                best, best_h = j, h
                if h == 0:
                    break
        k[i] = best
        occ.put(wins[i][best], i)

    def free_slot(i, upto=None):
        """Closest slot of label i that touches no other label, i itself lifted off by the caller."""
        for j in range(len(slots[i]) if upto is None else upto):
            if occ.hits(wins[i][j]) == 0:
                return j
        return None

    def distinct_blockers(i, upto):
        """Slots of label i below upto worth an ejection chain, cheapest first. Most slots in a cluster are blocked by the same few labels and would fail the same way, so three per set of them."""
        seen = {}
        for j in range(upto):
            them = frozenset(occ.who(wins[i][j]))
            if them and seen.get(them, 0) < 3:
                seen[them] = seen.get(them, 0) + 1
                yield j

    def eject(i, j, limit):
        """Put label i in slot j, moving what stands there to free slots of their own. Labels i is lifted. Returns True if done and worth it: limit is the most the move may cost."""
        them = occ.who(wins[i][j])
        if not them or len(them) > 3:
            return False
        was = {b: k[b] for b in them}
        for b in them:
            occ.take(wins[b][k[b]], b)
        occ.put(wins[i][j], i)
        extra, done = 0.0, True
        moved = []
        for b in them:
            jb = free_slot(b)
            if jb is None:
                done = False
                break
            extra += slots[b][jb][0] - slots[b][was[b]][0]
            k[b] = jb
            occ.put(wins[b][jb], b)
            moved.append(b)
        if done and slots[i][j][0] + extra < limit:
            k[i] = j
            return True
        for b in moved:
            occ.take(wins[b][k[b]], b)
        occ.take(wins[i][j], i)
        for b in them:
            k[b] = was[b]
            occ.put(wins[b][k[b]], b)
        return False

    def clashing():
        """Labels overlapping another label now."""
        out = []
        for i in range(n):
            occ.take(wins[i][k[i]], i)
            if occ.hits(wins[i][k[i]]):
                out.append(i)
            occ.put(wins[i][k[i]], i)
        return out

    for _round in range(40):
        bad = clashing()
        if not bad:
            break
        rng.shuffle(bad)
        fixed = 0
        for i in bad:
            occ.take(wins[i][k[i]], i)
            if occ.hits(wins[i][k[i]]) == 0:                 # an earlier move cleared it
                occ.put(wins[i][k[i]], i)
                continue
            j = free_slot(i)
            if j is not None:
                k[i] = j
                occ.put(wins[i][j], i)
                fixed += 1
                continue
            done = False
            for j in distinct_blockers(i, len(slots[i])):
                if eject(i, j, 1e12):
                    done = True
                    fixed += 1
                    break
            if not done:
                occ.put(wins[i][k[i]], i)
        if not fixed:
            break

    def overlap_left():
        """Cells where one label stands on another."""
        cells = 0
        for i in range(n):
            occ.take(wins[i][k[i]], i)
            cells += occ.hits(wins[i][k[i]])
            occ.put(wins[i][k[i]], i)
        return cells

    if not pull and overlap_left():
        return k, overlap_left()
    for _round in range(10):
        pulled = 0
        for i in sorted(range(n), key=lambda q: -slots[q][k[q]][0]):
            cur = k[i]
            if cur == 0:
                continue
            occ.take(wins[i][cur], i)
            if occ.hits(wins[i][cur]):                       # still clashing: repair's job, not this pass's
                occ.put(wins[i][cur], i)
                continue
            j = free_slot(i, cur)
            if j is not None:
                k[i] = j
                occ.put(wins[i][j], i)
                pulled += 1
                continue
            done = False
            for j in distinct_blockers(i, cur):
                if eject(i, j, slots[i][cur][0] - 0.5):
                    done = True
                    pulled += 1
                    break
            if not done:
                occ.put(wins[i][cur], i)
        if not pulled:
            break

    for _try in range(min(RESEATS * n, RESEAT_MAX)):
        i = rng.choices(range(n), weights=[slots[q][k[q]][0] + 1.0 for q in range(n)])[0]
        # the labels in its way, then the labels in their way: the one holding a label's home often has nowhere to go until a third label moves
        them, queue = set(), [i]
        while queue and len(them) < RESEAT_GROUP - 1:
            a = queue.pop(0)
            for j in range(0, min(k[a], 300), 3):
                new = occ.who(wins[a][j]) - them - {i}
                them |= new
                queue += sorted(new)
                if len(them) >= RESEAT_GROUP - 1:
                    break
        if not them:
            continue
        group = [i] + rng.sample(sorted(them), min(len(them), RESEAT_GROUP - 1))
        was = {b: k[b] for b in group}
        before = sum(slots[b][k[b]][0] for b in group)
        for b in group:
            occ.take(wins[b][k[b]], b)
        rest = group[1:]
        rng.shuffle(rest)
        group = group[:1] + rest                             # the label this is for chooses first: seated last it finds its home taken again
        seated = []
        for b in group:
            j = free_slot(b)
            if j is None:
                break
            k[b] = j
            occ.put(wins[b][j], b)
            seated.append(b)
        if len(seated) == len(group) and sum(slots[b][k[b]][0] for b in group) < before - 0.01:
            continue
        for b in seated:                                     # worse, or someone found no seat: everything goes back
            occ.take(wins[b][k[b]], b)
        for b in group:
            k[b] = was[b]
            occ.put(wins[b][k[b]], b)
    return k, overlap_left()


def solve_side(labels, side, bounds, slant=True, seed=1, polish=False):
    """One face at one text size. Returns dict(ok, left, no_slot, chosen {ref: (cx, cy, angle, gap cost)}, slanted). polish: a clear answer still carries the luck of the first order that cleared,
    one label sent 6 mm where another order sends it 2, so more orders are tried and the cheapest kept. Left off while searching for the size, where it would only cost time."""
    boxes = np.array([q["box"] for q in labels], float) if labels else np.zeros((0, 4))
    slots, no_slot = [], []
    use = []
    for i, q in enumerate(labels):
        others = np.delete(boxes, i, axis=0)
        s = slots_of(q, others, side, slant)
        if s:
            slots.append(s); use.append(q)
        else:
            no_slot.append(q["ref"])
    if not slots:
        return dict(ok=not no_slot, left=0, no_slot=no_slot, chosen={}, slanted=0)
    k, left = assign(slots, bounds, seed=seed)
    best = seed
    for s in range(seed + 1, seed + SEEDS):            # the repair pass takes clashing labels in shuffled order: a tight board can clear under another order
        if left == 0:
            break
        k2, left2 = assign(slots, bounds, seed=s)
        if left2 < left:
            k, left, best = k2, left2, s
    if left:                                           # nothing cleared: the best order still stands, so its labels come in too
        k, left = assign(slots, bounds, seed=best, pull=True)
    elif polish:
        total = lambda ks: sum(slots[i][ks[i]][0] for i in range(len(use)))
        for s in range(best + 1, best + 1 + min(POLISH_SEEDS, POLISH_LABELS // max(len(use), 1))):
            k2, left2 = assign(slots, bounds, seed=s)
            if left2 == 0 and total(k2) < total(k):
                k = k2
    chosen = {q["ref"]: slots[i][k[i]][:4] for i, q in enumerate(use)}
    return dict(ok=left == 0 and not no_slot, left=left, no_slot=no_slot, chosen=chosen, slanted=sum(1 for v in chosen.values() if abs(v[3]) == 45.0))
