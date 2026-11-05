#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 test_pf_route.py - the negotiated router on small problems whose answer is known, no KiCad needed.

 Interpreter: any Python >= 3.10 with numpy, scipy and shapely, plus a C compiler (same needs as pf_route.py).
 Run:         python pcb/tools/test_pf_route.py
 Paths: command line (none; every board here is built in this file).

 Checks: two crossing nets on two layers close with a via. Copper keeps the netclass clearance, width and via, and the
 board's edge rule. The clearance of a pair is the larger of the two. A plane net ends at vias inside its plane's copper.
 A net with no way through is reported open and leaves no copper. A rule area stops tracks. A config pattern that matches
 no net warns. Prints one "[hil] pf_route: P/T checks passed, F failed" line; exit 1 on failure.
=======================================================================================================================
"""
import os
import sys

# ---- Windows consoles: never crash on a character the code page lacks
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pf_route  # noqa: E402

WIDTH, HEIGHT = 12.0, 8.0                                   # mm, the test board
RESULTS = [0, 0]                                            # passed, total


def check(name: str, ok: bool, detail: str = "") -> None:
    """Count one check and print the failures."""
    RESULTS[1] += 1
    RESULTS[0] += 1 if ok else 0
    if not ok:
        print(f"FAIL {name} {detail}")


def rect(x: float, y: float, w: float, h: float) -> list:
    """A rectangle centred on (x, y) as the ring format of the problem file."""
    return [{"outer": [[x - w / 2, y - h / 2], [x + w / 2, y - h / 2], [x + w / 2, y + h / 2], [x - w / 2, y + h / 2]], "holes": []}]


def pad(ref: str, net: str, x: float, y: float, layer: str = "F.Cu") -> dict:
    """A 0.6 mm square SMD pad, and the single-pad cluster that goes with it."""
    return {"ref": ref, "num": "1", "net": net, "x": x, "y": y, "layers": [layer], "polys": {layer: rect(x, y, 0.6, 0.6)},
            "drill": 0.0, "plated": True, "clearance": 0.0}


def problem(pads: list, *, layers=("F.Cu", "B.Cu"), plane_layers=(), plane_nets=None, areas=(), classes=None) -> dict:
    """A rectangular board around the given pads, in the format route_board.py writes. Every pad starts as its own cluster."""
    default = {"class": "Default", "clearance": 0.2, "width": 0.2, "via": [0.6, 0.3]}
    nets = sorted({p["net"] for p in pads})
    routing = [ln for ln in layers if ln not in plane_layers]
    clusters: dict = {}
    for p in pads:
        clusters.setdefault(p["net"], []).append({"pads": [[p["ref"], p["num"], p["x"], p["y"]]], "vias": [], "tracks": [], "zone": False})
    return {"schema": pf_route.SCHEMA, "board": "test", "layers": list(layers), "routing": routing, "plane_layers": list(plane_layers),
            "outline": {"poly": [[0, 0], [WIDTH, 0], [WIDTH, HEIGHT], [0, HEIGHT]], "holes": [], "box": [0, 0, WIDTH, HEIGHT]},
            "rules": {"clearance": 0.1, "edge": 0.3, "hole_clr": 0.2, "hole_to_hole": 0.25, "track_min": 0.1, "via_min": 0.4, "drill_min": 0.2, "annular_min": 0.1},
            "pads": pads, "vias": [], "tracks": [], "areas": list(areas), "zones": [], "copper": [], "plane_nets": plane_nets or {},
            "netclass": {"": default, **{n: (classes or {}).get(n, default) for n in nets}}, "clusters": clusters}


def segment_gap(a: list, b: list) -> float:
    """Smallest copper gap between two track rows."""
    from shapely.geometry import LineString
    return LineString([a[:2], a[2:4]]).distance(LineString([b[:2], b[2:4]])) - (a[4] + b[4]) / 2


def quiet(_line: str) -> None:
    """Swallow the router's progress lines."""


def test_crossing() -> None:
    """Two nets that must cross: closed with a layer change, every rule the problem states kept."""
    p = problem([pad("J1", "A", 2, 4), pad("J2", "A", 10, 4), pad("J3", "B", 6, 1.5), pad("J4", "B", 6, 6.5)])
    res, closed = pf_route.solve(p, {}, 60, 2.0, log=quiet)
    check("crossing closes", closed and not res["open"], str(res["open"]))
    check("crossing uses a via", len(res["vias"]) >= 1)
    check("via from the netclass", all(v[2:4] == [0.6, 0.3] for v in res["vias"]), str(res["vias"][:2]))
    check("width from the netclass", all(t[4] == 0.2 for t in res["tracks"]))
    gaps = [segment_gap(a, b) for a in res["tracks"] for b in res["tracks"] if a[6] != b[6] and a[5] == b[5]]
    check("track clearance", not gaps or min(gaps) >= 0.2 - 1e-6, f"min gap {min(gaps) if gaps else None}")
    edge = min(min(t[0], t[2], t[1], t[3], WIDTH - max(t[0], t[2]), HEIGHT - max(t[1], t[3])) - t[4] / 2 for t in res["tracks"])
    check("edge clearance", edge >= 0.3 - 1e-6, f"{edge}")


def test_pair_clearance() -> None:
    """Two parallel nets, one in a wide-clearance class: the pair keeps the larger clearance, and only that."""
    hv = {"class": "HV", "clearance": 0.5, "width": 0.2, "via": [0.6, 0.3]}
    p = problem([pad("J1", "A", 2, 3.5), pad("J2", "A", 10, 3.5), pad("J3", "B", 2, 4.5), pad("J4", "B", 10, 4.5)], classes={"A": hv})
    res, closed = pf_route.solve(p, {"layers": {".*": ["F.Cu"]}}, 60, 2.0, log=quiet)
    gaps = [segment_gap(a, b) for a in res["tracks"] for b in res["tracks"] if a[6] != b[6]]
    check("pair closes", closed, str(res["open"]))
    check("pair keeps the larger clearance", gaps and min(gaps) >= 0.5 - 1e-6, f"min gap {min(gaps) if gaps else None}")


def test_plane() -> None:
    """A plane net: each pad gets a via into the plane's copper, and none lands in the part of the board the plane does not cover."""
    plane = [[{"outer": [[0, 0], [6, 0], [6, HEIGHT], [0, HEIGHT]], "holes": []}]]
    p = problem([pad("C1", "GND", 3, 2), pad("C2", "GND", 9, 6)], layers=("F.Cu", "In1.Cu", "B.Cu"), plane_layers=["In1.Cu"], plane_nets={"GND": plane})
    res, closed = pf_route.solve(p, {}, 60, 2.0, log=quiet)
    check("plane closes", closed, str(res["open"]))
    check("one via per pad at most", 1 <= len(res["vias"]) <= 2, str(res["vias"]))
    check("vias inside the plane", all(v[0] <= 6.0 for v in res["vias"]), str(res["vias"]))
    check("nothing drawn on the plane layer", all(t[5] != "In1.Cu" for t in res["tracks"]))


def test_blocked() -> None:
    """A rule area across the whole board on both layers: the net stays open, says so, and leaves no copper."""
    wall = {"layers": ["F.Cu", "B.Cu"], "rings": rect(6, 4, 1.0, 20.0), "tracks": True, "vias": True}
    p = problem([pad("J1", "A", 2, 4), pad("J2", "A", 10, 4), pad("J3", "B", 2, 2), pad("J4", "B", 4, 2)], areas=[wall])
    res, closed = pf_route.solve(p, {"patience": 5}, 20, 1.0, log=quiet)
    check("blocked is not closed", not closed)
    check("blocked net is named", [r["net"] for r in res["open"]] == ["A"], str(res["open"]))
    check("blocked net leaves no copper", not [t for t in res["tracks"] if t[6] == "A"])
    check("the other net still routes", bool([t for t in res["tracks"] if t[6] == "B"]))


def test_keepout_wins_detour() -> None:
    """A rule area on one layer only: the route goes round it or under it, and no track of F.Cu touches it."""
    from shapely.geometry import LineString, Polygon
    area = rect(6, 4, 2.0, 2.0)
    p = problem([pad("J1", "A", 2, 4), pad("J2", "A", 10, 4)], areas=[{"layers": ["F.Cu"], "rings": area, "tracks": True, "vias": False}])
    res, closed = pf_route.solve(p, {}, 30, 1.0, log=quiet)
    keep = Polygon(area[0]["outer"])
    hit = [t for t in res["tracks"] if t[5] == "F.Cu" and LineString([t[:2], t[2:4]]).buffer(t[4] / 2).intersects(keep)]
    check("keep-out closes", closed, str(res["open"]))
    check("keep-out kept", not hit, str(hit[:2]))


def test_warnings() -> None:
    """A typing error in the config must be heard."""
    p = problem([pad("J1", "A", 2, 4), pad("J2", "A", 10, 4)])
    res, _ = pf_route.solve(p, {"width": {"NO_SUCH_NET": 0.3}}, 10, 1.0, log=quiet)
    check("unknown pattern warns", any("NO_SUCH_NET" in w for w in res["warnings"]), str(res["warnings"]))
    check("config width is not needed to route", not res["open"])


def test_pattern() -> None:
    """Net names as KiCad writes them, including ones that are broken regexes."""
    check("name without the slash", pf_route.net_matches("GND", "/GND"))
    check("broken regex is a name", pf_route.net_matches("+3V3", "/+3V3"))
    check("regex", pf_route.net_matches("CAM_D[01][NP]", "/CAM_D1P"))
    check("regex is a full match", not pf_route.net_matches("CAM", "/CAM_D1P"))


def main() -> int:
    """Run every check."""
    for fn in (test_pattern, test_crossing, test_pair_clearance, test_plane, test_blocked, test_keepout_wins_detour, test_warnings):
        fn()
    print(f"[hil] pf_route: {RESULTS[0]}/{RESULTS[1]} checks passed, {RESULTS[1] - RESULTS[0]} failed")
    return 0 if RESULTS[0] == RESULTS[1] else 1


if __name__ == "__main__":
    sys.exit(main())
