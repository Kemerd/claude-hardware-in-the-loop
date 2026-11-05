#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 pf_route.py - negotiated-congestion (PathFinder) router for dense multi-layer KiCad boards
=======================================================================================================================
 Interpreter: any Python >= 3.10 with numpy, scipy and shapely >= 2.0, plus a C compiler the first time it runs. It never
              imports pcbnew: route_board.py reads the board, hands this file a problem JSON and writes the result back.

              Two ways to get the packages (docs/PCB.md has both):
                  "<KiCad python>" -m pip install --target <dir> scipy shapely      then set HIL_KICAD_SITE=<dir>
                  a venv of your own, or the CadQuery venv, with HIL_ROUTER_PYTHON pointing at its python

 Run:         python pf_route.py <problem.json> <config.json | -> <result.json> [--iters N] [--minutes M]
              Exit 0 = every job routed without a conflict, 1 = conflicts or open connections remain (the result still
              holds what did route, plus the list of what did not), 2 = bad input, 3 = a package or compiler is missing.
 Paths: command line.

 Why it exists
   A sequential router lays one net at a time, so a net routed early walls in the nets routed later, and on a BGA or a
   fine-pitch field it stalls in rip-up with connections left open. Here every net sees every other net's copper as a
   SOFT cost. Nets that collide are ripped up and routed again, the price of sharing a cell rises each round
   (present-sharing factor) and cells that keep being fought over stay dearer for good (history), until no two nets
   share copper. McMurchie and Ebeling, "PathFinder: a negotiation-based performance-driven router for FPGAs", FPGA 1995.
   The search itself is the C grid A* in astar.c, built once on first use.

 Rules (mm). All of them come from the board through the problem file; this script never relaxes one, and kicad-cli DRC
 checks the result afterwards.
   * Square grid. A track centre may sit on a cell centre when every other net's copper is at least clearance + width / 2
     + margin away on that layer: pads, vias, holes, tracks, rule areas, board edge. A via needs the same for its own pad
     and drill on every layer.
   * The clearance of a pair is the larger of the two nets' clearances, as in KiCad's own DRC.
   * Every connected copper cluster of a net is one terminal, so copper already on the board stays and only what is
     open gets routed.
   * A cluster of a plane net ends at a new via inside the region where its plane has copper, or at copper already on
     the plane. The plane layer is an end point, never a corridor.
   * No via inside a pad unless the config asks for it. All new vias are through vias.

 Problem file: written by route_board.py, schema "hil-route-problem/1". The config keys are listed in route_board.py.
 Result file:  {"tracks": [[x0, y0, x1, y1, width, layer, net]], "vias": [[x, y, diameter, drill, net]],
                "stats": {...}, "open": [{"net", "terminals", "why"}], "collisions": [{"a", "b", "cells", "at"}]}
=======================================================================================================================
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

PIP_HINT = ('install them with:  "<KiCad python>" -m pip install --target <dir> scipy shapely   and set HIL_KICAD_SITE=<dir>,\n'
            '                    or point HIL_ROUTER_PYTHON at a python that has numpy, scipy and shapely (see docs/PCB.md)')


def _import_packages():
    """Import numpy, scipy and shapely, looking in HIL_KICAD_SITE only when the interpreter does not have them itself."""
    def attempt():
        import numpy
        from scipy import ndimage
        import shapely
        return numpy, ndimage, shapely
    try:
        return attempt()
    except ImportError:
        site = os.environ.get("HIL_KICAD_SITE", "")
        if not site or not os.path.isdir(site):
            raise
        sys.path.insert(0, site)
        return attempt()


try:
    np, ndi, shapely = _import_packages()
    from shapely import STRtree, contains_xy
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import unary_union
except ImportError as _e:                                   # a clear message beats a bare traceback in a CI log
    print(f"pf_route: missing package ({_e}) in {sys.executable}\n{PIP_HINT}", file=sys.stderr)
    sys.exit(3)

SCHEMA = "hil-route-problem/1"
HARD = 30000                # raster id of an obstacle every net must avoid; net ids stay below it, the rasters are int16
MAX_EXPAND = 6_000_000      # cells one search may expand before it gives up

# ---------------------------------------------------------------------------------------------------------------------
#  Defaults of the config. These are the values dense 4 to 8 layer boards converged on; route_board.py documents each key.
# ---------------------------------------------------------------------------------------------------------------------
DEFAULTS = {
    "grid": 0.05,               # mm per cell
    "margin": 0.02,             # mm added to every clearance, the allowance for a track that runs between cell centres
    "window": None,             # mm a search may wander past the bounding box of its ends; None = a quarter of the board, at least 8
    "near": 2.0,                # mm round a net's terminals where a restricted layer may still be used, to reach a pad
    "via_cost": 22.0,           # cells of track one layer change is worth
    "soft_vias": True,          # a via on a cell another net occupies is priced, not forbidden
    "corner_cut": True,         # a diagonal step may pass between two blocked cells, the way out of a via field (astar.c says why)
    "sequential": True,         # one net off, the same net back on, against everybody else's current copper
    "keep_best": True,          # the round with the fewest conflicts is what goes out, not the last round
    "hist_cap": 110,            # ceiling of the history cost; below pf_cap so an occupied cell stays dearer than any free one
    "pf_cap": 60.0,             # ceiling of the present-sharing factor
    "pf_grow": 1.2,             # present-sharing factor per round
    "hist_step": 1,             # history added per round to a conflict cell
    "late_after": 6,            # round at which the late nets join at the latest
    "late_frozen": False,       # finished jobs keep their copper when the late ones join
    "late_frozen_iters": 0,     # rounds they stay frozen; after that they may give way
    "stall_iters": 40,          # rounds without progress before everything near the conflicts comes off at once
    "stall_mm": [1.5, 3.0, 6.0, 1000.0],  # circles of that kick, wider each time it happens
    "patience": 120,            # stop when the best count has not improved for this many rounds (0 = no such stop)
    "dir_cost": 1,              # surcharge that sends a mostly east-west and a mostly north-south net to different layers
    "max_ram_gb": 12.0,         # refuse a problem whose rasters would need more than this
}


# =====================================================================================================================
#  C A* core: compiled once per source hash into a cache folder
# =====================================================================================================================
def _compiler_commands(lib: str, src: str) -> list[list[str]]:
    """Every compile command worth trying on this machine, best first: $CC, clang, gcc, cc, then MSVC on Windows."""
    if sys.platform == "darwin":
        flags = ["-O2", "-dynamiclib"]
    elif os.name == "nt":
        flags = ["-O2", "-shared"]
    else:
        flags = ["-O2", "-shared", "-fPIC"]
    names = [os.environ.get("CC", "").strip(), "clang", "gcc", "cc"]
    if os.name == "nt":                                     # the winget LLVM package does not always land on PATH
        names.insert(2, os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "LLVM", "bin", "clang.exe"))
    cmds, seen = [], set()
    for n in names:
        exe = shutil.which(n) if n else None
        if exe and exe not in seen:
            seen.add(exe)
            cmds.append([exe] + flags + ["-o", lib, src])
    cl = shutil.which("cl") if os.name == "nt" else None
    if cl:
        cmds.append([cl, "/nologo", "/O2", "/LD", src, f"/Fe:{lib}", f"/Fo:{lib}.obj"])
    return cmds


def _cache_dir() -> str:
    """Where the compiled core lives: HIL_ROUTER_CACHE, else <repo>/.tmp/astar (generated, never committed)."""
    return os.path.abspath(os.environ.get("HIL_ROUTER_CACHE", "").strip() or os.path.join(REPO, ".tmp", "astar"))


def _load_library(path: str):
    """Open a compiled core, or return the loader's complaint (a 32-bit build under a 64-bit python, for one)."""
    try:
        return ctypes.CDLL(path), ""
    except OSError as e:
        return None, str(e)


def load_astar():
    """Compile astar.c on first use, load it, declare the ctypes signature. Exits with code 3 when no compiler can build it."""
    src = os.path.join(HERE, "astar.c")
    if not os.path.isfile(src):
        print(f"pf_route: {src} is missing; it ships beside this file", file=sys.stderr)
        sys.exit(3)
    out_dir = _cache_dir()
    os.makedirs(out_dir, exist_ok=True)
    with open(src, "rb") as fh:
        tag = hashlib.sha1(fh.read()).hexdigest()[:10]
    ext = ".dll" if os.name == "nt" else (".dylib" if sys.platform == "darwin" else ".so")
    lib_path = os.path.join(out_dir, f"astar_{tag}{ext}")
    lib, why = _load_library(lib_path) if os.path.isfile(lib_path) else (None, "")
    # ---- no usable library yet: every compiler in turn. A build can succeed and still not load, so the load is the test
    tried = []
    if lib is None:
        for cmd in _compiler_commands(lib_path, src):
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=out_dir)
            if r.returncode == 0 and os.path.isfile(lib_path):
                lib, why = _load_library(lib_path)
                if lib is not None:
                    break
                tried.append(f"{cmd[0]}: built, but this python cannot load it ({why})")
                os.remove(lib_path)
            else:
                tried.append(f"{cmd[0]}: {(r.stderr or r.stdout).strip()[-200:]}")
    if lib is None:
        print("pf_route: cannot build the search core astar.c.\n"
              + "".join(f"  {t}\n" for t in tried or ["no compiler found (tried $CC, clang, gcc, cc, and cl on Windows)"])
              + f"  A C compiler of the same bitness as this python ({8 * ctypes.sizeof(ctypes.c_void_p)}-bit) is needed:\n"
                "    Windows  winget install LLVM.LLVM\n"
                "    Linux    apt install gcc\n"
                "    macOS    xcode-select --install", file=sys.stderr)
        sys.exit(3)
    f = lib.grid_astar
    u8, i64 = ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_int64)
    f.argtypes = [u8, u8, u8, u8, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, i64, ctypes.c_int64, ctypes.c_double, ctypes.c_double,
                  ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int64, i64, ctypes.c_int64, i64]
    f.restype = ctypes.c_int64
    return f


# =====================================================================================================================
#  Net patterns in the config
# =====================================================================================================================
def net_matches(pattern: str, net: str) -> bool:
    """A config pattern against a net name: the exact name, the name without KiCad's leading '/', or a full regex."""
    if pattern == net or pattern == net.lstrip("/"):
        return True
    try:
        return bool(re.fullmatch(pattern, net) or re.fullmatch(pattern, net.lstrip("/")))
    except re.error:                                        # '+3V3' is a fine net name and a broken regex
        return False


def pattern_lookup(table: dict | None, net: str):
    """Value of the last pattern in `table` that matches `net` (later lines refine earlier ones), or None."""
    hit = None
    for pat, val in (table or {}).items():
        if net_matches(pat, net):
            hit = val
    return hit


def rings_to_geom(rings: list):
    """Shapely geometry of [{"outer": ring, "holes": [ring, ...]}, ...]; a ring is [[x, y], ...]. None when nothing is left."""
    parts = []
    for r in rings or ():
        if len(r.get("outer", ())) < 3:
            continue
        p = Polygon(r["outer"], [h for h in r.get("holes", ()) if len(h) >= 3])
        if not p.is_valid:
            p = shapely.make_valid(p)
        if not p.is_empty:
            parts.append(p)
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else unary_union(parts)


# =====================================================================================================================
#  One routing problem: static obstacles, per-net terminals, jobs
# =====================================================================================================================
class Router:
    """One routing problem: the rule rasters, the net terminals and the search jobs. The negotiation is class Negotiator."""

    def __init__(self, problem: dict, cfg: dict | None = None):
        if not isinstance(problem, dict) or problem.get("schema") != SCHEMA:
            raise SystemExit(f"pf_route: not a {SCHEMA} problem file (schema {problem.get('schema') if isinstance(problem, dict) else None!r})")
        self.d = problem
        self.cfg = {**DEFAULTS, **(cfg or {})}
        self.G = float(self.cfg["grid"])
        self.margin = float(self.cfg["margin"])
        if not 0.005 <= self.G <= 1.0:
            raise SystemExit(f"pf_route: grid {self.G} mm is outside 0.005 .. 1.0")
        self.warnings: list[str] = []
        self.astar = load_astar()
        self.corner_cut = 1 if self.cfg["corner_cut"] else 0
        # ---- the layers the search sees: the routing layers plus one plane layer as the pseudo layer plane jobs end on.
        #      Every new via is a through via, so reaching that one layer means reaching whichever plane the job wants
        self.plane_layer = problem["plane_layers"][0] if problem["plane_layers"] else ""
        wanted = set(problem["routing"]) | ({self.plane_layer} if self.plane_layer else set())
        self.layers = [ln for ln in problem["layers"] if ln in wanted]
        if not self.layers:
            raise SystemExit("pf_route: no routing layers in the problem")
        self.nL = len(self.layers)
        self.li = {n: i for i, n in enumerate(self.layers)}
        self.pl = self.li.get(self.plane_layer, -1)
        # ---- grid: cell (i, j) has its centre at (X0 + j * G, Y0 + i * G). X0 and Y0 are multiples of G, so pad rows laid out
        #      on a multiple of the grid sit on cells
        x0, y0, x1, y1 = problem["outline"]["box"]
        self.X0 = math.floor((x0 - 0.5) / self.G) * self.G
        self.Y0 = math.floor((y0 - 0.5) / self.G) * self.G
        self.W = int(math.ceil((x1 + 0.5 - self.X0) / self.G)) + 1
        self.H = int(math.ceil((y1 + 0.5 - self.Y0) / self.G)) + 1
        self.board_mm = max(x1 - x0, y1 - y0)
        rules = problem["rules"]
        self.edge, self.hole_clr, self.hole_to_hole = float(rules["edge"]), float(rules["hole_clr"]), float(rules["hole_to_hole"])
        self.nets: dict[str, int] = {}
        self._build_nets()
        self._resolve_nets()
        self._static()
        self._terminals()

    # -------------------------------------------------------------------------------------------------------- helpers
    def nid(self, name: str) -> int:
        """Integer id of a net name; '' and unconnected-* pads belong to no net (an obstacle for everybody)."""
        if not name or name.startswith("unconnected-"):
            return HARD
        return self.nets.setdefault(name, len(self.nets))

    def xy2ij(self, x: float, y: float) -> tuple[int, int]:
        """Cell nearest a board position."""
        return int(round((y - self.Y0) / self.G)), int(round((x - self.X0) / self.G))

    def ij2xy(self, i: int, j: int) -> tuple[float, float]:
        """Board position of a cell centre."""
        return self.X0 + j * self.G, self.Y0 + i * self.G

    def _build_nets(self) -> None:
        """Number every net that has copper on the board."""
        for p in self.d["pads"]:
            self.nid(p["net"])
        for v in self.d["vias"]:
            self.nid(v[4])
        for t in self.d["tracks"]:
            self.nid(t[6])
        if len(self.nets) >= HARD:
            raise SystemExit(f"pf_route: {len(self.nets)} nets do not fit the int16 rasters")

    def _resolve_nets(self) -> None:
        """Width, clearance, via, layers and costs of every net: the board's netclass first, then what the config adds."""
        cfg, nc = self.cfg, self.d["netclass"]
        rules = self.d["rules"]
        dflt = nc.get("", {})
        self.net_width, self.net_clr, self.net_via, self.net_layers, self.net_cost = {}, {}, {}, {}, {}
        for net in list(self.nets) + [""]:
            c = nc.get(net) or dflt
            self.net_width[net] = float(pattern_lookup(cfg.get("width"), net) or c["width"])
            self.net_clr[net] = max(float(c["clearance"]), float(pattern_lookup(cfg.get("clearance"), net) or 0.0))
            via = pattern_lookup(cfg.get("via_net"), net) or cfg.get("via") or c["via"]
            self.net_via[net] = (float(via[0]), float(via[1]))
            self.net_layers[net] = pattern_lookup(cfg.get("layers"), net)
            self.net_cost[net] = pattern_lookup(cfg.get("layer_cost"), net) or {}
        # ---- a typing error in the config must not pass in silence: every pattern has to hit a net, every layer has to exist
        for key in ("width", "clearance", "via_net", "layers", "layer_cost", "widen", "planes", "late_nets", "order", "skip"):
            table = cfg.get(key) or ()
            for pat in table:
                if not any(net_matches(pat, n) for n in self.nets):
                    self.warnings.append(f"config {key}: pattern {pat!r} matches no net")
        for net, ls in self.net_layers.items():
            for ln in ls or ():
                if ln not in self.li:
                    raise SystemExit(f"pf_route: config layers: {ln!r} for net {net} is not a routing layer here ({self.layers})")
        for net, costs in self.net_cost.items():
            for ln in costs:
                if ln not in self.li:
                    raise SystemExit(f"pf_route: config layer_cost: {ln!r} for net {net} is not a routing layer here ({self.layers})")
        # ---- what the board's own minimums allow. Routing below them would only trade an open for a DRC error
        for net in self.nets:
            w, (vd, vdr) = self.net_width[net], self.net_via[net]
            if w < float(rules["track_min"]) - 1e-6:
                raise SystemExit(f"pf_route: net {net} routes at {w} mm, below the board's minimum track width {rules['track_min']}")
            if vd < float(rules["via_min"]) - 1e-6 or vdr < float(rules["drill_min"]) - 1e-6 or (vd - vdr) / 2 < float(rules["annular_min"]) - 1e-6:
                raise SystemExit(f"pf_route: net {net} via {vd} / {vdr} breaks the board's minimums "
                                 f"(via {rules['via_min']}, drill {rules['drill_min']}, annular ring {rules['annular_min']})")
        # ---- the base case the rasters are built for: the narrowest track, the smallest clearance, the smallest via.
        #      Everything wider answers for its own extra, so a board with one class pays nothing for the generality
        self.base_w = min(self.net_width[n] for n in self.net_width)
        self.clr = min(self.net_clr[n] for n in self.net_clr)
        self.via_d, self.via_dr = min((self.net_via[n] for n in self.net_via), key=lambda v: (self._reach(v), v))

    def _reach(self, via: tuple[float, float]) -> float:
        """How far a via wants other copper from its centre: its pad or its drill, whichever wants more room."""
        return max(via[0] / 2 + self.clr, via[1] / 2 + self.hole_clr)

    def extra_of(self, net: str) -> float:
        """How much further than the base clearance this net stands off."""
        return round(self.net_clr.get(net, self.net_clr[""]) - self.clr, 4)

    def width_of(self, net: str) -> float:
        """Track width of a net."""
        return self.net_width.get(net, self.net_width[""])

    def via_of(self, net: str) -> tuple[float, float]:
        """(diameter, drill) of a new via of this net."""
        return self.net_via.get(net, self.net_via[""])

    def via_reach(self, net: str) -> float:
        """How far a new via of this net wants other copper from its centre."""
        return self._reach(self.via_of(net))

    def via_guard(self, net: str) -> float:
        """Centre distance a new via keeps from another drill of its own net: the board's hole-to-hole rule, or the config's."""
        g = self.cfg.get("via_guard")
        return float(g) if g else self.via_of(net)[1] + self.hole_to_hole + 0.01

    def _cells_in(self, geom, margin_cells: int = 1):
        """Cell index window (i0, i1, j0, j1) and a boolean mask of the cells whose centre lies inside a shapely geometry."""
        if geom is None or geom.is_empty:
            return None
        x0, y0, x1, y1 = geom.bounds
        i0 = max(0, int(math.floor((y0 - self.Y0) / self.G)) - margin_cells)
        i1 = min(self.H - 1, int(math.ceil((y1 - self.Y0) / self.G)) + margin_cells)
        j0 = max(0, int(math.floor((x0 - self.X0) / self.G)) - margin_cells)
        j1 = min(self.W - 1, int(math.ceil((x1 - self.X0) / self.G)) + margin_cells)
        if i1 < i0 or j1 < j0:
            return None
        if (i1 - i0) * (j1 - j0) > 40000:                          # a long ring or a big pour: worth indexing first
            shapely.prepare(geom)
        ii, jj = np.mgrid[i0:i1 + 1, j0:j1 + 1]
        m = contains_xy(geom, self.X0 + jj * self.G, self.Y0 + ii * self.G)
        return i0, i1, j0, j1, m

    # ------------------------------------------------------------------------------------------------ static obstacles
    def _own_mark(self, own1, own2, layer_idx, geom, nid) -> None:
        """Record `nid` as holding every cell inside `geom` (two holders per cell are enough to decide 'blocked for net n')."""
        r = self._cells_in(geom)
        if r is None:
            return
        i0, i1, j0, j1, m = r
        a = own1[layer_idx, i0:i1 + 1, j0:j1 + 1]
        b = own2[layer_idx, i0:i1 + 1, j0:j1 + 1]
        if nid == HARD:
            a[m] = HARD
            return
        free1 = m & (a == -1)
        a[free1] = nid
        sec = m & ~free1 & (a != nid) & (a != HARD) & (b == -1)
        b[sec] = nid

    def _static(self) -> None:
        """Build the obstacle rasters: one per (width, extra clearance) class per layer for tracks, one per extra clearance for vias."""
        d = self.d
        jobs_nets = list(self.nets)
        self.classes = sorted({(self.width_of(n), self.extra_of(n)) for n in jobs_nets} | {(self.base_w, 0.0)})
        self.extras = sorted({e for _w, e in self.classes})
        cells = self.H * self.W
        need_gb = (4 * len(self.classes) * self.nL + 4 * len(self.extras) + 2) * cells / 2 ** 30
        if need_gb > float(self.cfg["max_ram_gb"]):
            raise SystemExit(f"pf_route: a {self.W} x {self.H} grid with {self.nL} layers and {len(self.classes)} width classes needs about "
                             f"{need_gb:.1f} GB. Raise `grid` in the config (now {self.G} mm), or `max_ram_gb` if the machine has the room")
        shape = (len(self.classes), self.nL, self.H, self.W)
        self.own1t = np.full(shape, -1, np.int16)
        self.own2t = np.full(shape, -1, np.int16)
        self.own1v = np.full((len(self.extras), self.H, self.W), -1, np.int16)
        self.own2v = np.full((len(self.extras), self.H, self.W), -1, np.int16)
        self.nopad_via = np.zeros((self.H, self.W), bool)         # no via here for anybody: inside a pad, or too close to another drill
        vr, m = self.via_d / 2, self.margin

        def add_item(layer_names, geom, nid, item_extra=0.0, hole=False):
            """Register an obstacle on layers: buffered per width class for tracks, once per clearance class for vias."""
            for ci, (w, e) in enumerate(self.classes):
                c = self.hole_clr if hole else self.clr + max(item_extra, e)
                g = geom.buffer(c + w / 2 + m)
                for ln in layer_names:
                    if ln in self.li:                              # a pad also has copper on planes this router does not list
                        self._own_mark(self.own1t[ci], self.own2t[ci], self.li[ln], g, nid)
            # a new via's drill keeps hole_clr from this copper too. On a small via that is often the larger distance
            for ei, e in enumerate(self.extras):
                c = self.hole_clr + vr if hole else max(self.clr + max(item_extra, e) + vr, self.hole_clr + self.via_dr / 2)
                self._own_mark(self.own1v, self.own2v, ei, geom.buffer(c + m), nid)

        def forbid_via_near(x, y, radius):
            """No new via within `radius` of a drill, its own net included: the hole-to-hole rule."""
            rc = int(math.ceil(radius / self.G))
            yy, xx = np.mgrid[-rc:rc + 1, -rc:rc + 1]
            se = (xx * xx + yy * yy) * self.G * self.G < radius * radius
            i, j = self.xy2ij(x, y)
            a, b, c, e = max(0, i - rc), min(self.H, i + rc + 1), max(0, j - rc), min(self.W, j + rc + 1)
            if a < b and c < e:
                self.nopad_via[a:b, c:e] |= se[a - i + rc:b - i + rc, c - j + rc:e - j + rc]

        via_pad_ok = list(self.cfg.get("via_in_pad") or ())
        for p in d["pads"]:
            nid = self.nid(p["net"])
            extra = max(self.extra_of(p["net"]), float(p.get("clearance") or 0.0) - self.clr)
            tag = f"{p['ref']}.{p['num']}"
            for ln, rings in p["polys"].items():
                poly = rings_to_geom(rings)
                if poly is None:
                    continue
                add_item([ln], poly, nid, extra)
                if ln in self.li and not any(net_matches(q, tag) for q in via_pad_ok):
                    r = self._cells_in(poly.buffer(0.02))
                    if r:
                        i0, i1, j0, j1, msk = r
                        self.nopad_via[i0:i1 + 1, j0:j1 + 1] |= msk
            if p["drill"] > 0:                                     # a drill keeps hole_clr from other copper, plated or not
                hole = Point(p["x"], p["y"]).buffer(p["drill"] / 2)
                add_item(self.layers, hole, nid if p["plated"] else HARD, hole=True)
                forbid_via_near(p["x"], p["y"], (p["drill"] + self.via_dr) / 2 + self.hole_to_hole + 0.01)
        for v in d["vias"]:
            nid = self.nid(v[4])
            add_item(self._via_span(v), Point(v[0], v[1]).buffer(v[2] / 2), nid, self.extra_of(v[4]))
            add_item(self._via_span(v), Point(v[0], v[1]).buffer(v[3] / 2), nid, hole=True)
            forbid_via_near(v[0], v[1], (v[3] + self.via_dr) / 2 + self.hole_to_hole + 0.01)
        for t in d["tracks"]:
            add_item([t[5]], LineString([(t[0], t[1]), (t[2], t[3])]).buffer(t[4] / 2), self.nid(t[6]), self.extra_of(t[6]))
        # ---- copper with no net: drawn shapes and text on a copper layer. Everybody stays out
        for c in d.get("copper", ()):
            add_item([c["layer"]], rings_to_geom(c["rings"]), HARD, self.extra_of(""))
        # ---- a pour the config names as solid: other nets stay out of it on its layers instead of letting it flow round them
        for z in d["zones"]:
            if any(net_matches(q, z["net"]) for q in self.cfg.get("solid_zones") or ()):
                add_item(z["layers"], rings_to_geom(z["rings"]), self.nid(z["net"]), self.extra_of(z["net"]))
        # ---- keep-out rule areas, on the board and inside footprints
        for a in d["areas"]:
            poly = rings_to_geom(a["rings"])
            if poly is None:
                continue
            if a["tracks"]:
                for ci, (w, _e) in enumerate(self.classes):
                    g = poly.buffer(w / 2 + m)
                    for ln in a["layers"]:
                        if ln in self.li:                          # a rule area also covers plane layers this router does not list
                            self._own_mark(self.own1t[ci], self.own2t[ci], self.li[ln], g, HARD)
            if a["vias"]:
                for ei in range(len(self.extras)):
                    self._own_mark(self.own1v, self.own2v, ei, poly.buffer(vr + m), HARD)
        self._edge_rasters()
        self._config_regions()

    def _via_span(self, v: list) -> list:
        """Layers an existing via has copper on. A blind via stops at its last layer, a through via (top = F, bottom = B) spans all."""
        layers = self.d["layers"]
        if len(v) < 7 or v[5] not in layers or v[6] not in layers:
            return list(self.layers)
        a, b = sorted((layers.index(v[5]), layers.index(v[6])))
        return layers[a:b + 1]

    def _edge_rasters(self) -> None:
        """Board edge: copper keeps the edge clearance from the outline and from every cut-out, measured exactly to the polygon."""
        o = self.d["outline"]
        board = rings_to_geom([{"outer": o["poly"], "holes": o.get("holes") or []}])
        if board is None:
            raise SystemExit("pf_route: the board has no outline polygon")
        self.board = board
        shapely.prepare(board)
        yy, xx = np.mgrid[0:self.H, 0:self.W]
        outside = ~contains_xy(board, self.X0 + xx * self.G, self.Y0 + yy * self.G)
        ring = board.boundary
        for ci, (w, _e) in enumerate(self.classes):
            self.own1t[ci][:, outside] = HARD
            band = self._cells_in(ring.buffer(self.edge + w / 2 + self.margin))
            if band:
                i0, i1, j0, j1, msk = band
                self.own1t[ci][:, i0:i1 + 1, j0:j1 + 1][:, msk] = HARD
        band = self._cells_in(ring.buffer(self.edge + self.via_d / 2 + self.margin))
        for ei in range(len(self.extras)):
            self.own1v[ei][outside] = HARD
            if band:
                i0, i1, j0, j1, msk = band
                self.own1v[ei][i0:i1 + 1, j0:j1 + 1][msk] = HARD

    def _config_regions(self) -> None:
        """Regions that come from the config, not the board: layer boxes and per-net keep-outs."""
        cfg = self.cfg
        # ---- a layer that may carry tracks only inside a box (a plane everywhere else, escape routing inside)
        for ln, (x0, y0, x1, y1) in (cfg.get("layer_box") or {}).items():
            if ln not in self.li:
                raise SystemExit(f"pf_route: config layer_box: {ln!r} is not a routing layer here ({self.layers})")
            yy, xx = np.mgrid[0:self.H, 0:self.W]
            X, Y = self.X0 + xx * self.G, self.Y0 + yy * self.G
            outside = (X < x0) | (X > x1) | (Y < y0) | (Y > y1)
            for ci in range(len(self.classes)):
                self.own1t[ci][self.li[ln]][outside] = HARD
        # ---- keep-outs: cells a net may not use on a layer unless it is exempt. `around` builds the rectangle from a part
        #      (a crystal: nothing under it but its own nets and ground), `rect` states it outright
        self.keep = []
        for k in cfg.get("keepouts") or ():
            exempt = list(k.get("exempt") or ())
            rect = k.get("rect")
            if k.get("around"):
                pads = [q for q in self.d["pads"] if q["ref"] in k["around"]]
                if not pads:
                    raise SystemExit(f"pf_route: config keepouts: no pads of {k['around']} on the board")
                pad = float(k.get("margin", 0.5))
                xs, ys = [q["x"] for q in pads], [q["y"] for q in pads]
                rect = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad]
                exempt += [q["net"] for q in pads if q["net"]]     # the part's own nets have to reach it
            if not rect or len(rect) != 4:
                raise SystemExit(f"pf_route: config keepouts: {k!r} needs `rect` or `around`")
            ls = k.get("layers") or list(self.layers)
            for ln in ls:
                if ln not in self.li:
                    raise SystemExit(f"pf_route: config keepouts: {ln!r} is not a routing layer here ({self.layers})")
            i0, j0 = self.xy2ij(rect[0], rect[1])
            i1, j1 = self.xy2ij(rect[2], rect[3])
            self.keep.append((max(0, i0), i1, max(0, j0), j1, [self.li[n] for n in ls], exempt, bool(k.get("vias", True))))

    def extra_block(self, job: dict, t_block, v_block):
        """Per-job additions to the blocked rasters: the config keep-outs this job's net is not exempt from."""
        hit = [k for k in self.keep if not any(net_matches(q, job["net"]) for q in k[5])]
        if not hit:
            return t_block, v_block
        t, v = t_block.copy(), v_block
        for (i0, i1, j0, j1, ls, _e, vias) in hit:
            for l in ls:
                t[l, i0:i1 + 1, j0:j1 + 1] = True
            if vias:
                v = v.copy() if v is v_block else v
                v[i0:i1 + 1, j0:j1 + 1] = True
        return t, v

    # ------------------------------------------------------------------------------------------------------- terminals
    def _cells_of(self, c: dict, anchors: list) -> list:
        """Routing-layer cells of a cluster: pad copper and track centre lines. Vias are handed to the caller as they are."""
        cells = []
        for r, num, x, y in c["pads"]:
            p = self.padix.get((r, num, x, y))
            if p is None:
                raise SystemExit(f"pf_route: cluster pad {r}.{num} is not in the pad list")
            got = []
            for ln, rings in p["polys"].items():
                if ln not in self.li or ln == self.plane_layer:
                    continue
                geom = rings_to_geom(rings)
                if geom is None:
                    continue
                rr = self._cells_in(geom.buffer(-0.005), 0)
                if rr is None:
                    continue
                i0, i1, j0, j1, m = rr
                ii, jj = np.nonzero(m)
                got += [(self.li[ln], i0 + int(a), j0 + int(b)) for a, b in zip(ii, jj)]
            if not got:                                     # a land smaller than the grid: its centre cell, and a short link to the true centre
                i, j = self.xy2ij(x, y)
                got = [(self.li[ln], i, j) for ln in p["polys"] if ln in self.li and ln != self.plane_layer]
                anchors.append((x, y))
            cells += got
        for t in c["tracks"]:
            x0, y0, x1, y1, ln = t[0], t[1], t[2], t[3], t[5]
            if ln not in self.li or ln == self.plane_layer:
                continue
            n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (self.G / 2)))
            for k in range(n + 1):
                i, j = self.xy2ij(x0 + (x1 - x0) * k / n, y0 + (y1 - y0) * k / n)
                cells.append((self.li[ln], i, j))
        return list(dict.fromkeys(cells))

    def via_cells(self, v: list) -> list:
        """Routing-layer cells a via in a cluster gives access to."""
        i, j = self.xy2ij(v[0], v[1])
        return [(self.li[ln], i, j) for ln in self._via_span(v) if ln in self.li and ln != self.plane_layer]

    def _terminals(self) -> None:
        """Per net: one terminal per connected copper cluster."""
        self.padix = {(p["ref"], p["num"], p["x"], p["y"]): p for p in self.d["pads"]}
        self.term: dict[str, list[dict]] = {}
        skip = list(self.cfg.get("skip") or ())
        for net, cl in self.d["clusters"].items():
            if net.startswith("unconnected-") or any(net_matches(q, net) for q in skip):
                continue
            ts = []
            for c in cl:
                xs = [p[2] for p in c["pads"]] + [v[0] for v in c["vias"]]
                ys = [p[3] for p in c["pads"]] + [v[1] for v in c["vias"]]
                if not xs:
                    xs = [t[0] for t in c["tracks"]]
                    ys = [t[1] for t in c["tracks"]]
                tag = c["pads"][0][0] + "." + c["pads"][0][1] if c["pads"] else f"copper at {xs[0]:.2f}, {ys[0]:.2f}"
                anchors: list = []
                cells = self._cells_of(c, anchors)
                # plated drills of the cluster: the places where it is tied through the plane layers
                holes = [(p[2], p[3]) for p in c["pads"] if self.padix[(p[0], p[1], p[2], p[3])]["drill"] > 0]
                ts.append({"ref": tag, "x": sum(xs) / len(xs), "y": sum(ys) / len(ys), "cells": cells, "anchors": anchors, "holes": holes,
                           "vias": [[v[0], v[1], v[2], v[3], net, v[4], v[5]] for v in c["vias"]],
                           "zone": bool(c["zone"])})
            self.term[net] = ts

    def plane_mask(self, net: str):
        """Cells where a via of this net lands on its plane: the plane's filled copper, shrunk a cell, plus what the config adds."""
        spec = pattern_lookup(self.cfg.get("planes"), net) or {}
        yy, xx = np.mgrid[0:self.H, 0:self.W]
        X, Y = self.X0 + xx * self.G, self.Y0 + yy * self.G
        mask = np.zeros((self.H, self.W), bool)
        if "inside" in spec:                                       # the config states the region outright
            x0, y0, x1, y1 = spec["inside"]
            mask = (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        else:
            for rings in self.d["plane_nets"].get(net, ()):
                geom = rings_to_geom(rings)
                if geom is None:
                    continue
                geom = geom.buffer(-self.G)
                shapely.prepare(geom)
                mask |= contains_xy(geom, X, Y)
        for x0, y0, x1, y1 in spec.get("also", ()):
            mask |= (X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1)
        for x0, y0, x1, y1 in spec.get("exclude", ()):
            mask &= ~((X >= x0) & (X <= x1) & (Y >= y0) & (Y <= y1))
        return mask

    def is_plane_net(self, net: str) -> bool:
        """True for a net that ends at vias into a plane rather than at its other pads."""
        return net in self.d["plane_nets"] or pattern_lookup(self.cfg.get("planes"), net) is not None

    def jobs(self) -> list[dict]:
        """One job per net that needs copper; a plane net gets one job per cluster that is not on the plane yet."""
        jobs = []
        for net, ts in self.term.items():
            w = self.width_of(net)
            allowed, lcost = self.net_layers[net], self.net_cost[net]
            plane = self.pl >= 0 and self.is_plane_net(net)
            if len(ts) < 2 and not plane:
                continue
            if plane:
                mask = self.plane_mask(net)
                if not mask.any():
                    self.warnings.append(f"plane net {net}: no cell of its plane can take a via")
                # a cluster counts as on the plane only with a drill inside the plane's copper. A via on a torn-off fragment among a
                # chip's antipads is on a zone, but not on the plane
                on = [t for t in ts if t["zone"] and any(mask[self.xy2ij(*q)] for q in [(v[0], v[1]) for v in t["vias"]] + t["holes"])]
                main = max(on, key=lambda t: len(t["vias"]) + len(t["cells"])) if on else None
                tied = list(main["cells"]) if main else []
                for v in (main["vias"] if main else ()):
                    tied += self.via_cells(v)
                for t in ts:
                    if t is not main:
                        jobs.append({"net": net, "terms": [t], "plane": True, "w": w, "allowed": allowed, "lcost": lcost,
                                     "plane_mask": mask, "plane_cells": tied, "plane_set": set(tied)})
            else:
                jobs.append({"net": net, "terms": ts, "plane": False, "w": w, "allowed": allowed, "lcost": lcost})
        # ---- layer direction: a mostly east-west net prefers one layer, a mostly north-south net another, so nets that must cross
        #      sit on different layers from the start instead of negotiating it cell by cell
        pair = self.cfg.get("dir_layers") or self._default_dir_layers()
        if pair:
            lh, lv = pair
            for j in jobs:
                if not j["plane"] and not j["lcost"]:
                    xs, ys = [t["x"] for t in j["terms"]], [t["y"] for t in j["terms"]]
                    horiz = (max(xs) - min(xs)) >= (max(ys) - min(ys))
                    j["lcost"] = {(lv if horiz else lh): int(self.cfg["dir_cost"])}
        for k, j in enumerate(jobs):
            j["id"] = k
            j["nid"] = self.nid(j["net"])
            j["ci"] = self.classes.index((j["w"], self.extra_of(j["net"])))
            j["ei"] = self.extras.index(self.extra_of(j["net"]))
        return jobs

    def _default_dir_layers(self):
        """The two layers that take the direction preference: the two inner routing layers, else an inner one and B, else F and B."""
        r = [ln for ln in self.layers if ln != self.plane_layer]
        inner = r[1:-1]
        if len(inner) >= 2:
            return inner[0], inner[1]
        if len(inner) == 1:
            return inner[0], r[-1]
        return (r[0], r[-1]) if len(r) >= 2 else None


# =====================================================================================================================
#  Negotiation driver
# =====================================================================================================================
def disc(r_cells: float):
    """Boolean disc structuring element of radius r_cells (cells)."""
    n = int(math.ceil(r_cells))
    yy, xx = np.mgrid[-n:n + 1, -n:n + 1]
    return (xx * xx + yy * yy) <= r_cells * r_cells + 1e-9


class Negotiator:
    """Rip-up and re-route loop over all jobs with present-sharing and history costs."""

    def __init__(self, R: Router, jobs: list[dict], log=print):
        self.R, self.jobs, self.log = R, jobs, log
        shape = (R.nL, R.H, R.W)
        self.occT = np.zeros(shape, np.int8)          # nets whose copper blocks a TRACK centre here, per layer
        self.occV = np.zeros((R.H, R.W), np.int8)     # nets whose copper blocks a VIA centre here, any layer
        self.hist = np.zeros(shape, np.uint8)
        self.route: dict[int, dict] = {}              # job id -> {"paths": [...], "fp": {...}, "ok": bool}
        self.pf = 3.0
        self.waiting: set[int] = set()                # late jobs that have not joined the negotiation yet
        self.frozen: set[int] = set()                 # jobs that stopped negotiating (cfg late_frozen): they keep their copper
        self.bad_cells: dict[int, list] = {}
        self.disc_cache: dict = {}
        # jobs of the same net (every plane pad is its own job): their copper may overlap without being a conflict
        self.same: dict[int, list[int]] = {}
        for j in jobs:
            self.same.setdefault(j["nid"], []).append(j["id"])

    # ---- footprints ------------------------------------------------------------------------------------------------
    def _disc(self, r_mm: float):
        """Cached disc of a radius in mm."""
        k = round(r_mm, 4)
        if k not in self.disc_cache:
            self.disc_cache[k] = disc(r_mm / self.R.G)
        return self.disc_cache[k]

    def _grow(self, mask, *radii_mm) -> list:
        """The cells within each radius of a mask. One distance transform answers every radius at once, and unlike a dilation with a
        disc its cost does not rise with the radius, which is what a long net in a big box needs."""
        d = ndi.distance_transform_edt(~mask)
        return [d <= r / self.R.G + 1e-9 for r in radii_mm]

    def _footprint(self, job: dict, paths: list) -> dict:
        """Boolean copper footprints of a routed job: per layer 'tracks others must keep out of' and the via-blocking raster."""
        R = self.R
        w = job["w"]
        m = R.margin
        vr = R.via_of(job["net"])[0] / 2
        hx = R.extra_of(job["net"])
        vreach = R.via_reach(job["net"])
        bw = R.base_w / 2                                               # half the narrowest neighbour: every paint below is drawn for it
        r_tt = w / 2 + R.clr + bw + m + hx
        r_tv = w / 2 + vreach + m + hx
        r_vt = vreach + bw + m + hx
        r_vv = max(vr + vreach, R.via_of(job["net"])[1] + R.hole_to_hole) + m + hx
        cells = [c for p in paths for c in p]
        if not cells:
            return {"T": {}, "V": None, "box": None, "cl": set(), "vc": set()}
        ii = [c[1] for c in cells]
        jj = [c[2] for c in cells]
        pad = int(math.ceil(r_vv / R.G)) + 2
        i0, i1, j0, j1 = max(0, min(ii) - pad), min(R.H - 1, max(ii) + pad), max(0, min(jj) - pad), min(R.W - 1, max(jj) + pad)
        h, wd = i1 - i0 + 1, j1 - j0 + 1
        trk = {l: np.zeros((h, wd), bool) for l in range(R.nL)}         # centreline cells per layer (tracks)
        via = np.zeros((h, wd), bool)
        for p in paths:
            for a, b in zip(p, p[1:]):
                if a[0] == b[0]:                                        # same layer: fill the straight step
                    trk[a[0]][a[1] - i0, a[2] - j0] = True
                    trk[b[0]][b[1] - i0, b[2] - j0] = True
                else:                                                   # layer change on the same cell = a via
                    via[a[1] - i0, a[2] - j0] = True
            if len(p) == 1:
                trk[p[0][0]][p[0][1] - i0, p[0][2] - j0] = True
        T, tv = {}, np.zeros((h, wd), bool)
        for l, msk in trk.items():
            if msk.any():
                T[l], grown = self._grow(msk, r_tt, r_tv)
                tv |= grown
        V_all = np.zeros((h, wd), bool)
        if via.any():
            V_all, vt = self._grow(via, r_vv, r_vt)
            for l in range(R.nL):
                T[l] = T[l] | vt if l in T else vt
        vc = {(a[1], a[2]) for p in paths for a, b in zip(p, p[1:]) if a[0] != b[0]}     # new via sites, for conflicts()
        cl = {(c[0], c[1], c[2]) for c in cells}
        # every footprint above is painted for the narrowest neighbour. A wider job answers for its own extra width: conflicts() tests its
        # whole copper, not just the centreline, or two wide tracks pass each other's test and come out under clearance
        extra = (w - R.base_w) / 2 - m                                  # the paint holds the margin already: only the real shortfall counts
        if extra >= R.G / 2:
            for l, msk in trk.items():
                if msk.any():
                    a, b = np.nonzero(self._grow(msk, extra)[0])
                    cl.update((l, int(p) + i0, int(q) + j0) for p, q in zip(a, b))
        return {"T": T, "V": tv | V_all, "box": (i0, i1, j0, j1), "cl": cl, "vc": vc}

    def _paint(self, fp: dict, sign: int) -> None:
        """Add a footprint to the shared occupancy, or take it off."""
        if fp["box"] is None:
            return
        i0, i1, j0, j1 = fp["box"]
        for l, msk in fp["T"].items():
            self.occT[l, i0:i1 + 1, j0:j1 + 1] += (sign * msk).astype(np.int8)
        self.occV[i0:i1 + 1, j0:j1 + 1] += (sign * fp["V"]).astype(np.int8)

    def _same_net_occ(self, job: dict, l: int, i0: int, i1: int, j0: int, j1: int):
        """Occupancy (int16 window) that the OTHER jobs of the job's own net contribute on layer l: it must not count against the job."""
        out = np.zeros((i1 - i0 + 1, j1 - j0 + 1), np.int16)
        for k in self.same.get(job["nid"], ()):
            if k == job["id"]:
                continue
            r = self.route.get(k)
            if not r or r["fp"]["box"] is None or l not in r["fp"]["T"]:
                continue
            a0, a1, b0, b1 = r["fp"]["box"]
            ii0, ii1, jj0, jj1 = max(i0, a0), min(i1, a1), max(j0, b0), min(j1, b1)
            if ii1 < ii0 or jj1 < jj0:
                continue
            out[ii0 - i0:ii1 - i0 + 1, jj0 - j0:jj1 - j0 + 1] += r["fp"]["T"][l][ii0 - a0:ii1 - a0 + 1, jj0 - b0:jj1 - b0 + 1]
        return out

    def rip(self, jid: int) -> None:
        """Take a job's copper off the board."""
        r = self.route.pop(jid, None)
        if r:
            self._paint(r["fp"], -1)

    # ---- one job ---------------------------------------------------------------------------------------------------
    def route_job(self, job: dict) -> bool:
        """Route every connection of a job from scratch; True when all were found. Paints the footprint either way."""
        R = self.R
        nid, w, ci, ei = job["nid"], job["w"], job["ci"], job["ei"]
        terms = job["terms"]
        allowed = set(R.li[n] for n in job["allowed"]) if job["allowed"] else set(range(R.nL)) - {R.pl}
        near = float(R.cfg["near"])
        paths_all: list = []
        # tree: the cells joined so far; terminals are added nearest first (Prim on terminal centres)
        remaining = list(range(len(terms)))
        if job["plane"]:
            order = [0]
        else:
            first = max(remaining, key=lambda k: sum(abs(terms[k]["x"] - terms[m]["x"]) + abs(terms[k]["y"] - terms[m]["y"]) for m in remaining))
            order = [first]
        tree = set(terms[order[0]]["cells"])
        for v in terms[order[0]]["vias"]:
            tree.update(R.via_cells(v))
        done = {order[0]}
        # blocked-for-this-net rasters (static), combined once per job
        t_block = ((R.own1t[ci] != -1) & (R.own1t[ci] != nid)) | ((R.own2t[ci] != -1) & (R.own2t[ci] != nid))
        v_block = ((R.own1v[ei] != -1) & (R.own1v[ei] != nid)) | ((R.own2v[ei] != -1) & (R.own2v[ei] != nid)) | R.nopad_via
        t_block, v_block = R.extra_block(job, t_block, v_block)
        # a via larger than the board's smallest stands further off everything by what it adds
        grow = max(0.0, R.via_reach(job["net"]) - R._reach((R.via_d, R.via_dr)))
        if grow > 0.0:
            v_block = self._grow(v_block, grow + R.G / 2)[0]
        ok = True
        ctx = self._rasters(job, t_block, v_block, allowed, near, w)
        if job["plane"]:
            # a plane job is one search: from the cluster to the nearest legal via site on the plane
            p = self._search(job, ctx, tree, None)
            ok = p is not None
            if ok:
                paths_all.append(p)
        else:
            while len(done) < len(terms):
                best = None
                for k in remaining:
                    if k in done:
                        continue
                    dmin = min(abs(terms[k]["x"] - terms[m]["x"]) + abs(terms[k]["y"] - terms[m]["y"]) for m in done)
                    if best is None or dmin < best[0]:
                        best = (dmin, k)
                tgt = best[1]
                p = self._search(job, ctx, tree, tgt)
                if p is None:
                    ok = False
                    break
                paths_all.append(p)
                tree.update(p)
                done.add(tgt)
        fp = self._footprint(job, paths_all)
        self.route[job["id"]] = {"paths": paths_all, "fp": fp, "ok": ok}
        self._paint(fp, +1)
        return ok

    def _rasters(self, job, t_block, v_block, allowed, near, w) -> dict:
        """The search rasters of one job, over the bounding box of its terminals plus the window. They only change when a job is ripped, so a
        net with a hundred terminals builds them once, not a hundred times."""
        R = self.R
        nid = job["nid"]
        G = R.G
        xs = [t["x"] for t in job["terms"]]
        ys = [t["y"] for t in job["terms"]]
        wm = R.cfg["window"]
        wm = float(wm) if wm else max(8.0, R.board_mm / 4.0)
        i0, j0 = R.xy2ij(min(xs) - wm, min(ys) - wm)
        i1, j1 = R.xy2ij(max(xs) + wm, max(ys) + wm)
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(R.H - 1, i1), min(R.W - 1, j1)
        h, wd = i1 - i0 + 1, j1 - j0 + 1
        nL = R.nL
        sl = (slice(i0, i1 + 1), slice(j0, j1 + 1))
        free = np.zeros((nL, h, wd), np.uint8)
        halo = np.zeros((nL, h, wd), np.uint8)
        # endpoint zones where a restricted-layer net may still use any layer (pad access)
        ep = None
        if any(l != R.pl and l not in allowed for l in range(nL)):
            ep = np.zeros((h, wd), bool)
            rc = int(math.ceil(near / G))
            yy, xx = np.mgrid[-rc:rc + 1, -rc:rc + 1]
            se = (xx * xx + yy * yy) * G * G < near * near
            for t in job["terms"]:
                ti, tj = R.xy2ij(t["x"], t["y"])
                a, b, c, e = max(i0, ti - rc), min(i1 + 1, ti + rc + 1), max(j0, tj - rc), min(j1 + 1, tj + rc + 1)
                if a < b and c < e:
                    ep[a - i0:b - i0, c - j0:e - j0] |= se[a - ti + rc:b - ti + rc, c - tj + rc:e - tj + rc]
        for l in range(nL):
            if l == R.pl and not job["plane"]:
                continue
            f = ~t_block[l][sl]
            if l != R.pl and l not in allowed:
                f = f & ep
            free[l] = f
            occ = self.occT[l][sl].astype(np.int16)
            if len(self.same.get(nid, ())) > 1:
                occ = occ - self._same_net_occ(job, l, i0, i1, j0, j1)
            if w > R.base_w + 1e-9:                                 # the same extra width when it looks for a way through
                occ = ndi.maximum_filter(occ, size=2 * max(1, int(math.ceil(((w - R.base_w) / 2 - R.margin) / G))) + 1)
            cost = self.pf * occ + self.hist[l][sl]
            lc = job["lcost"].get(R.layers[l], 0)
            halo[l] = np.clip(cost + lc, 0, 255).astype(np.uint8)
        # a via must clear every layer and every other net's via and track raster
        occv = self.occV[sl].astype(np.int16)
        if len(self.same.get(nid, ())) > 1:
            for k in self.same[nid]:
                r = self.route.get(k)
                if k == job["id"] or not r or r["fp"]["box"] is None:
                    continue
                a0, a1, b0, b1 = r["fp"]["box"]
                ii0, ii1, jj0, jj1 = max(i0, a0), min(i1, a1), max(j0, b0), min(j1, b1)
                if ii1 >= ii0 and jj1 >= jj0:
                    occv[ii0 - i0:ii1 - i0 + 1, jj0 - j0:jj1 - j0 + 1] -= r["fp"]["V"][ii0 - a0:ii1 - a0 + 1, jj0 - b0:jj1 - b0 + 1]
        # cfg soft_vias: a via on a cell another net's copper occupies is priced like a track there, not forbidden. A hard block lets the
        # first net through a tight spot deny every layer change around it, and the nets behind it then cross it on one layer instead.
        # conflicts() sees those vias through occV
        if R.cfg["soft_vias"]:
            soft = np.clip(1.0 + self.pf * np.maximum(occv, 0) + self.hist[:, sl[0], sl[1]].max(axis=0), 1, 255)
            via_ok_u8 = np.where(v_block[sl], 0, soft).astype(np.uint8)
        else:
            via_ok_u8 = (~v_block[sl] & (occv == 0)).astype(np.uint8)
        # a job's new via keeps the drill spacing from the vias other jobs of its net already placed (one job per plane pad: same-net copper
        # is free, so two pads could drop a via on one cell). A plane job may land on such a via.
        shared = []
        guard = R.via_guard(job["net"])
        if guard > 0 and len(self.same.get(nid, ())) > 1:
            se = self._disc(guard)
            rc = se.shape[0] // 2
            for k in self.same[nid]:
                r = self.route.get(k)
                if k == job["id"] or not r:
                    continue
                for (vi, vj) in r["fp"].get("vc", ()):
                    a, b, c, e = max(i0, vi - rc), min(i1 + 1, vi + rc + 1), max(j0, vj - rc), min(j1 + 1, vj + rc + 1)
                    if a < b and c < e:
                        via_ok_u8[a - i0:b - i0, c - j0:e - j0][se[a - vi + rc:b - vi + rc, c - vj + rc:e - vj + rc]] = 0
                    if i0 <= vi <= i1 and j0 <= vj <= j1:
                        shared.append((vi - i0, vj - j0))
        return {"box": (i0, i1, j0, j1), "h": h, "wd": wd, "free": free, "halo": halo, "via": np.ascontiguousarray(via_ok_u8), "shared": shared}

    def _search(self, job, ctx, tree, tgt):
        """One A* run from the tree to terminal `tgt` (or to the plane for a plane job); returns the cell path or None."""
        R = self.R
        i0, i1, j0, j1 = ctx["box"]
        h, wd, free = ctx["h"], ctx["wd"], ctx["free"]
        nL = R.nL
        tgt_t = job["terms"][tgt] if tgt is not None else None
        goal = np.zeros((nL, h, wd), np.uint8)
        if tgt_t is not None:
            for (l, i, j) in tgt_t["cells"]:
                if i0 <= i <= i1 and j0 <= j <= j1:
                    goal[l, i - i0, j - j0] = 1
            for v in tgt_t["vias"]:
                for (l, i, j) in R.via_cells(v):
                    if i0 <= i <= i1 and j0 <= j <= j1:
                        goal[l, i - i0, j - j0] = 1
            ti, tj = R.xy2ij(tgt_t["x"], tgt_t["y"])
            ti, tj = ti - i0, tj - j0
            use_h = 1
        else:
            sl = (slice(i0, i1 + 1), slice(j0, j1 + 1))
            goal[R.pl] = free[R.pl] & (ctx["via"] > 0)
            # several plane nets on one board: the job carries the region where its plane exists and the routing-layer cells already tied
            # to that plane (a dog-bone via and its short track): reaching those needs no new via
            goal[R.pl] &= job["plane_mask"][sl].astype(np.uint8)
            for (l, i, j) in job.get("plane_cells", ()):
                if i0 <= i <= i1 and j0 <= j <= j1:
                    goal[l, i - i0, j - j0] = 1
            free[R.pl] = goal[R.pl]                         # the plane layer is an end point, never a corridor
            for (a, c) in ctx["shared"]:
                for l in range(nL):
                    if l != R.pl:
                        goal[l, a, c] = 1
            ti = tj = 0
            use_h = 0
        starts = []
        for (l, i, j) in tree:
            if i0 <= i <= i1 and j0 <= j <= j1:
                starts.append((l * h + (i - i0)) * wd + (j - j0))
                free[l, i - i0, j - j0] = 1                 # a start cell is the net's own copper
        if not starts or not goal.any():
            return None
        st = np.array(starts, np.int64)
        cap = min(nL * h * wd, MAX_EXPAND + 16)
        path = np.empty(cap, np.int64)
        nexp = ctypes.c_int64(0)
        P = lambda a, t: a.ctypes.data_as(ctypes.POINTER(t))    # noqa: E731
        n = R.astar(P(free, ctypes.c_uint8), P(ctx["via"], ctypes.c_uint8), P(ctx["halo"], ctypes.c_uint8), P(goal, ctypes.c_uint8), nL, h, wd,
                    P(st, ctypes.c_int64), len(st), float(R.cfg["via_cost"]), 1.0, use_h, int(ti), int(tj), R.corner_cut, MAX_EXPAND,
                    P(path, ctypes.c_int64), cap, ctypes.byref(nexp))
        if n < 0:                                           # bad arguments or no memory: a defect here, not a routing result
            raise SystemExit(f"pf_route: the search core returned {n} on net {job['net']} ({h} x {wd} x {nL} cells)")
        if n == 0:
            return None
        out = []
        for k in path[:n]:
            l, rem = divmod(int(k), h * wd)
            i, j = divmod(rem, wd)
            out.append((l, i + i0, j + j0))
        return out

    # ---- conflicts -------------------------------------------------------------------------------------------------
    def conflicts(self) -> dict[int, int]:
        """{job id: number of its centreline cells that lie inside another net's footprint} (unrouted jobs count as 10**6)."""
        bad = {}
        self.bad_cells = {}
        R_nL = self.R.nL
        for job in self.jobs:
            r = self.route.get(job["id"])
            if job["id"] in self.frozen or (r is None and job["id"] in self.waiting):
                continue
            if r is None or not r["ok"]:
                bad[job["id"]] = 10 ** 6
                continue
            fp = r["fp"]
            n = 0
            self.bad_cells[job["id"]] = []
            for (l, i, j, cnt) in self._suspects(fp):
                if len(self.same.get(job["nid"], ())) > 1:
                    for k in self.same[job["nid"]]:
                        rk = self.route.get(k)
                        if k == job["id"] or not rk or rk["fp"]["box"] is None or l not in rk["fp"]["T"]:
                            continue
                        a0, a1, b0, b1 = rk["fp"]["box"]
                        if a0 <= i <= a1 and b0 <= j <= b1 and rk["fp"]["T"][l][i - a0, j - b0]:
                            cnt -= 1
                if cnt > 0:
                    n += 1
                    self.bad_cells[job["id"]].append((l, i, j))
            # a plane job that landed on a same-net job's via needs that via to still be there: when the job that placed it moved, go again
            if job["plane"] and r["paths"]:
                end = r["paths"][-1][-1]
                if end[0] != self.R.pl and end not in job.get("plane_set", ()):
                    # another job's end only counts on the plane itself: two jobs that end on one cell on two routing layers would each take
                    # the other for the way down, and no via exists
                    ends = {(rk["paths"][-1][-1][1], rk["paths"][-1][-1][2]) for k in self.same.get(job["nid"], ()) if k != job["id"]
                            for rk in [self.route.get(k)] if rk and rk["paths"] and rk["paths"][-1][-1][0] == self.R.pl}
                    live = any((end[1], end[2]) in self.route[k]["fp"].get("vc", ()) for k in self.same.get(job["nid"], ())
                               if k != job["id"] and k in self.route)
                    if not live and (end[1], end[2]) not in ends:
                        n += 1
            # a new via on a cell another net's via raster covers: via to via, or via to track on a layer the track check misses
            for (i, j) in fp.get("vc", ()):
                i0, i1, j0, j1 = fp["box"]
                cnt = int(self.occV[i, j]) - int(fp["V"][i - i0, j - j0])
                if cnt > 0:
                    for k in self.same.get(job["nid"], ()):
                        rk = self.route.get(k)
                        if k == job["id"] or not rk or rk["fp"]["box"] is None:
                            continue
                        a0, a1, b0, b1 = rk["fp"]["box"]
                        if a0 <= i <= a1 and b0 <= j <= b1 and rk["fp"]["V"][i - a0, j - b0]:
                            cnt -= 1
                if cnt > 0:
                    n += 1
                    self.bad_cells[job["id"]].extend((l, i, j) for l in range(R_nL))
            if n:
                bad[job["id"]] = n
        return bad

    def _suspects(self, fp: dict) -> list:
        """Cells of a job's copper where some other job's paint lies: (layer, i, j, how many). numpy over the whole centreline, so a
        negotiation round does not spend its time in a python loop over cells nobody else is near."""
        if fp["box"] is None or not fp["cl"]:
            return []
        i0, i1, j0, j1 = fp["box"]
        arr = np.array(sorted(fp["cl"]), np.int32)
        L, I, J = arr[:, 0], arr[:, 1], arr[:, 2]
        own = np.zeros(len(arr), np.int16)
        for l, msk in fp["T"].items():
            sel = np.nonzero((L == l) & (I >= i0) & (I <= i1) & (J >= j0) & (J <= j1))[0]
            own[sel] = msk[I[sel] - i0, J[sel] - j0]
        cnt = self.occT[L, I, J].astype(np.int16) - own
        hit = np.nonzero(cnt > 0)[0]
        return [(int(L[k]), int(I[k]), int(J[k]), int(cnt[k])) for k in hit]

    def run(self, iters: int, deadline: float) -> dict:
        """The negotiation: route everything, then rip and re-route what collides, until nothing does or a limit is hit."""
        R = self.R
        cfg = R.cfg
        order = list(self.jobs)
        pri = list(cfg.get("order") or ())

        def priority(j) -> int:
            """Index of the first `order` pattern that names this job's net."""
            return next((k for k, q in enumerate(pri) if net_matches(q, j["net"])), len(pri))
        order.sort(key=lambda j: (priority(j), -self._span(j)))
        t0 = time.time()
        # cfg late_nets: these jobs join once the others stop colliding (or at round late_after), then negotiate with them.
        # Default: the plane nets, whose hundreds of small jobs would only get in the way of the signals finding their paths
        late_pats = cfg.get("late_nets")
        is_late = (lambda n: any(net_matches(q, n) for q in late_pats)) if late_pats is not None else R.is_plane_net
        late_after = int(cfg["late_after"])
        self.waiting = {j["id"] for j in order if is_late(j["net"])}
        order = [j for j in order if j["id"] not in self.waiting]
        stall_iters = int(cfg["stall_iters"])
        stall_mm = cfg["stall_mm"]
        stall_mm = [float(v) for v in (stall_mm if isinstance(stall_mm, list) else [stall_mm])]
        stall_n = 0
        patience = int(cfg["patience"])
        # the step cost is one byte: hist + pf * occupancy clips at 255. With a long history a region prices free and occupied cells the same
        # and the negotiation goes blind there. hist_cap < pf_cap keeps an occupied cell dearer than any free one
        hist_cap, pf_cap, hist_step = int(cfg["hist_cap"]), float(cfg["pf_cap"]), int(cfg["hist_step"])
        for j in order:
            self.route_job(j)
        bad = self.conflicts()
        self.log(f"initial: routed {len(self.jobs) - sum(1 for v in bad.values() if v >= 10 ** 6)}/{len(self.jobs)}, conflicts {len(bad)} jobs  ({time.time() - t0:.0f} s)")
        frozen_iters = int(cfg["late_frozen_iters"])
        joined_at = None
        late_ids = set(self.waiting)
        score = lambda b: sum(1 if k in late_ids else 100 for k in b)        # noqa: E731  an open plane pad weighs less than an open signal
        best = (score(bad), dict(self.route))
        last_gain, floor = 0, len(bad)
        best_at, best_len = 0, len(bad) + len(self.waiting)
        walled = 0
        for it in range(1, iters + 1):
            if self.waiting and (not bad or it >= late_after):
                jl = sorted(self.waiting)
                self.waiting = set()
                # cfg late_frozen: the finished jobs keep their copper and the late ones work around it. A late job that finds no way stays in
                # conflict and is left out at the end
                if cfg["late_frozen"]:
                    self.frozen = {k for k in self.route if k not in bad}
                for k in jl:
                    self.route_job(self.jobs[k])
                bad = self.conflicts()
                best = (10 ** 9, dict(self.route))
                last_gain, floor, joined_at = it, len(bad), it
                best_at, best_len = it, len(bad)
                self.log(f"iter {it}: {len(jl)} late jobs joined -> conflicts {len(bad)}")
            if self.frozen and frozen_iters and joined_at is not None and it - joined_at >= frozen_iters and bad:
                self.frozen = set()
                bad = self.conflicts()
                last_gain, floor = it, len(bad)
                self.log(f"iter {it}: frozen jobs released -> conflicts {len(bad)}")
            if not bad or time.time() > deadline:
                break
            # other nets' copper only costs, it never blocks, so a job with no path is shut in by something that will not move: a pad, a keep-out,
            # the edge, a via rule. Once only such jobs are left, more rounds change nothing
            walled = walled + 1 if all(v >= 10 ** 6 for v in bad.values()) else 0
            if walled >= 10:
                self.log(f"iter {it}: only {len(bad)} jobs with no path are left, shut in by fixed copper or rules, stopping")
                break
            # the stop rule of docs/PCB.md: a run that stopped improving will not fix itself by running longer
            if patience and it - best_at >= patience:
                self.log(f"iter {it}: no new best for {patience} rounds (best {best_len}), stopping. Render the board and read it: docs/PCB.md, 'When the router gets stuck'")
                break
            # cfg stop_file: the operator's way out, the run ends at the next round and still writes its best round and its conflict map
            if cfg.get("stop_file") and os.path.exists(cfg["stop_file"]):
                self.log(f"iter {it}: stop file found")
                break
            # history: every conflicting centreline cell gets dearer for good
            for jid in bad:
                for (l, i, j) in self.bad_cells.get(jid, ()):
                    a, b, c, e = max(0, i - 2), i + 3, max(0, j - 2), j + 3
                    self.hist[l, a:b, c:e] = np.minimum(hist_cap, self.hist[l, a:b, c:e].astype(np.int16) + hist_step).astype(np.uint8)
            self.pf = min(pf_cap, self.pf * float(cfg["pf_grow"]))
            todo = sorted(bad, key=lambda k: -bad[k])
            # a pair stuck for stall_iters rounds: the net that blocks their way is not in conflict and would never move on its own, so
            # everything with copper within stall_mm of the conflict cells comes off at once and the stuck pair goes back first
            spread = False
            if stall_iters and it - last_gain >= stall_iters:
                # every stall starts again from the best round seen, with a wider circle each time: one kick that went nowhere does not drift
                # the whole board away from a near solution (the history keeps growing, so the same kick is not the same twice)
                if cfg["keep_best"] and not self.waiting and score(bad) > best[0]:
                    self._restore(best[1])
                    bad = self.conflicts()
                    todo = sorted(bad, key=lambda k: -bad[k])
                todo += [k for k in self._neighbours(stall_mm[stall_n % len(stall_mm)]) if k not in bad and k not in self.frozen]
                stall_n += 1
                last_gain, spread, floor = it, True, 10 ** 9     # the kick raises the count: every fall from it counts as progress
            if cfg["sequential"] and not spread:
                # the textbook loop: one net off, the same net back on against everybody else's current copper. Ripping the whole conflict
                # set first lets the first net back take the contested cells for free, so the set just rotates
                for jid in todo:
                    self.rip(jid)
                    self.route_job(self.jobs[jid])
            else:
                for jid in todo:
                    self.rip(jid)
                for jid in todo:
                    self.route_job(self.jobs[jid])
            bad = self.conflicts()
            if len(bad) < floor:
                floor, last_gain = len(bad), it
            if len(bad) < best_len:
                best_at, best_len = it, len(bad)
            if cfg["keep_best"] and not self.waiting and score(bad) < best[0]:
                best = (score(bad), dict(self.route))               # route records are replaced, never edited: a shallow copy holds
            unrouted = sum(1 for v in bad.values() if v >= 10 ** 6)
            self.log(f"iter {it}: pf {self.pf:.1f} jobs rerouted {len(todo)} -> conflicts {len(bad)} (unrouted {unrouted})  ({time.time() - t0:.0f} s)")
        if self.waiting:                                            # the run ended before the late jobs joined: they are open, and counted
            for k in sorted(self.waiting):
                self.route_job(self.jobs[k])
            self.waiting = set()
            bad = self.conflicts()
        if cfg["keep_best"] and score(bad) > best[0]:
            # back to the best round seen: repaint its occupancy, so the conflict list that goes out describes the copper that goes out
            self._restore(best[1])
            self.frozen = set()
            bad = self.conflicts()
            self.log(f"kept the best round: {len(bad)} conflicts")
        return bad

    def _restore(self, route: dict) -> None:
        """Back to a saved round: its route records, and the occupancy repainted from them."""
        self.route = dict(route)
        self.occT[:] = 0
        self.occV[:] = 0
        for r in self.route.values():
            self._paint(r["fp"], +1)

    def _neighbours(self, mm: float) -> list[int]:
        """Jobs with a centreline cell within `mm` of a conflict cell (any layer)."""
        rc = int(mm / self.R.G)
        cells = {(i, j) for v in self.bad_cells.values() for (_l, i, j) in v}
        out = []
        for k, r in self.route.items():
            box = r["fp"].get("box")
            if box is None:
                continue
            i0, i1, j0, j1 = box
            near = [(i, j) for (i, j) in cells if i0 - rc <= i <= i1 + rc and j0 - rc <= j <= j1 + rc]
            if near and any(abs(c[1] - i) <= rc and abs(c[2] - j) <= rc for c in r["fp"]["cl"] for (i, j) in near):
                out.append(k)
        return out

    def collisions(self, top: int = 40) -> list[dict]:
        """Which net pairs collide and where (first cell of each pair), to see structural conflicts."""
        pairs: dict[tuple[str, str], list] = {}
        for jid, cells in self.bad_cells.items():
            a = self.jobs[jid]
            for (l, i, j) in cells:
                for k, r in self.route.items():
                    b = self.jobs[k]
                    if k == jid or b["nid"] == a["nid"] or r["fp"]["box"] is None or l not in r["fp"]["T"]:
                        continue
                    a0, a1, b0, b1 = r["fp"]["box"]
                    if a0 <= i <= a1 and b0 <= j <= b1 and r["fp"]["T"][l][i - a0, j - b0]:
                        pairs.setdefault((a["net"], b["net"]), []).append([self.R.layers[l], *[round(v, 2) for v in self.R.ij2xy(i, j)]])
        rows = sorted(pairs.items(), key=lambda kv: -len(kv[1]))[:top]
        return [{"a": x, "b": y, "cells": len(v), "at": v[0]} for (x, y), v in rows]

    def _span(self, job) -> float:
        """Half the perimeter of a job's bounding box: long nets go first."""
        xs = [t["x"] for t in job["terms"]]
        ys = [t["y"] for t in job["terms"]]
        return (max(xs) - min(xs)) + (max(ys) - min(ys))

    # ---- output ----------------------------------------------------------------------------------------------------
    def emit(self) -> dict:
        """Convert the cell paths to merged track segments and vias in board mm."""
        tracks, vias = [], []
        for job in self.jobs:
            r = self.route.get(job["id"])
            if not r:
                continue
            for p in r["paths"]:
                self._emit_path(p, job["net"], job["w"], job, tracks, vias)
        return {"tracks": tracks, "vias": vias}

    def _emit_path(self, p, net, w, job, tracks, vias) -> None:
        """One cell path as track runs and vias. A run that ends on the cell of a via or a sub-grid pad gets a link to the true centre."""
        R = self.R
        pts = [(c[0], *R.ij2xy(c[1], c[2])) for c in p]
        runs: list = []
        cur_l = pts[0][0]
        cur = [(pts[0][1], pts[0][2])]
        for a, b in zip(pts, pts[1:]):
            if b[0] != a[0]:
                runs.append((cur_l, cur))
                vias.append([round(a[1], 4), round(a[2], 4), *R.via_of(net), net])
                cur_l, cur = b[0], [(b[1], b[2])]
            else:
                cur.append((b[1], b[2]))
        runs.append((cur_l, cur))
        anchors = []
        for t in job["terms"]:
            anchors += [(v[0], v[1]) for v in t["vias"]] + list(t.get("anchors", ()))
        for l, run in runs:
            if l == R.pl:                                           # the plane is an end point: nothing is drawn on it
                continue
            run = list(run)
            for ax, ay in anchors:
                for end in (0, -1):
                    if math.hypot(run[end][0] - ax, run[end][1] - ay) < 0.75 * R.G and (run[end][0], run[end][1]) != (ax, ay):
                        if end == 0:
                            run.insert(0, (ax, ay))
                        else:
                            run.append((ax, ay))
            # merge collinear steps
            seg = [run[0]]
            for q in run[1:]:
                if len(seg) >= 2:
                    ux, uy = seg[-1][0] - seg[-2][0], seg[-1][1] - seg[-2][1]
                    vx, vy = q[0] - seg[-1][0], q[1] - seg[-1][1]
                    if abs(ux * vy - uy * vx) < 1e-9 and ux * vx + uy * vy > 0:
                        seg[-1] = q
                        continue
                seg.append(q)
            for a, b in zip(seg, seg[1:]):
                if (a[0], a[1]) != (b[0], b[1]):
                    tracks.append([round(a[0], 4), round(a[1], 4), round(b[0], 4), round(b[1], 4), w, R.layers[l], net])


# =====================================================================================================================
#  Passes over the finished copper
# =====================================================================================================================
def merge_vias(res: dict, guard_of) -> int:
    """One net, two layer changes on one cell or a cell or two apart: keep one drill. Each track that ended on the dropped via gets a
    short link to the kept one on its own layer, so the copper stays joined. `guard_of(net)` is the centre distance. Returns vias dropped."""
    by_net: dict[str, list] = {}
    for v in res["vias"]:
        by_net.setdefault(v[4], []).append(v)
    keep, links, dropped = [], [], 0
    for net, vs in by_net.items():
        kept: list = []
        guard = guard_of(net)
        for v in vs:
            k = next((q for q in kept if math.hypot(q[0] - v[0], q[1] - v[1]) < guard), None)
            if k is None:
                kept.append(v)
                continue
            dropped += 1
            if (k[0], k[1]) == (v[0], v[1]):
                continue
            layers = {}
            for t in res["tracks"]:
                if t[6] == net and ((abs(t[0] - v[0]) < 1e-6 and abs(t[1] - v[1]) < 1e-6) or (abs(t[2] - v[0]) < 1e-6 and abs(t[3] - v[1]) < 1e-6)):
                    layers[t[5]] = max(layers.get(t[5], 0.0), t[4])
            links += [[v[0], v[1], k[0], k[1], w, ln, net] for ln, w in layers.items()]
        keep += kept
    res["vias"] = keep
    res["tracks"] += links
    return dropped


def widen(R: Router, res: dict, targets: dict) -> dict:
    """Bring rails that had to route thin back up to their width, wherever the copper around them allows it.
    A rail that ties balls inside a BGA cannot be 0.25 mm through the gap between two vias, and at one width for its whole length the
    router has to find it 0.25 mm of room everywhere: the field then never settles. So such a rail routes at signal width and this pass
    widens it segment by segment, longest first, against every other net's copper as it stands, the board edge and the keep-outs.
    A segment that has no room for the full width tries two steps below it, then stays as it was: a neck is a neck, never a violation.
    `targets` is {net pattern: mm}. Changes the track rows of `res` in place. Returns {net: [mm at the full width, mm left narrower]}."""
    margin = R.margin
    gap_of = lambda a, b: R.clr + max(R.extra_of(a), R.extra_of(b)) + margin     # noqa: E731
    copper: dict[str, list] = {ln: [] for ln in R.layers}          # layer -> (shape, net)
    forbid: dict[str, list] = {ln: [] for ln in R.layers}          # layer -> keep-out shapes
    for p in R.d["pads"]:
        for ln, rings in p["polys"].items():
            g = rings_to_geom(rings)
            if ln in copper and g is not None:
                copper[ln].append((g, p["net"]))
        if p["drill"] > 0 and not p["plated"]:                      # a plain hole: the same test, drawn out by what hole_clr adds to clearance
            for ln in R.layers:
                copper[ln].append((Point(p["x"], p["y"]).buffer(p["drill"] / 2 + R.hole_clr - R.clr), ""))
    for v in list(R.d["vias"]) + list(res["vias"]):
        r = max(v[2] / 2, v[3] / 2 + R.hole_clr - R.clr)            # whichever reaches further, the pad or the drill's hole clearance
        for ln in R.layers:
            copper[ln].append((Point(v[0], v[1]).buffer(r), v[4]))
    for t in list(R.d["tracks"]) + list(res["tracks"]):
        if t[5] in copper:
            copper[t[5]].append((LineString([(t[0], t[1]), (t[2], t[3])]).buffer(t[4] / 2), t[6]))
    for c in R.d.get("copper", ()):
        if c["layer"] in copper:
            copper[c["layer"]].append((rings_to_geom(c["rings"]), ""))
    for a in R.d["areas"]:
        if a["tracks"]:
            for ln in a["layers"]:
                if ln in forbid:
                    forbid[ln].append(rings_to_geom(a["rings"]))
    inside = R.board.buffer(-R.edge)
    trees = {ln: STRtree([c[0] for c in items]) for ln, items in copper.items() if items}
    grown: dict[str, list] = {ln: [] for ln in R.layers}            # segments already widened: the trees still hold their old shape
    stats: dict[str, list] = {}
    todo = sorted((t for t in res["tracks"] if pattern_lookup(targets, t[6]) is not None), key=lambda t: -math.hypot(t[2] - t[0], t[3] - t[1]))
    for t in todo:
        net, ln, full = t[6], t[5], float(pattern_lookup(targets, t[6]))
        line = LineString([(t[0], t[1]), (t[2], t[3])])
        length = line.length
        if length < 1e-6 or ln not in trees:
            continue
        for w in (full, (full + t[4]) / 2, (full + 3 * t[4]) / 4):
            if w <= t[4]:
                continue
            g = line.buffer(w / 2)
            if not inside.contains(g):
                continue
            if any(k is not None and g.intersects(k) for k in forbid[ln]):
                continue
            near = trees[ln].query(g.buffer(R.clr + max(R.net_clr.values()) - min(R.net_clr.values()) + margin))
            if any(copper[ln][int(i)][1] != net and g.distance(copper[ln][int(i)][0]) < gap_of(net, copper[ln][int(i)][1]) for i in near):
                continue
            if any(n != net and g.distance(s) < gap_of(net, n) for s, n in grown[ln]):
                continue
            t[4] = round(w, 4)
            grown[ln].append((g, net))
            break
        row = stats.setdefault(net, [0.0, 0.0])
        row[0 if abs(t[4] - full) < 1e-6 else 1] += length
    return {k: [round(v[0], 1), round(v[1], 1)] for k, v in stats.items()}


# =====================================================================================================================
#  One run
# =====================================================================================================================
def below_normal() -> None:
    """Drop this process to BelowNormal priority on Windows: a long route must not take the machine from whoever is at it."""
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
        except (AttributeError, OSError) as e:              # not worth stopping a route for, worth a line in the log
            print(f"pf_route: priority left as it was ({e})", flush=True)


def solve(problem: dict, cfg: dict | None, iters: int, minutes: float, log=print) -> tuple[dict, bool]:
    """Route one problem. Returns (result, closed): `closed` is True when every job routed and nothing collides."""
    R = Router(problem, cfg)
    jobs = R.jobs()
    for w in R.warnings:
        log(f"warning: {w}")
    log(f"nets {len(R.nets)}, jobs {len(jobs)} ({sum(1 for j in jobs if j['plane'])} to planes), grid {R.W} x {R.H} x {R.nL} at {R.G} mm, "
        f"layers {R.layers}, clearance {R.clr} (+ class extras {R.extras}), widths {sorted({w for w, _e in R.classes})}, "
        f"via {R.via_d} / {R.via_dr}")
    N = Negotiator(R, jobs, log=log)
    bad = N.run(iters, time.time() + minutes * 60)
    # ---- where the negotiation ended: the unrouted terminals and the colliding net pairs, the next run's starting point
    open_rows, pairs = [], N.collisions()
    for k, v in bad.items():
        why = "no path" if v >= 10 ** 6 else "in conflict"
        open_rows.append({"net": jobs[k]["net"], "terminals": [t["ref"] for t in jobs[k]["terms"]], "why": why})
        log(f"OPEN {jobs[k]['net']}: {why}, {[t['ref'] for t in jobs[k]['terms']][:8]}")
    for row in pairs:
        log(f"   {row['a']} x {row['b']}: {row['cells']} cells, e.g. {row['at']}")
    if not R.cfg.get("emit_all"):
        # the colliding and unrouted jobs stay off the board: a board with a short is worth less than one with an open
        N.route = {k: v for k, v in N.route.items() if k not in bad}
    res = N.emit()
    n_merged = merge_vias(res, R.via_guard)
    widened = widen(R, res, R.cfg["widen"]) if R.cfg.get("widen") else {}
    res["open"], res["collisions"] = open_rows, pairs
    res["warnings"] = R.warnings
    res["stats"] = {"jobs": len(jobs), "vias_merged": n_merged, "conflicts": sum(1 for v in bad.values() if v < 10 ** 6),
                    "unrouted": sum(1 for v in bad.values() if v >= 10 ** 6), "widened_mm": widened}
    return res, not bad


def main() -> int:
    """Command line: problem, config, result."""
    below_normal()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    iters = minutes = None
    if "--iters" in sys.argv:
        iters = sys.argv[sys.argv.index("--iters") + 1]
        args.remove(iters)
    if "--minutes" in sys.argv:
        minutes = sys.argv[sys.argv.index("--minutes") + 1]
        args.remove(minutes)
    if len(args) < 3:
        print(__doc__)
        return 2
    try:
        with open(args[0], encoding="utf-8") as fh:
            problem = json.load(fh)
        cfg = {}
        if args[1] != "-":
            with open(args[1], encoding="utf-8") as fh:
                cfg = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"pf_route: cannot read the input: {e}", file=sys.stderr)
        return 2
    res, closed = solve(problem, cfg, int(iters or cfg.get("iters", 400)), float(minutes or cfg.get("minutes", 60.0)),
                        log=lambda s: print(s, flush=True))
    with open(args[2], "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, separators=(",", ":"))
    print("pf_route done:", json.dumps(res["stats"]), "tracks", len(res["tracks"]), "vias", len(res["vias"]), flush=True)
    return 0 if closed else 1


if __name__ == "__main__":
    sys.exit(main())
