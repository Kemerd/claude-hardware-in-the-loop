#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
silk_solver.py - put every reference designator beside its part, as big as the board has room for, overlapping nothing.

Interpreter: any Python >= 3.9 with numpy (imported by place_labels.py under KiCad's python); it reads no files.

Goal: labels as close to their part as possible, as small as they must be, overlapping nothing, with both rotations and every edge of the part tried. Pure numpy, no KiCad here:
place_labels.py hands in the rectangles and writes the answer back, so this file works with any board tool that can produce boxes.

How it works
    Slots       every free spot around every part on a 0.1 mm lattice, at 0 and 90 degrees, and on the diagonals at +-45 as a last resort.  A slot is a chain of boxes, one for a straight
                label, one per character for a slanted one, so a slanted label does not claim the whole square around it.
    Legal       a slot may not touch a pad, a hole, a part body, silk that is already there, or the board edge.  Summed-area tables answer that for thousands of slots at once.
    Cost        distance to its own part first, then a small price for 90 degrees, for sliding off the edge's middle, for sitting over a via, for standing nearer to another part than to its
                own, and a large one for 45 degrees.
    Assign      hardest labels first, closest free slot, then ejection chains for what still clashes, then a pull-in pass that never makes an overlap.  A plain annealing run from an overlapping
                start was tried first: it pushed labels far out and left them there (41 clashing pairs where this leaves none).
    Size        place_labels.py searches the heights for the largest that leaves no overlap, so a roomy board keeps big text and only a dense one shrinks.
"""
from __future__ import annotations

import math
import random

import numpy as np

CELL = 0.05                      # mm, static obstacle raster: fine enough to hold the 0.15 mm pad rule
OCC_CELL = 0.1                   # mm, label occupancy grid
LABEL_GAP = 0.08                 # mm, kept between two labels
GAPS = (0.16, 0.22, 0.30, 0.40, 0.52, 0.66, 0.82, 1.0, 1.25, 1.55, 1.95, 2.5, 3.2, 4.0, 5.0, 6.5, 8.0)     # mm, label box to the part's pads
LATTICE = 0.1                    # mm, slot lattice
REACH = 7.0                      # mm, furthest a label is looked for from its part
MAX_SLOTS = 1800                 # cheapest slots a label keeps
W_OVERLAP = 400.0                # cost of one overlapping cell: always worth paying a long way to clear
C_GAP, C_GAP2 = 10.0, 6.0        # cost per mm of distance, and its square: a far label is much worse than a near one
C_ROT90, C_ROT45 = 1.5, 30.0     # 90 degrees is normal on a board, 45 is the last thing to try
C_SLIDE = 2.0
C_VIA = 0.05                     # per 0.05 mm cell of via under a letter, about 2 for a whole via: a dimple, worth a fifth of a mm. At 2.5 a cell a label fled 3 mm from each via and the via fields emptied
C_OTHER = 12.0                   # the label stands nearer to another part than to its own


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
        if outside is not None:
            g |= outside
        self.hard = sat(g)
        self.soft = sat(raster(soft_boxes, self.x0, self.y0, self.nx, self.ny))

    def query(self, x0, y0, x1, y1):
        """(hard cells, soft cells) under boxes given as arrays, mm. A box past the grid counts as blocked."""
        off = (x0 < self.x0) | (y0 < self.y0) | (x1 >= self.x0 + (self.nx - 1) * CELL) | (y1 >= self.y0 + (self.ny - 1) * CELL)
        i0 = np.clip(((x0 - self.x0) / CELL).astype(int), 0, self.nx)
        j0 = np.clip(((y0 - self.y0) / CELL).astype(int), 0, self.ny)
        i1 = np.clip(np.ceil((x1 - self.x0) / CELL).astype(int), 0, self.nx)
        j1 = np.clip(np.ceil((y1 - self.y0) / CELL).astype(int), 0, self.ny)
        return box_sum(self.hard, i0, j0, i1, j1) + off * 1000, box_sum(self.soft, i0, j0, i1, j1)


def slots_of(lab, others, side, slant=True):
    """Every legal spot around a part, cheapest first: list of (cost, cx, cy, angle, boxes). A 0.1 mm lattice out to REACH mm, both rotations: that covers each edge, each corner and the gaps
    between neighbours, where a fixed set of offsets along the edges leaves free board unused."""
    px0, py0, px1, py1 = lab["box"]
    w, h = lab["w"], lab["h"]
    mx, my = (px0 + px1) / 2.0, (py0 + py1) / 2.0
    out = []
    # the two box sizes are measured on the board at each text angle: a text angle turns with its footprint, so "0" lies along x only on an unrotated part
    for ang, hw, hh in ((0.0, lab["w0"] / 2, lab["h0"] / 2), (90.0, lab["w90"] / 2, lab["h90"] / 2)):
        xs = np.arange(px0 - REACH - hw, px1 + REACH + hw + 1e-6, LATTICE)
        ys = np.arange(py0 - REACH - hh, py1 + REACH + hh + 1e-6, LATTICE)
        X, Y = np.meshgrid(xs, ys, indexing="ij")
        I, J = np.meshgrid(np.arange(len(xs)), np.arange(len(ys)), indexing="ij")
        cx, cy, ii, jj = X.ravel(), Y.ravel(), I.ravel(), J.ravel()
        hard, soft = side.query(cx - hw, cy - hh, cx + hw, cy + hh)
        keep = hard == 0
        # fine beside the part, coarser further out, so the slots a label keeps reach across the board and do not all crowd the same millimetre as its neighbours' slots
        g0 = np.hypot(np.maximum(np.maximum(px0 - (cx + hw), (cx - hw) - px1), 0.0), np.maximum(np.maximum(py0 - (cy + hh), (cy - hh) - py1), 0.0))
        keep &= (g0 < 0.8) | ((ii % 2 == 0) & (jj % 2 == 0))
        keep &= (g0 < 2.5) | ((ii % 4 == 0) & (jj % 4 == 0))
        cx, cy, soft = cx[keep], cy[keep], soft[keep]
        if not len(cx):
            continue
        gx = np.maximum(np.maximum(px0 - (cx + hw), (cx - hw) - px1), 0.0)
        gy = np.maximum(np.maximum(py0 - (cy + hh), (cy - hh) - py1), 0.0)
        gap = np.hypot(gx, gy)
        # centred on an edge reads best: a small price for sitting off the part's axes
        off = np.minimum(np.abs(cx - mx) / max(w, 0.5), np.abs(cy - my) / max(w, 0.5))
        cost = C_GAP * gap + C_GAP2 * gap * gap + (0.0 if hw >= hh else C_ROT90) + C_SLIDE * np.minimum(off, 1.5) + C_VIA * soft
        top = np.argsort(cost)[:MAX_SLOTS]
        cx, cy, gap, cost = cx[top], cy[top], gap[top], cost[top]
        cost = cost + np.where(_nearest_other(cx, cy, lab["box"], others) < gap + 0.15, C_OTHER, 0.0)
        out += [(float(c), float(x), float(y), ang, [(float(x) - hw, float(y) - hh, float(x) + hw, float(y) + hh)]) for c, x, y in zip(cost, cx, cy)]
    if slant:
        out += _slanted(lab, side, others)
    out.sort(key=lambda q: q[0])
    return out[:MAX_SLOTS]


def _nearest_other(cx, cy, own, others):
    """Distance from each slot centre to the nearest pad box of another part."""
    if not len(others):
        return np.full(cx.shape, 1e9)
    dx = np.maximum(np.maximum(others[:, 0][None, :] - cx[:, None], cx[:, None] - others[:, 2][None, :]), 0.0)
    dy = np.maximum(np.maximum(others[:, 1][None, :] - cy[:, None], cy[:, None] - others[:, 3][None, :]), 0.0)
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
                c = C_GAP * g + C_GAP2 * g * g + C_ROT45 + C_VIA * float(soft.sum())
                out.append((c, mx, my, ang, list(zip(bx0.tolist(), by0.tolist(), bx1.tolist(), by1.tolist()))))
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


def assign(slots, bounds, seed=1):
    """Choose a slot per label. Returns (chosen index per label, overlapping cells left).

    1. Hardest first: the label with the fewest slots near its part takes the closest free one, so a label in a tight cluster is not shut out by one that had room to go elsewhere.
    2. Repair: a clashing label takes any free slot; if none, the labels in the way of one of its slots step aside to free slots of their own (an ejection chain).
    3. Pull in: labels furthest out first, each takes a closer free slot, or a closer slot whose occupants can step aside for less than it gains. No step makes an overlap.
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
            for j in range(0, len(slots[i]), 3):
                if eject(i, j, 1e12):
                    done = True
                    fixed += 1
                    break
            if not done:
                occ.put(wins[i][k[i]], i)
        if not fixed:
            break

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
            for j in range(0, min(cur, 300), 4):
                if eject(i, j, slots[i][cur][0] - 0.5):
                    done = True
                    pulled += 1
                    break
            if not done:
                occ.put(wins[i][cur], i)
        if not pulled:
            break
    left = 0
    for i in range(n):
        occ.take(wins[i][k[i]], i)
        left += occ.hits(wins[i][k[i]])
        occ.put(wins[i][k[i]], i)
    return k, left


def solve_side(labels, side, bounds, slant=True, seed=1):
    """One face at one text size. Returns dict(ok, left, no_slot, chosen {ref: (cx, cy, angle, gap cost)}, slanted)."""
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
    chosen = {q["ref"]: slots[i][k[i]][:4] for i, q in enumerate(use)}
    return dict(ok=left == 0 and not no_slot, left=left, no_slot=no_slot, chosen=chosen, slanted=sum(1 for v in chosen.values() if abs(v[3]) == 45.0))
