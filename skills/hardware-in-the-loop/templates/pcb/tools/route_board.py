#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 route_board.py - route a KiCad board with the negotiated router: everything it needs is read from the board itself
=======================================================================================================================
 Interpreter: KiCad 10's bundled python, the only one that has pcbnew. The search runs in a child process, pf_route.py,
              which needs numpy, scipy and shapely (and a C compiler the first time). Searched in this order:
                HIL_ROUTER_PYTHON   a python of your own that has them
                this python         once they are in HIL_KICAD_SITE, the same folder place_labels.py reads shapely from:
                                    "<KiCad python>" -m pip install --target <dir> scipy shapely
                HIL_CQ_PYTHON       the CadQuery venv from the skill's setup script

 Run:         "<KiCad python>" route_board.py <board.kicad_pcb> [--out <routed.kicad_pcb>] [--config <cfg.json>]
                                              [--iters N] [--minutes M] [--work <dir>] [--result <route.json>] [--no-drc]

              --out      routed board, default <board>_routed.kicad_pcb beside the input, with its project and custom rules.
                         The input is never written unless --out names it.
              --config   JSON, default <board>.route.json beside the board when that file exists
              --work     problem, result and log files, default <routed board>_route/ beside the output
              --result   also write every new track and via to this JSON (what board_pipeline.py freezes)
              --no-drc   skip the closing kicad-cli DRC (the report then has no DRC numbers)
 Exit:        0 = closed: nothing open, and DRC clean unless --no-drc. 1 = the board is saved as far as it got and the
              report lists what is open and what DRC found. 2 = bad input. 3 = a package or compiler is missing.
 Paths: command line.

 What it reads from the board, so nothing here is a constant for one design
   copper stack, outline with its cut-outs, pads with their real shapes, holes and slots, existing tracks and vias, net
   names, each net's netclass (clearance, track width, via size), the board's minimums (clearance, track, via, drill,
   annular ring, copper to edge, hole clearance, hole to hole), pours, keep-out rule areas on the board and inside
   footprints, drawn copper and copper text.

 What it decides from that, and prints, so you can see it and overrule it
   Plane layers: an inner layer is a plane when its pours cover at least `plane_cover` of the board and at least one of
   them is a net's first inner pour. A second inner pour of a net stays a routing layer and flows round the tracks.
   Every other copper layer carries tracks. A net with a plane ends at vias into it. A pour on a routing layer flows round
   whatever crosses it and does not count as a connection, so its net routes like any other (`pour_first` changes that).
   Copper already on the board stays. Run it on a half routed board and only what is open gets routed.
   When a pass leaves something open, the next one looks at the filled board and routes what is still in pieces.

 What it does not do: length matching, differential pairs as pairs, impedance, blind or buried vias, curved tracks, or
 custom rules in a .kicad_dru (the closing DRC does check those). docs/PCB.md has the list and when to use Freerouting.

 Config: JSON, every key optional. A net pattern is the exact net name, with or without KiCad's leading '/', or a full
 regex over it. A pattern that matches no net prints a warning.

   Rules and shapes
     width        {pattern: mm}             track width instead of the netclass's
     clearance    {pattern: mm}             more clearance than the netclass's (a high-voltage rail); never less
     via          [diameter, drill]         every new via, instead of the netclass's
     via_net      {pattern: [dia, drill]}   one net's new vias
     widen        {pattern: mm}             route at the width above, then widen to this wherever the copper allows
     via_in_pad   [pattern]                 pads, as "U1.A3" or "U1\\..*", that may take a via: needed under a fine BGA.
                                            Say so to the fab: via-in-pad means plugged and capped.
   Layers
     plane_cover     0.4                    share of the board a layer's pours must cover to count as a plane
     plane_layers    [layer]                state the plane layers instead of detecting them
     routing_layers  [layer]                state the routing layers instead
     planes          {pattern: {...}}       a net's plane by hand: "inside" [x0, y0, x1, y1] replaces the region from the fill,
                                            "also" and "exclude" are lists of such boxes
     layers          {pattern: [layer]}     layers a net may use. Within `near` mm of a terminal any layer is still open
     layer_cost      {pattern: {layer: n}}  surcharge per cell on a layer: a fast net pays to leave its reference plane
     layer_box       {layer: [x0, y0, x1, y1]}  a layer that takes tracks only inside a box
     dir_layers      [layer, layer]         east-west and north-south preference (default: picked from the routing layers)
     dir_cost        1
     solid_zones     [pattern]              pours other nets must stay out of. Default: pours flow round everything
     pour_first      false                  true: nets with a pour on a routing layer wait for pass 2 and get only what the fill
                                            did not join. Less copper, but their pads can get walled in: nothing negotiates
                                            with them, so the second pass has to work round everyone else's frozen tracks
   Keep-outs beyond the rule areas on the board
     keepouts     [{"rect": [x0, y0, x1, y1]} or {"around": ["Y1"], "margin": 0.8}, plus "layers": [...], "exempt": [pattern],
                  "vias": true]             `around` builds the box from a part's pads and exempts that part's own nets
   Search
     order        [pattern]                 nets that go first. Crystals and memory buses like it
     late_nets    [pattern]                 nets that join after the others settle. Default: the plane nets
     skip         [pattern]                 nets left alone
     grid 0.05    margin 0.02    window auto    near 2.0    via_cost 22    corner_cut true    soft_vias true
     sequential true    keep_best true    hist_cap 110    hist_step 1    pf_cap 60    pf_grow 1.2    late_after 6
     late_frozen false    stall_iters 40    stall_mm [1.5, 3, 6, 1000]    patience 120    max_ram_gb 12
     iters 400    minutes 60    passes 3    emit_all false (true also writes jobs that still collide: for a hand fix, never for fab)
     stop_file "<path>"                     the run ends at the next round when this file appears, and still saves its best
=======================================================================================================================
"""
from __future__ import annotations

import argparse
import json
import os
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
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import kicad_env  # noqa: E402

SCHEMA = "hil-route-problem/1"
REPORT_SCHEMA = "hil-route-report/1"
PLANE_FRAGMENT = 0.25       # a fill fragment under this share of its zone's largest is a torn-off island, not the plane


# =====================================================================================================================
#  Reading the board
# =====================================================================================================================
def _ring(chain, MM) -> list:
    """A SHAPE_LINE_CHAIN as [[x, y], ...] in mm."""
    return [[round(MM(chain.CPoint(i).x), 4), round(MM(chain.CPoint(i).y), 4)] for i in range(chain.PointCount())]


def _rings(ps, MM) -> list:
    """A SHAPE_POLY_SET as [{"outer": ring, "holes": [ring, ...]}, ...]."""
    if ps is None:
        return []
    return [{"outer": _ring(ps.Outline(i), MM), "holes": [_ring(ps.Hole(i, h), MM) for h in range(ps.HoleCount(i))]}
            for i in range(ps.OutlineCount())]


def _zone_layers(b, z, copper: list) -> list:
    """Copper layers of a zone, as names."""
    return [b.GetLayerName(l) for l in z.GetLayerSet().Seq() if b.GetLayerName(l) in copper]


def _pour_zones(b) -> list:
    """Copper zones that pour: rule areas and the small teardrop zones KiCad generates are something else."""
    return [z for z in b.Zones() if not z.GetIsRuleArea() and not z.IsTeardropArea()]


def _board_outline(b):
    """The board outline as a SHAPE_POLY_SET. A board without a closed Edge.Cuts cannot be routed: stop and say so."""
    import pcbnew
    ps = pcbnew.SHAPE_POLY_SET()
    if not b.GetBoardPolygonOutlines(ps, False) or ps.OutlineCount() < 1:
        raise SystemExit("route_board: no closed board outline on Edge.Cuts; close it first (kicad-cli DRC lists the gap)")
    return ps


def classify_layers(b, copper: list, cfg: dict, log) -> tuple[list, list, dict]:
    """Split the copper stack into routing and plane layers. Returns (routing, planes, {plane layer: [zones on it]})."""
    board = _board_outline(b)
    area = max(board.Area(), 1.0)
    cover = float(cfg.get("plane_cover", 0.4))
    zones = _pour_zones(b)
    on_layer: dict[str, list] = {ln: [z for z in zones if ln in _zone_layers(b, z, copper)] for ln in copper}
    forced = cfg.get("plane_layers")
    if forced is not None:
        planes = list(forced)
    else:
        planes, seen = [], set()
        for ln in copper[1:-1]:                                     # outer layers carry parts: never a plane here
            zs = [z for z in on_layer[ln] if z.GetNetname()]
            share = 0.0
            for z in zs:
                ps = z.Outline().CloneDropTriangulation()
                ps.BooleanIntersection(board)
                share += ps.Area() / area
            if share >= cover and any(z.GetNetname() not in seen for z in zs):
                planes.append(ln)
                seen.update(z.GetNetname() for z in zs)
    wanted = cfg.get("routing_layers")
    for ln in list(planes) + list(wanted or ()):
        if ln not in copper:
            raise SystemExit(f"route_board: config names layer {ln!r}; this board has {copper}")
    if wanted is not None:
        planes = [ln for ln in planes if ln not in wanted]
    routing = list(wanted) if wanted is not None else [ln for ln in copper if ln not in planes]
    if not routing:
        raise SystemExit(f"route_board: no routing layer left of {copper}")
    log(f"copper {copper}: routing {routing}, planes {[(ln, sorted({z.GetNetname() for z in on_layer[ln]})) for ln in planes]}")
    return routing, planes, {ln: on_layer[ln] for ln in planes}


def _via_row(v, b, MM) -> list:
    """An existing via as [x, y, diameter, drill, top layer, bottom layer]."""
    import pcbnew
    return [round(MM(v.GetX()), 4), round(MM(v.GetY()), 4), round(MM(v.GetWidth(pcbnew.F_Cu)), 4), round(MM(v.GetDrill()), 4),
            b.GetLayerName(v.TopLayer()), b.GetLayerName(v.BottomLayer())]


def _track_rows(t, b, MM) -> list:
    """An existing track as rows [x0, y0, x1, y1, width, layer]. A curved one comes as two chords through its midpoint."""
    import pcbnew
    pts = [t.GetStart(), t.GetEnd()]
    if t.Type() == pcbnew.PCB_ARC_T:
        pts.insert(1, pcbnew.Cast_to_PCB_ARC(t).GetMid())
    w, ln = round(MM(t.GetWidth()), 4), b.GetLayerName(t.GetLayer())
    return [[round(MM(a.x), 4), round(MM(a.y), 4), round(MM(c.x), 4), round(MM(c.y), 4), w, ln] for a, c in zip(pts, pts[1:])]


def net_clusters(b) -> dict:
    """{net name: [cluster]} of the board's connected copper, as KiCad's own connectivity sees it. A cluster = pads, vias, tracks, zone."""
    import pcbnew
    MM = pcbnew.ToMM
    b.BuildConnectivity()
    conn = b.GetConnectivity()
    by_code: dict[int, list] = {}
    for fp in b.GetFootprints():
        for p in fp.Pads():
            if p.GetNetCode() > 0:
                by_code.setdefault(p.GetNetCode(), []).append(p)
    for t in b.GetTracks():
        if t.GetNetCode() > 0:
            by_code.setdefault(t.GetNetCode(), []).append(t)

    def key(x) -> str:
        # pad numbers repeat on a connector (tabs): the position keeps them apart
        return f"{x.m_Uuid.AsString()}|{x.GetPosition().x}|{x.GetPosition().y}"

    pad_t, via_t, zone_t = pcbnew.PCB_PAD_T, pcbnew.PCB_VIA_T, pcbnew.PCB_ZONE_T
    out: dict[str, list] = {}
    for code, items in by_code.items():
        name = items[0].GetNetname()
        if name.startswith("unconnected-"):
            continue
        seen: set[str] = set()
        cl = []
        for it in items:
            k = key(it)
            if k in seen:
                continue
            seen.add(k)
            rec = {"pads": [], "vias": [], "tracks": [], "zone": False}
            members = {k: it}
            for x in conn.GetConnectedItems(it):
                if x.GetNetCode() != code:
                    continue
                if x.Type() == zone_t:
                    rec["zone"] = True
                else:
                    members[key(x)] = x
            seen.update(members)
            for x in members.values():
                ty = x.Type()
                if ty == pad_t:
                    rec["pads"].append([x.GetParentFootprint().GetReference(), x.GetNumber(), round(MM(x.GetX()), 4), round(MM(x.GetY()), 4)])
                elif ty == via_t:
                    rec["vias"].append(_via_row(pcbnew.Cast_to_PCB_VIA(x), b, MM))
                elif ty in (pcbnew.PCB_TRACE_T, pcbnew.PCB_ARC_T):
                    rec["tracks"] += _track_rows(x, b, MM)
                # anything else in a cluster is drawn copper that picked up the net by touching a pad: an obstacle, listed with the rest
            cl.append(rec)
        out[name] = cl
    return out


def _netclass_row(nc, MM) -> dict:
    """What a netclass says about clearance, track width and via size."""
    return {"class": nc.GetName(), "clearance": round(MM(nc.GetClearance()), 4), "width": round(MM(nc.GetTrackWidth()), 4),
            "via": [round(MM(nc.GetViaDiameter()), 4), round(MM(nc.GetViaDrill()), 4)]}


def _local_clearance(pad, fp) -> float:
    """A clearance set on a pad, or on its footprint; 0 when neither has one."""
    import pcbnew
    v = pad.GetLocalClearance()
    if v is None:
        v = fp.GetLocalClearance()
    return round(pcbnew.ToMM(v), 4) if v else 0.0


def read_problem(b, cfg: dict, connect_all: bool, log) -> dict:
    """The routing problem of a loaded board, schema hil-route-problem/1.

    connect_all False: only plane pours count as connections, every other pour is taken off first, because tracks are about to cut
    it. True: all pours count, which is what a later pass wants: it joins only what the fill did not."""
    import pcbnew
    MM = pcbnew.ToMM
    copper = [b.GetLayerName(i) for i in b.GetEnabledLayers().CuStack()]
    lid = {n: b.GetLayerID(n) for n in copper}
    routing, planes, plane_zones = classify_layers(b, copper, cfg, log)
    outline = _board_outline(b)
    if outline.OutlineCount() > 1:
        raise SystemExit(f"route_board: Edge.Cuts holds {outline.OutlineCount()} separate boards; route one at a time")
    box = outline.BBox()
    ds = b.GetDesignSettings()
    out = {"schema": SCHEMA, "board": os.path.basename(b.GetFileName()), "layers": copper, "routing": routing, "plane_layers": planes,
           "outline": {"poly": _ring(outline.Outline(0), MM), "holes": [_ring(outline.Hole(0, h), MM) for h in range(outline.HoleCount(0))],
                       "box": [round(MM(box.GetLeft()), 4), round(MM(box.GetTop()), 4), round(MM(box.GetRight()), 4), round(MM(box.GetBottom()), 4)]},
           "rules": {"clearance": round(MM(ds.m_MinClearance), 4), "edge": round(MM(ds.m_CopperEdgeClearance), 4), "hole_clr": round(MM(ds.m_HoleClearance), 4),
                     "hole_to_hole": round(MM(ds.m_HoleToHoleMin), 4), "track_min": round(MM(ds.m_TrackMinWidth), 4), "via_min": round(MM(ds.m_ViasMinSize), 4),
                     "drill_min": round(MM(ds.m_MinThroughDrill), 4), "annular_min": round(MM(ds.m_ViasMinAnnularWidth), 4)},
           "pads": [], "vias": [], "tracks": [], "areas": [], "zones": [], "copper": [], "plane_nets": {}, "netclass": {}}
    # ---- netclasses, resolved by KiCad for each net. A class under the board's minimum clearance is held to the minimum, as DRC holds it
    ns = ds.m_NetSettings
    floor = out["rules"]["clearance"]
    dflt = _netclass_row(ns.GetDefaultNetclass(), MM)
    dflt["clearance"] = max(dflt["clearance"], floor)
    out["netclass"][""] = dflt
    for name in {str(ni.GetNetname()) for ni in b.GetNetInfo().NetsByNetcode().values()} - {""}:
        row = _netclass_row(ns.GetEffectiveNetClass(name), MM)
        row["clearance"] = max(row["clearance"], floor)
        out["netclass"][name] = row
    # ---- pads, with the shape of the copper on every layer they reach
    for fp in b.GetFootprints():
        for p in fp.Pads():
            layers = [b.GetLayerName(l) for l in p.GetLayerSet().Seq() if b.GetLayerName(l) in lid]
            dr = p.GetDrillSize()
            out["pads"].append({"ref": fp.GetReference(), "num": p.GetNumber(), "net": p.GetNetname(), "x": round(MM(p.GetX()), 4), "y": round(MM(p.GetY()), 4),
                                "layers": layers, "polys": {ln: _rings(p.GetEffectivePolygon(lid[ln]), MM) for ln in layers},
                                "drill": round(MM(max(dr.x, dr.y)), 4), "plated": bool(p.GetAttribute() != pcbnew.PAD_ATTRIB_NPTH),
                                "clearance": _local_clearance(p, fp)})
    # ---- copper already on the board stays where it is
    for t in b.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            v = pcbnew.Cast_to_PCB_VIA(t)
            r = _via_row(v, b, MM)
            out["vias"].append(r[:4] + [t.GetNetname()] + r[4:])
        else:
            out["tracks"] += [r + [t.GetNetname()] for r in _track_rows(t, b, MM)]
    # ---- drawn copper with no net: obstacles. A net tie's shape sits between its own pads and DRC judges it
    def add_copper(item) -> None:
        ps = pcbnew.SHAPE_POLY_SET()
        item.TransformShapeToPolygon(ps, item.GetLayer(), 0, pcbnew.FromMM(0.005), pcbnew.ERROR_INSIDE)
        ln = b.GetLayerName(item.GetLayer())
        if ln in lid and ps.OutlineCount():
            out["copper"].append({"layer": ln, "rings": _rings(ps, MM)})
    for d in b.GetDrawings():
        if d.IsOnCopperLayer():
            add_copper(d)
    for fp in b.GetFootprints():
        if not fp.IsNetTie():
            for g in fp.GraphicalItems():
                if g.IsOnCopperLayer():
                    add_copper(g)
    # ---- keep-out rule areas, on the board and inside footprints
    areas = [z for z in b.Zones() if z.GetIsRuleArea()]
    for fp in b.GetFootprints():
        areas += [z for z in fp.Zones() if z.GetIsRuleArea()]
    for z in areas:
        out["areas"].append({"layers": _zone_layers(b, z, copper), "rings": _rings(z.Outline(), MM),
                             "tracks": bool(z.GetDoNotAllowTracks()), "vias": bool(z.GetDoNotAllowVias())})
    # ---- pours. The fill is what tells a plane from its torn-off islands and which pads a plane already reaches
    pours = _pour_zones(b)
    for z in pours:
        out["zones"].append({"layers": _zone_layers(b, z, copper), "net": z.GetNetname(), "rings": _rings(z.Outline(), MM)})
    b.BuildConnectivity()
    if b.Zones():
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    for ln, zs in plane_zones.items():
        for z in zs:
            name = z.GetNetname()
            if not name:
                continue
            fill = z.GetFilledPolysList(lid[ln]).CloneDropTriangulation()
            fill.Unfracture()
            rows = _rings(fill, MM)
            areas_mm = [fill.Outline(i).Area() for i in range(fill.OutlineCount())]
            biggest = max(areas_mm, default=0.0)
            keep = [r for r, a in zip(rows, areas_mm) if a >= PLANE_FRAGMENT * biggest]
            if keep:
                out["plane_nets"].setdefault(name, []).append(keep)
            else:
                log(f"warning: zone of {name} on {ln} filled empty: it is no plane until it fills")
    pour_nets = sorted({z.GetNetname() for z in pours if z.GetNetname() and z.GetNetname() not in out["plane_nets"]
                        and any(ln in routing for ln in _zone_layers(b, z, copper))})
    out["pour_nets"] = pour_nets
    if not connect_all:
        for z in pours:
            if not set(_zone_layers(b, z, copper)) & set(planes):      # by layer: pcbnew hands out a new wrapper for one zone on every call
                z.UnFill()
    out["clusters"] = net_clusters(b)
    return out


# =====================================================================================================================
#  The search, in its own interpreter
# =====================================================================================================================
def find_search_python() -> str:
    """The python that runs pf_route.py. Exits with code 3, and says what to install, when none has the packages."""
    probe = "import numpy, scipy, shapely"
    site = os.environ.get("HIL_KICAD_SITE", "").strip()
    tried = []
    for label, exe, env_site in (("HIL_ROUTER_PYTHON", os.environ.get("HIL_ROUTER_PYTHON", "").strip(), ""),
                                 ("this python", sys.executable, site),
                                 ("HIL_CQ_PYTHON", os.environ.get("HIL_CQ_PYTHON", "").strip(), "")):
        if not exe:
            continue
        if not os.path.isfile(exe):
            tried.append(f"{label}: {exe} does not exist")
            continue
        code = (f"import sys; sys.path.insert(0, {env_site!r}); " if env_site else "") + probe
        r = subprocess.run([exe, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode == 0:
            return exe
        tried.append(f"{label}: {exe}: {(r.stderr or '').strip().splitlines()[-1:]}")
    print("route_board: no python here has numpy, scipy and shapely.\n" + "".join(f"  {t}\n" for t in tried)
          + f'  Install them for KiCad\'s python:  "{sys.executable}" -m pip install --target <dir> scipy shapely\n'
            "  then set HIL_KICAD_SITE=<dir>. Or set HIL_ROUTER_PYTHON to any python that has them.", file=sys.stderr)
    sys.exit(3)


def run_search(python: str, problem: dict, cfg: dict, work: str, tag: str, iters: int, minutes: float, log) -> tuple[dict, int]:
    """One pass of pf_route.py over a problem, its output going to the console and to a log. Returns (result, exit code)."""
    prob, conf, res = (os.path.join(work, f"{tag}_{n}.json") for n in ("problem", "config", "result"))
    with open(prob, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(problem, fh, separators=(",", ":"))
    with open(conf, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(cfg, fh, indent=1)
    if os.path.isfile(res):
        os.remove(res)
    cmd = [python, "-I", os.path.join(HERE, "pf_route.py"), prob, conf, res, "--iters", str(iters), "--minutes", f"{minutes:.2f}"]
    log(f"{tag}: search under {python}")
    with open(os.path.join(work, f"{tag}.log"), "w", encoding="utf-8", errors="replace", newline="\n") as lf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        for line in proc.stdout:
            print(line, end="", flush=True)
            lf.write(line)
            lf.flush()                                      # a long run can be read while it goes
        rc = proc.wait()
    if rc not in (0, 1) or not os.path.isfile(res):
        raise SystemExit(rc if rc in (2, 3) else 2)
    with open(res, encoding="utf-8") as fh:
        return json.load(fh), rc


# =====================================================================================================================
#  Writing the result back
# =====================================================================================================================
def apply_result(b, r: dict) -> None:
    """Add a route result's tracks and vias to a loaded board (nets by name). The caller refills the zones."""
    import pcbnew
    mm = pcbnew.FromMM
    V = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))          # noqa: E731

    def net(name: str):
        ni = b.FindNet(name)
        if ni is None:
            raise SystemExit(f"route_board: net {name!r} is not on the board")
        return ni

    for x0, y0, x1, y1, w, ln, name in r["tracks"]:
        if abs(x1 - x0) < 1e-4 and abs(y1 - y0) < 1e-4:     # an anchor snap can leave a zero-length step: DRC reads it as a crossing
            continue
        t = pcbnew.PCB_TRACK(b)
        t.SetStart(V(x0, y0))
        t.SetEnd(V(x1, y1))
        t.SetWidth(mm(w))
        t.SetLayer(b.GetLayerID(ln))
        t.SetNet(net(name))
        b.Add(t)
    for x, y, d, dr, name in r["vias"]:
        v = pcbnew.PCB_VIA(b)
        v.SetPosition(V(x, y))
        v.SetWidth(pcbnew.F_Cu, mm(d))                      # layer argument: the layer-less overload can assert headless
        v.SetDrill(mm(dr))
        v.SetNet(net(name))
        b.Add(v)


def open_nets(b) -> list[dict]:
    """What is still open on a filled board, by KiCad's connectivity: one row per net in more than one piece."""
    rows = []
    for name, cl in sorted(net_clusters(b).items()):
        if len(cl) > 1:
            rows.append({"net": name, "pieces": len(cl), "where": [(c["pads"][0][0] + "." + c["pads"][0][1]) if c["pads"] else "copper" for c in cl][:12]})
    return rows


def run_drc(board: str) -> dict:
    """kicad-cli DRC over the saved board with the zones refilled, at the board's own severities; returns the counts that matter."""
    cli = kicad_env.cli()
    j = os.path.splitext(board)[0] + "_drc.json"
    r = subprocess.run([cli, "pcb", "drc", "--refill-zones", "--format", "json", "--severity-all", "--units", "mm", "-o", j, board],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not os.path.isfile(j):
        raise SystemExit(f"route_board: kicad-cli DRC wrote no report (exit {r.returncode}): {(r.stdout + r.stderr).strip()[-300:]}")
    with open(j, encoding="utf-8") as fh:
        data = json.load(fh)
    viol = data.get("violations", [])
    types: dict[str, int] = {}
    for v in viol:
        k = f"{v.get('severity')}/{v.get('type')}"
        types[k] = types.get(k, 0) + 1
    return {"errors": sum(1 for v in viol if v.get("severity") == "error"), "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
            "unconnected": len(data.get("unconnected_items", [])), "types": types, "report": os.path.basename(j)}


def route(src: str, dst: str, cfg: dict, *, work: str, iters: int, minutes: float, drc: bool = True, result: str = "", log=print) -> int:
    """Route one board file: read it, search, write the copper back, fill, save, check. Returns the exit code of this tool."""
    kicad_env.isolate()
    import pcbnew
    t0 = time.time()
    deadline = t0 + minutes * 60
    python = find_search_python()
    os.makedirs(work, exist_ok=True)
    b = pcbnew.LoadBoard(src)
    if b is None:
        raise SystemExit(f"route_board: KiCad could not load {src}")
    total = {"tracks": [], "vias": []}
    passes, last_open, last_collide = [], [], []
    closed = False
    max_passes = max(1, int(cfg.get("passes", 3)))
    # ---- a later pass routes whatever the filled board still has open, against all the copper so far. With pour_first, pass 1 also leaves
    #      the nets that have a pour on a routing layer alone: their turn comes when the fill can show what they still need
    for n in range(1, max_passes + 1):
        first = n == 1
        p = read_problem(b, cfg, connect_all=not first, log=log)
        run_cfg = dict(cfg)
        if first and cfg.get("pour_first") and p["pour_nets"]:
            log(f"pass 1 leaves {p['pour_nets']} to their pours")
            run_cfg["skip"] = list(cfg.get("skip") or ()) + p["pour_nets"]
        left = max(0.2, (deadline - time.time()) / 60.0)
        res, rc = run_search(python, p, run_cfg, work, f"pass{n}", iters, left, log)
        apply_result(b, res)
        total["tracks"] += res["tracks"]
        total["vias"] += res["vias"]
        passes.append({"pass": n, "jobs": res["stats"]["jobs"], "tracks": len(res["tracks"]), "vias": len(res["vias"]),
                       "conflicts": res["stats"]["conflicts"], "unrouted": res["stats"]["unrouted"], "widened_mm": res["stats"]["widened_mm"]})
        last_open, last_collide = res["open"], res["collisions"]
        # ---- what counts is the board after the fill, not what the search believes
        if b.Zones():
            pcbnew.ZONE_FILLER(b).Fill(b.Zones())
        still = open_nets(b)
        log(f"pass {n}: +{len(res['tracks'])} tracks, +{len(res['vias'])} vias; {len(still)} nets still in pieces")
        if not still and rc == 0:
            closed = True
            break
        if not first and not res["tracks"] and not res["vias"]:     # a pass that adds nothing will not add anything the next time either
            break
    # ---- the output: board, project settings, custom rules
    if b.Zones():
        pcbnew.ZONE_FILLER(b).Fill(b.Zones())
    still = open_nets(b)
    closed = closed and not still
    if not pcbnew.SaveBoard(dst, b):
        raise SystemExit(f"route_board: SaveBoard failed for {dst}")
    dru = os.path.splitext(src)[0] + ".kicad_dru"
    if os.path.isfile(dru) and os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(dru, os.path.splitext(dst)[0] + ".kicad_dru")
    prl = os.path.splitext(dst)[0] + ".kicad_prl"
    if os.path.isfile(prl):
        os.remove(prl)                                              # per-user view state, never part of a generated set
    if result:
        with open(result, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(total, fh, separators=(",", ":"))
    report = {"schema": REPORT_SCHEMA, "board": os.path.basename(src), "routed_board": os.path.basename(dst), "closed": closed,
              "tracks": len(total["tracks"]), "vias": len(total["vias"]), "passes": passes, "open_nets": still,
              "search_open": last_open, "collisions": last_collide, "seconds": round(time.time() - t0, 1)}
    clean = True
    if drc:
        d = run_drc(dst)
        report["drc"] = d
        clean = not (d["errors"] or d["unconnected"])
        log(f"DRC: {d['errors']} errors, {d['warnings']} warnings, {d['unconnected']} unconnected {d['types']}")
    rep = os.path.splitext(dst)[0] + "_route_report.json"
    with open(rep, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(report, fh, indent=1)
    for row in still:
        log(f"OPEN {row['net']}: {row['pieces']} pieces, {row['where']}")
    log(f"{'closed' if closed and clean else 'NOT closed'}: {report['tracks']} tracks, {report['vias']} vias, {len(still)} open nets, "
        f"{report['seconds']} s -> {dst}, report {rep}")
    return 0 if closed and clean else 1


def main() -> int:
    """Command line."""
    ap = argparse.ArgumentParser(description="Route a KiCad board with the negotiated router (see the header of this file)")
    ap.add_argument("board", help=".kicad_pcb file")
    ap.add_argument("--out", default="", help="routed board (default <board>_routed.kicad_pcb)")
    ap.add_argument("--config", default="", help="JSON config (default <board>.route.json when it exists)")
    ap.add_argument("--work", default="", help="folder for the problem, result and log files")
    ap.add_argument("--result", default="", help="also write the new tracks and vias to this JSON")
    ap.add_argument("--iters", type=int, default=0, help="negotiation rounds per pass")
    ap.add_argument("--minutes", type=float, default=0.0, help="time limit of the whole run")
    ap.add_argument("--no-drc", action="store_true", help="skip the closing kicad-cli DRC")
    a = ap.parse_args()
    src = os.path.abspath(a.board)
    if not os.path.isfile(src) or not src.endswith(".kicad_pcb"):
        print(f"route_board: {src} is not a .kicad_pcb file", file=sys.stderr)
        return 2
    stem = os.path.splitext(src)[0]
    conf = a.config or (stem + ".route.json")
    cfg = {}
    if a.config or os.path.isfile(conf):
        try:
            with open(conf, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as e:
            print(f"route_board: cannot read config {conf}: {e}", file=sys.stderr)
            return 2
        if not isinstance(cfg, dict):
            print(f"route_board: config {conf} must be a JSON object", file=sys.stderr)
            return 2
        print(f"config {conf}", flush=True)
    dst = os.path.abspath(a.out) if a.out else stem + "_routed.kicad_pcb"
    work = os.path.abspath(a.work) if a.work else os.path.splitext(dst)[0] + "_route"
    return route(src, dst, cfg, work=work, iters=a.iters or int(cfg.get("iters", 400)), minutes=a.minutes or float(cfg.get("minutes", 60.0)),
                 drc=not a.no_drc, result=os.path.abspath(a.result) if a.result else "", log=lambda s: print(s, flush=True))


if __name__ == "__main__":
    sys.exit(main())
