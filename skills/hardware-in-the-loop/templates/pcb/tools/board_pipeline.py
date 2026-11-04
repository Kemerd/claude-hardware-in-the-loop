#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 board_pipeline.py - generator-first PCB pipeline: a board script DECLARES data, this module does the rest
=======================================================================================================================
 Interpreter: KiCad 10's bundled python ("C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" on Windows; on Linux the
              python that ships the pcbnew module). It is the only interpreter that has pcbnew.

 The idea
   A board is a Python script (pcb/<board>/gen_<board>.py) that declares DATA:
     * parts: reference, value, MPN, distributor order code, footprint (stock "Lib:Name" or a custom land pattern
       written from the vendor drawing), the datasheet pin table [(number, name, electrical type)], and a pin -> net map
     * the outline polygon, mounting holes, hand placement {ref: (x, y, rotation, side)}, net classes, pours, stitching
   and calls Board.run(). Every board then goes through the SAME steps, so a fix here fixes every board:

     1. design checks: every pin number exists on the footprint and vice versa, no single-pin nets, every part placed
     2. board library: every footprint copied into the board's OWN <lib>.pretty (stock ones localized with their
        silkscreen moved to the fab layers; custom ones written from pad tables)
     3. schematic + project symbol library + project lib tables, written from the data (label style, deterministic)
     4. ERC with kicad-cli (all severities, JSON): 0 errors / 0 warnings or the run stops
     5. the board with pcbnew: outline, footprints from the board library, nets, holes, rules, net classes
     6. placement DRC (courtyards, edge clearance, parity) before any routing, so a bad placement stops early
     7. routing: Freerouting 2.1 headless (Specctra DSN -> SES), BelowNormal priority, capped threads, off the home dir
     8. router remnant clean-up with KiCad's own dangling-track test, repeated until a round removes nothing
     9. copper pours + stitching vias + zone fill
    10. final DRC with schematic parity, all severities, KiCad DEFAULT severities (no rule is ever demoted here)
    11. live distributor verification of every purchased line (distributor_verify.py): code must resolve to the MPN
    12. fab set (Gerbers, Excellon, CPL, BOM in JLCPCB assembly format, zip), STEP, CAD fit JSON, results JSON with
        every count and the SHA-256 of every fab file. Fab files are written ONLY when 4, 10 and 11 are clean.

 Determinism
   The interpreter re-executes itself once with PYTHONHASHSEED=0 (set iteration order feeds numbering), schematic
   UUIDs are uuid5 of (board name, item key), and KiCad's KIID generator is seeded, so two runs of an unchanged board
   write the same schematic and board files. Freerouting with more than one thread is not bit-reproducible; the
   default thread count is therefore 1 (raise it with Board(router_threads=N) when speed matters more).

 Home-directory isolation
   kicad_env.isolate() runs BEFORE `import pcbnew` (KiCad config / documents / cache and wx temp files go to
   <repo>/.tmp/kicad_home); Freerouting runs with -Duser.home and -Djava.io.tmpdir in <out>/.router, with a seeded
   freerouting.json (Freerouting 2.1.0 defaults to allow_telemetry=true and a GUI; both are switched off).

 Hard lessons baked in (see docs/PCB.md)
   * Never Remove() a footprint's graphic items through pcbnew: freeing a C++ object under a live SWIG wrapper corrupts
     KiCad's heap, and later FootprintLoad calls return bare SwigPyObjects. Silkscreen is moved to the fab layer in the
     .kicad_mod TEXT before loading. The only Remove() calls here delete router remnant tracks / vias, and they run
     after the last FootprintLoad of the process, with a fresh track list every round and no wrapper kept afterwards.
   * Pass the layer argument to PCB_VIA width calls: the layer-less overloads raise an assert dialog in debug-assert
     builds, which hangs a headless run forever.
   * A child process with no log growth for HANG_S seconds is treated as hung and killed (by its exact PID only).

 Environment
   HIL_FREEROUTING_JAR   path of the Freerouting 2.1 jar (required for boards with routing)
   HIL_JAVA              java 21 executable (default: JAVA_HOME/bin/java, then PATH)
   HIL_KICAD_*           see kicad_env.py

 Paths: the board script passes its output folder; this module resolves its own helpers from __file__.
=======================================================================================================================
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import zipfile

# ---------------------------------------------------------------------------------------------------------------------
#  Console: UTF-8 everywhere (part descriptions carry micro / ohm signs; Windows consoles default to cp1252)
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------------------------------------------------
#  Determinism: string hashes must be fixed before the interpreter starts, so a run without PYTHONHASHSEED=0 relaunches
#  itself once with it set and returns the child's exit code (HIL_NO_REEXEC=1 disables this for interactive use).
# ---------------------------------------------------------------------------------------------------------------------
if os.environ.get("PYTHONHASHSEED") != "0" and os.environ.get("HIL_NO_REEXEC") != "1" and sys.argv and sys.argv[0]:
    _env = dict(os.environ, PYTHONHASHSEED="0")
    sys.exit(subprocess.run([sys.executable] + sys.argv, env=_env).returncode)

# ---------------------------------------------------------------------------------------------------------------------
#  Helper modules sit beside this file; kicad_env MUST be imported (and isolate() run) before pcbnew
# ---------------------------------------------------------------------------------------------------------------------
TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(TOOLS, "..", ".."))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import kicad_env           # noqa: E402
kicad_env.isolate()
import distributor_verify  # noqa: E402

try:
    import pcbnew          # noqa: E402  (KiCad python only)
except ImportError:        # the data model and schematic writer still work, the board steps refuse to run
    pcbnew = None

# =====================================================================================================================
#  Constants
# =====================================================================================================================
SCHEMA = "hil-board-results/1"
GRID = 2.54                                         # schematic grid (mm); every pin end and label sits on it
KIID_SEED = 20261103                                # seed for KiCad's object UUIDs (any constant works; never change it
                                                    # casually: it would rewrite every board file once)
HANG_S = 600                                        # a child process whose log has not grown for this long is hung
ELECTRICAL_TYPES = {"input", "output", "bidirectional", "tri_state", "passive", "free", "unspecified",
                    "power_in", "power_out", "open_collector", "open_emitter", "no_connect"}

# ---------------------------------------------------------------------------------------------------------------------
#  Default design rules: conservative values at or above JLCPCB's published standard-process minimums for 1-2 layer
#  and 4-6 layer FR-4 (JLCPCB "PCB Manufacturing & Assembly Capabilities" page, jlcpcb.com/capabilities). They are
#  written into the board as DRC minimums, so the fab's capability is enforced by KiCad's own DRC. Re-check the page
#  before relaxing any of them, and cite it (or DESIGN_STANDARDS.md) in the commit that does.
# ---------------------------------------------------------------------------------------------------------------------
JLC_RULES = {
    "track": 0.15,          # min track width (published minimum 0.127 mm / 5 mil for 1 oz outer copper)
    "clearance": 0.15,      # min copper clearance (published minimum 0.127 mm)
    "via": 0.6,             # via pad diameter used by the router
    "drill": 0.3,           # via drill used by the router (published minimum 0.2 mm on standard process)
    "min_via": 0.5,         # DRC minimum via diameter
    "min_drill": 0.3,       # DRC minimum through-hole drill
    "min_annular": 0.13,    # DRC minimum annular ring (published minimum 0.13 mm)
    "hole_to_hole": 0.5,    # min hole-to-hole (published 0.5 mm for vias in different nets)
    "hole_clearance": 0.25, # hole to copper
    "edge": 0.3,            # copper to board edge (published 0.3 mm for routed outlines)
    "default_track": 0.25,  # default net class track width
    "default_clearance": 0.2,
}


class PipelineError(SystemExit):
    """A step failed: the message names the step and what to fix. SystemExit so a board script exits non-zero."""


def _f(v: float) -> str:
    """Compact, stable number formatting for S-expressions (no -0, no exponent)."""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _esc(s: str) -> str:
    """Escape a string for a KiCad S-expression literal."""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _sha256(path: str) -> str:
    """SHA-256 of a file, streamed (fab zips can be large)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


_STAMP_RX = re.compile(r"(CreationDate|\bdate\s+\d{4}-\d{2}-\d{2}|^;\s*DRILL file .* date|^;\s*#@! TF\.CreationDate)", re.I)


def _content_sha256(path: str) -> str:
    """SHA-256 of a text fab file with its export time-stamp lines left out (see fab_export)."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for line in fh:
            if not _STAMP_RX.search(line.decode("utf-8", errors="replace")):
                h.update(line)
    return h.hexdigest()


def _snap(v: float) -> float:
    """Round to the schematic grid."""
    return round(round(v / GRID) * GRID, 4)


def _java() -> str | None:
    """The java executable: HIL_JAVA, then JAVA_HOME/bin/java, then PATH."""
    env = os.environ.get("HIL_JAVA", "").strip()
    if env and os.path.isfile(env):
        return env
    jh = os.environ.get("JAVA_HOME", "").strip()
    if jh:
        cand = os.path.join(jh, "bin", "java.exe" if os.name == "nt" else "java")
        if os.path.isfile(cand):
            return cand
    return shutil.which("java")


def run_watched(cmd: list[str], log_path: str, *, cwd: str | None = None, timeout_s: float = 3600.0,
                below_normal: bool = True, env: dict | None = None) -> int:
    """
    Run a child process with its output streamed into `log_path`, at BelowNormal priority on Windows, with a hard
    timeout and a hang detector (no new output for HANG_S seconds). Only the child this function started is ever
    killed, by its own handle. Returns the exit code (-9 when killed for a timeout / hang).
    """
    os.makedirs(os.path.dirname(os.path.abspath(log_path)) or ".", exist_ok=True)
    flags = 0
    if os.name == "nt" and below_normal:
        flags = 0x00004000                          # BELOW_NORMAL_PRIORITY_CLASS: the machine stays responsive
    last_growth = [time.monotonic()]
    with open(log_path, "w", encoding="utf-8", errors="replace", newline="\n") as log:
        log.write("### " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
                                creationflags=flags, text=True, encoding="utf-8", errors="replace")

        # ---- reader thread: every line lands in the log the moment the child prints it
        def _pump():
            assert proc.stdout is not None
            for line in proc.stdout:
                log.write(line)
                log.flush()
                last_growth[0] = time.monotonic()
        t = threading.Thread(target=_pump, daemon=True)
        t.start()
        t0 = time.monotonic()
        killed = ""
        while proc.poll() is None:
            time.sleep(1.0)
            now = time.monotonic()
            if now - t0 > timeout_s:
                killed = f"timeout after {timeout_s:.0f} s"
            elif now - last_growth[0] > HANG_S:
                killed = f"no output for {HANG_S} s (hung)"
            if killed:
                proc.kill()                          # our own child only, by its handle
                break
        rc = proc.wait()
        t.join(timeout=5)
        log.write(f"\n### exit {rc}{' (' + killed + ')' if killed else ''}\n")
    return -9 if killed else rc


# =====================================================================================================================
#  The board model
# =====================================================================================================================
class Board:
    """One board: declared data plus the pipeline steps. See the module docstring for the step list."""

    def __init__(self, name: str, title: str, rev: str, out_dir: str, *, layers: int = 2, thickness: float = 1.6,
                 rules: dict | None = None, router_passes: int = 40, router_threads: int = 1,
                 router_timeout_s: int = 1800, company: str = ""):
        # ---- defensive argument checks: a garbled name would end up in file names and lib ids
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_\-]{0,40}", name or ""):
            raise PipelineError(f"board name {name!r}: letters, digits, '_' and '-' only, starting with a letter")
        if layers not in (2, 4, 6, 8):
            raise PipelineError(f"{name}: layers must be 2, 4, 6 or 8 (got {layers})")
        if not (0.4 <= thickness <= 3.2):
            raise PipelineError(f"{name}: board thickness {thickness} mm is outside 0.4..3.2")
        if not (1 <= router_threads <= 4):
            raise PipelineError(f"{name}: router_threads must be 1..4 (the machine is shared)")
        self.name, self.title, self.rev, self.company = name, title, rev, company
        self.out = os.path.abspath(out_dir)
        self.lib = f"{name}_lib"                     # the board's own footprint + symbol library nickname
        self.kdir = os.path.join(self.out, "kicad")
        self.pretty = os.path.join(self.out, f"{self.lib}.pretty")
        self.fab = os.path.join(self.out, "fab")
        self.layers, self.thickness = layers, thickness
        self.rules = dict(JLC_RULES)
        self.rules.update(rules or {})
        self.router_passes, self.router_threads, self.router_timeout_s = router_passes, router_threads, router_timeout_s
        # ---- declared data
        self.parts: list[dict] = []
        self.custom_fps: dict[str, dict] = {}
        self.outline_pts: list[tuple[float, float]] = []
        self.holes: list[dict] = []
        self.placement: dict[str, tuple[float, float, float, str]] = {}
        self.netclasses: list[dict] = []
        self.pours: list[dict] = []
        self.stitches: list[tuple[str, float, float, float, float]] = []
        # ---- results collected by the steps
        self.res: dict = {"schema": SCHEMA, "board": name, "rev": rev, "layers": layers, "steps": {}}
        self.B = None
        self._fps: dict = {}
        self._nets: dict = {}
        self.net_limit: dict[str, dict] = {}
        self.root_uuid = self._uuid("root")
        self.sym_uuid: dict[str, str] = {}

    # -----------------------------------------------------------------------------------------------------------------
    #  Declaration API (what a board script calls)
    # -----------------------------------------------------------------------------------------------------------------
    def part(self, ref: str, value: str, mpn: str, footprint: str, pins: list[tuple[str, str, str]], nets: dict, *,
             code: str = "", distributor: str = "LCSC", height_mm: float = 1.0, note: str = "", in_bom: bool = True,
             abs_max_v: dict | None = None, tvs: dict | None = None, series: bool = False) -> str:
        """
        Declare one part.
          footprint  "Lib:Name" (a stock KiCad footprint, localized into the board library) or "@Name" (a custom
                     footprint declared with custom_footprint()).
          pins       datasheet pin table: [(number, name, electrical type)], electrical type from ELECTRICAL_TYPES.
          nets       {pin number or pin name: net name}; a name maps every pin of that name (e.g. "GND" on a QFN).
                     Pins left out get a no-connect flag.
          code       distributor order code (LCSC "C..." for JLCPCB assembly); empty = not bought through it.
          abs_max_v  {pin number or name: absolute-maximum voltage to GND from the datasheet}; required for every
                     power_in pin on a net a TVS protects (rule TVS-02), useful on every part that has one
          tvs        for a TVS / ESD diode: {"vrwm": stand-off V, "vbr_min": breakdown min V, "vc_max": clamp V at
                     its rated pulse current}, all from the datasheet table
          series     True for a part that passes a fast transient through (PTC, ferrite, 0 ohm link, fuse): the
                     nets on both sides share the TVS clamp as their worst case
        """
        if not re.fullmatch(r"[A-Z]+[0-9]+", ref or ""):
            raise PipelineError(f"{self.name}: reference {ref!r} must look like R1 / U12 / J3")
        if any(p["ref"] == ref for p in self.parts):
            raise PipelineError(f"{self.name}: duplicate reference {ref}")
        if not footprint or (":" not in footprint and not footprint.startswith("@")):
            raise PipelineError(f"{self.name}: {ref} footprint {footprint!r} must be 'Lib:Name' or '@Name'")
        if not pins:
            raise PipelineError(f"{self.name}: {ref} has no pin table")
        seen = set()
        for row in pins:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                raise PipelineError(f"{self.name}: {ref} pin row {row!r} must be (number, name, type)")
            num, pname, etype = (str(x) for x in row)
            if etype not in ELECTRICAL_TYPES:
                raise PipelineError(f"{self.name}: {ref} pin {num} type {etype!r} not in {sorted(ELECTRICAL_TYPES)}")
            if num in seen:
                raise PipelineError(f"{self.name}: {ref} pin number {num} appears twice")
            seen.add(num)
        # ---- resolve the pin -> net map: keys may be pin numbers or pin names
        by_name: dict[str, list[str]] = {}
        for num, pname, _ in pins:
            by_name.setdefault(str(pname), []).append(str(num))
        conn: dict[str, str] = {}
        for key, net in (nets or {}).items():
            if not net or not re.fullmatch(r"[A-Za-z0-9_+\-.]+", str(net)):
                raise PipelineError(f"{self.name}: {ref} net name {net!r} (letters, digits, _ + - . only)")
            targets = [str(key)] if str(key) in seen else by_name.get(str(key), [])
            if not targets:
                raise PipelineError(f"{self.name}: {ref} has no pin numbered or named {key!r}")
            for t in targets:
                conn[t] = str(net)
        self.parts.append({"ref": ref, "value": str(value), "mpn": str(mpn), "fp": footprint,
                           "pins": [(str(a), str(b), str(c)) for a, b, c in pins], "conn": conn, "code": str(code or ""),
                           "distributor": distributor if code else "", "height_mm": float(height_mm), "note": note,
                           "in_bom": bool(in_bom), "abs_max_v": self._ratings(ref, pins, abs_max_v),
                           "tvs": self._tvs_params(ref, tvs), "series": bool(series)})
        return ref

    def _ratings(self, ref: str, pins: list, abs_max_v: dict | None) -> dict:
        """{pin number: volts} from a {number or name: volts} map; unknown keys and non-positive values refused."""
        out: dict[str, float] = {}
        for key, v in (abs_max_v or {}).items():
            nums = [str(n) for n, nm, _ in pins if str(n) == str(key) or str(nm) == str(key)]
            if not nums or not isinstance(v, (int, float)) or not v > 0:
                raise PipelineError(f"{self.name}: {ref} abs_max_v {key!r}: {v!r} (pin unknown or rating not > 0)")
            for n in nums:
                out[n] = float(v)
        return out

    def _tvs_params(self, ref: str, tvs: dict | None) -> dict | None:
        """Validate the TVS datasheet numbers: vrwm < vbr_min < vc_max, all positive."""
        if tvs is None:
            return None
        try:
            vrwm, vbr, vc = float(tvs["vrwm"]), float(tvs["vbr_min"]), float(tvs["vc_max"])
        except (KeyError, TypeError, ValueError):
            raise PipelineError(f"{self.name}: {ref} tvs needs numbers vrwm, vbr_min, vc_max") from None
        if not (0 < vrwm < vbr < vc):
            raise PipelineError(f"{self.name}: {ref} tvs numbers must satisfy 0 < vrwm < vbr_min < vc_max")
        return {"vrwm": vrwm, "vbr_min": vbr, "vc_max": vc}

    def net_limits(self, net: str, *, operating_max: float | None = None, worst: float | None = None,
                   basis: str = "") -> None:
        """
        Declare what a net sees: operating_max = the highest normal voltage (a TVS on it must stand off at least
        that); worst = the transient worst case on a net no TVS sits on directly (a divider output, a node behind
        a regulator), with the reasoning in basis. Both feed rule TVS-02.
        """
        if operating_max is None and worst is None:
            raise PipelineError(f"{self.name}: net_limits({net!r}) needs operating_max or worst")
        if worst is not None and not basis:
            raise PipelineError(f"{self.name}: net_limits({net!r}, worst=...) needs the basis of the number")
        self.net_limit[net] = {"operating_max": operating_max, "worst": worst, "basis": basis}

    def custom_footprint(self, name: str, pads: list[dict], *, descr: str, courtyard: tuple[float, float],
                         body: tuple[float, float] | None = None, attr: str = "smd") -> str:
        """
        Declare a custom footprint from a vendor land-pattern table. Each pad dict:
          {"num": "1", "kind": "smd"|"thru_hole"|"np_thru_hole", "shape": "rect"|"roundrect"|"circle"|"oval",
           "at": (x, y), "size": (w, h), "drill": d (holes only)}
        courtyard = (width, height) of the courtyard rectangle centred on the origin; body = fab outline box.
        Returns the footprint id "@<name>" to pass to part().
        Rule: draw the WHOLE land pattern of the real package, never a reduced-pin stand-in.
        """
        if not re.fullmatch(r"[A-Za-z0-9_\-.]+", name or ""):
            raise PipelineError(f"{self.name}: custom footprint name {name!r}")
        if not pads:
            raise PipelineError(f"{self.name}: custom footprint {name} has no pads")
        for p in pads:
            if p.get("kind") not in ("smd", "thru_hole", "np_thru_hole") or p.get("shape") not in ("rect", "roundrect", "circle", "oval"):
                raise PipelineError(f"{self.name}: {name} pad {p!r}: bad kind / shape")
            if p["kind"] != "smd" and not p.get("drill"):
                raise PipelineError(f"{self.name}: {name} pad {p.get('num')!r} is a hole without a drill")
        self.custom_fps[name] = {"pads": pads, "descr": descr, "courtyard": courtyard, "body": body or courtyard, "attr": attr}
        return "@" + name

    def outline(self, pts: list[tuple[float, float]]) -> None:
        """Board outline polygon (mm, KiCad frame: x right, y DOWN), at least 3 points, closed automatically."""
        if not pts or len(pts) < 3:
            raise PipelineError(f"{self.name}: outline needs >= 3 points")
        self.outline_pts = [(float(x), float(y)) for x, y in pts]

    def hole(self, x: float, y: float, drill: float, label: str) -> None:
        """Non-plated mounting hole (board-only footprint, not in the schematic, BOM or CPL)."""
        if not (0.5 <= drill <= 8.0):
            raise PipelineError(f"{self.name}: hole {label} drill {drill} mm outside 0.5..8")
        self.holes.append({"x": float(x), "y": float(y), "drill": float(drill), "label": str(label)})

    def place(self, ref: str, x: float, y: float, rot: float = 0.0, side: str = "top") -> None:
        """Hand placement (the agent's reasoned coordinates). side = 'top' or 'bottom'."""
        if side not in ("top", "bottom"):
            raise PipelineError(f"{self.name}: {ref} side {side!r}")
        self.placement[ref] = (float(x), float(y), float(rot), side)

    def netclass(self, name: str, *, track: float, clearance: float, via: float, drill: float, nets: list[str]) -> None:
        """A net class (power nets wider, for example); values must not undercut the rules dict minimums."""
        if track < self.rules["track"] or clearance < self.rules["clearance"]:
            raise PipelineError(f"{self.name}: net class {name} undercuts the fab minimums {self.rules['track']} / {self.rules['clearance']}")
        self.netclasses.append({"name": name, "track": track, "clearance": clearance, "via": via, "drill": drill, "nets": list(nets)})

    def pour(self, net: str, layers: tuple[str, ...] = ("F.Cu", "B.Cu"), *, inset: float = 0.5, priority: int = 0,
             clearance: float = 0.3, min_width: float = 0.25) -> None:
        """A copper pour of `net` over the whole outline (inset from the edge) on each listed layer."""
        self.pours.append({"net": net, "layers": tuple(layers), "inset": inset, "priority": priority,
                           "clearance": clearance, "min_width": min_width})

    def stitch(self, net: str, points: list[tuple[float, float]], via: float = 0.6, drill: float = 0.3) -> None:
        """Locked stitching vias (return-path continuity between the pour layers); the router routes around them."""
        for x, y in points:
            self.stitches.append((net, float(x), float(y), float(via), float(drill)))

    # -----------------------------------------------------------------------------------------------------------------
    #  Small internal helpers
    # -----------------------------------------------------------------------------------------------------------------
    def _uuid(self, *key) -> str:
        """Deterministic UUID for a schematic item: uuid5 of the board name and an item key."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, "hil-board/" + self.name + "/" + "/".join(str(k) for k in key)))

    def _log(self, msg: str) -> None:
        print(f"[{self.name}] {msg}", flush=True)

    def _cli(self, args: list[str], ok=(0,), log_name: str | None = None) -> subprocess.CompletedProcess:
        """Run kicad-cli; a return code outside `ok` stops the pipeline with the tail of its output."""
        r = subprocess.run([kicad_env.cli()] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if log_name:
            with open(os.path.join(self.kdir, log_name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(r.stdout + "\n" + r.stderr)
        if r.returncode not in ok:
            raise PipelineError(f"{self.name}: kicad-cli {' '.join(args[:3])} rc={r.returncode}: {(r.stderr or r.stdout)[-1200:]}")
        return r

    def _fp_name(self, fp: str) -> str:
        """Footprint name inside the board library for a part's declared footprint."""
        return fp[1:] if fp.startswith("@") else fp.split(":", 1)[1]

    def _need_pcbnew(self) -> None:
        if pcbnew is None:
            raise PipelineError(f"{self.name}: pcbnew not importable; run this under KiCad 10's python")

    # =================================================================================================================
    #  Step 1-2: board library + design checks
    # =================================================================================================================
    def write_library(self) -> dict:
        """Copy every stock footprint into the board library (silk -> fab) and write every custom one."""
        os.makedirs(self.pretty, exist_ok=True)
        written = {}
        for p in self.parts:
            name = self._fp_name(p["fp"])
            if name in written:
                continue
            if p["fp"].startswith("@"):
                if name not in self.custom_fps:
                    raise PipelineError(f"{self.name}: {p['ref']} uses custom footprint {name} that was never declared")
                written[name] = self._write_custom(name, self.custom_fps[name])
            else:
                written[name] = self._localize(*p["fp"].split(":", 1))
        # ---- the mounting-hole footprints (one per drill size) live in the board library too
        for h in self.holes:
            hn = self._hole_fp(h["drill"])
            written[hn] = os.path.join(self.pretty, hn + ".kicad_mod")
        # ---- pad numbers on every footprint, for the pin / pad consistency check
        self._pads: dict[str, set[str]] = {}
        for name, path in written.items():
            with open(path, encoding="utf-8") as fh:
                txt = fh.read()
            self._pads[name] = {m for m in re.findall(r'\(pad\s+"([^"]*)"', txt) if m}
        self.res["steps"]["library"] = {"footprints": len(written), "pretty": os.path.relpath(self.pretty, self.out)}
        return written

    def _localize(self, lib: str, name: str) -> str:
        """Stock footprint -> board library. Silkscreen primitives and texts move to the same-side fab layer."""
        src = os.path.join(kicad_env.footprint_dir(lib), f"{name}.kicad_mod")
        if not os.path.isfile(src):
            raise PipelineError(f"{self.name}: stock footprint {lib}:{name} not found ({src})")
        with open(src, encoding="utf-8") as fh:
            txt = fh.read()
        # ---- text edit, never a pcbnew Remove(): see the module docstring (heap corruption under live wrappers)
        txt = txt.replace('(layer "F.SilkS")', '(layer "F.Fab")').replace('(layer "B.SilkS")', '(layer "B.Fab")')
        dst = os.path.join(self.pretty, f"{name}.kicad_mod")
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
        return dst

    def _write_custom(self, name: str, spec: dict) -> str:
        """Write a custom footprint from its pad table (KiCad 10 footprint format)."""
        cw, ch = spec["courtyard"]
        bw, bh = spec["body"]
        attr = {"smd": "(attr smd)", "through_hole": "(attr through_hole)",
                "board_only": "(attr board_only exclude_from_pos_files exclude_from_bom)"}.get(spec["attr"], "(attr smd)")
        out = [f'(footprint "{_esc(name)}"', '\t(version 20260206)', '\t(generator "board_pipeline")', '\t(layer "F.Cu")',
               f'\t(descr "{_esc(spec["descr"])}")',
               f'\t(property "Reference" "REF**" (at 0 {_f(-ch / 2 - 1)} 0) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
               f'\t(property "Value" "{_esc(name)}" (at 0 {_f(ch / 2 + 1)} 0) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))',
               "\t" + attr,
               f'\t(fp_rect (start {_f(-bw / 2)} {_f(-bh / 2)}) (end {_f(bw / 2)} {_f(bh / 2)}) (stroke (width 0.1) (type solid)) (fill no) (layer "F.Fab"))',
               f'\t(fp_rect (start {_f(-cw / 2)} {_f(-ch / 2)}) (end {_f(cw / 2)} {_f(ch / 2)}) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))']
        for p in spec["pads"]:
            x, y = p["at"]
            w, h = p["size"]
            layers = {"smd": '"F.Cu" "F.Paste" "F.Mask"', "thru_hole": '"*.Cu" "*.Mask"', "np_thru_hole": '"*.Cu" "*.Mask"'}[p["kind"]]
            drill = f' (drill {_f(p["drill"])})' if p["kind"] != "smd" else ""
            rr = " (roundrect_rratio 0.25)" if p["shape"] == "roundrect" else ""
            num = "" if p["kind"] == "np_thru_hole" else str(p.get("num", ""))
            out.append(f'\t(pad "{_esc(num)}" {p["kind"]} {p["shape"]} (at {_f(x)} {_f(y)}) (size {_f(w)} {_f(h)}){drill} (layers {layers}){rr})')
        out.append("\t(embedded_fonts no)")
        out.append(")")
        dst = os.path.join(self.pretty, f"{name}.kicad_mod")
        with open(dst, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(out) + "\n")
        return dst

    def _hole_fp(self, drill: float) -> str:
        """Board-only NPTH mounting hole footprint for one drill size (courtyard = hole + 0.25 mm all round)."""
        name = f"NPTH_{_f(drill).replace('.', 'p')}mm"
        if name not in self.custom_fps:
            self.custom_fps[name] = {"pads": [{"num": "", "kind": "np_thru_hole", "shape": "circle", "at": (0, 0),
                                               "size": (drill, drill), "drill": drill}],
                                     "descr": f"non-plated mounting hole {drill} mm", "courtyard": (drill + 0.5, drill + 0.5),
                                     "body": (drill, drill), "attr": "board_only"}
            self._write_custom(name, self.custom_fps[name])
        return name

    def _check_protection(self, problems: list[str]) -> list[dict]:
        """
        Rule TVS-02, protection coordination. Every net a TVS sits on, plus every net joined to it through a part
        declared series=True (a PTC, a ferrite: a fast transient passes them), has the TVS clamp voltage as its worst
        case; net_limits(worst=...) adds worst cases for derived nets (a divider output). Then:
          - every pin with an abs_max_v on such a net must be rated at or above the worst case;
          - every power_in pin on such a net must declare its abs_max_v (an unrated supply pin is a finding);
          - a TVS whose stand-off is below the net's declared operating_max conducts in normal operation.
        Returns one record per protected net for the results JSON.
        """
        worst: dict[str, tuple[float, str]] = {}
        for p in self.parts:
            t = p.get("tvs")
            if not t:
                continue
            # ---- the clamp spreads through series parts, breadth first
            start = {n for n in p["conn"].values() if n != "GND"}
            seen, todo = set(start), list(start)
            while todo:
                net = todo.pop()
                for q in self.parts:
                    if q.get("series") and net in q["conn"].values():
                        for other in q["conn"].values():
                            if other not in seen and other != "GND":
                                seen.add(other)
                                todo.append(other)
            for net in sorted(seen):
                if net not in worst or t["vc_max"] < worst[net][0]:
                    worst[net] = (t["vc_max"], f"{p['ref']} clamp {t['vc_max']:g} V")
            for net in sorted(start):
                op = (self.net_limit.get(net) or {}).get("operating_max")
                if op is not None and t["vrwm"] < op:
                    problems.append(f"TVS-02: {p['ref']} stand-off {t['vrwm']:g} V is below the operating maximum "
                                    f"{op:g} V of {net}: it conducts in normal operation")
        for net, lim in self.net_limit.items():
            if lim.get("worst") is not None and net not in worst:
                worst[net] = (float(lim["worst"]), lim.get("basis", ""))
        records = []
        for net, (v, why) in sorted(worst.items()):
            rated = []
            for p in self.parts:
                for num, pnet in p["conn"].items():
                    if pnet != net:
                        continue
                    etype = next((e for n, _, e in p["pins"] if n == num), "")
                    amax = p.get("abs_max_v", {}).get(num)
                    if amax is None:
                        if etype == "power_in":
                            problems.append(f"TVS-02: {p['ref']}.{num} ({etype}) on {net} (worst {v:g} V, {why}) "
                                            f"declares no abs_max_v: read it from the datasheet")
                        continue
                    rated.append(f"{p['ref']}.{num} {amax:g} V")
                    if amax < v:
                        problems.append(f"TVS-02: {p['ref']}.{num} is rated {amax:g} V but {net} reaches {v:g} V "
                                        f"({why}): pick a part rated above the clamp, or a lower clamp")
            records.append({"net": net, "worst_v": v, "basis": why, "rated_pins": rated})
        return records

    def check_design(self) -> dict:
        """Pin table vs footprint pads, single-pin nets, placement completeness. Any finding stops the run."""
        problems: list[str] = []
        nets: dict[str, list[str]] = {}
        for p in self.parts:
            pins = {n for n, _, _ in p["pins"]}
            pads = self._pads.get(self._fp_name(p["fp"]), set())
            # ---- the symbol must have exactly the footprint's pad numbers (parity fails otherwise)
            if pins != pads:
                problems.append(f"{p['ref']}: pins {sorted(pins - pads)} have no pad; pads {sorted(pads - pins)} have no pin")
            for num, net in p["conn"].items():
                nets.setdefault(net, []).append(f"{p['ref']}.{num}")
            if p["ref"] not in self.placement:
                problems.append(f"{p['ref']}: no placement (the agent places every part with a reasoned coordinate)")
        for net, members in sorted(nets.items()):
            if len(members) < 2:
                problems.append(f"net {net} has one pin only ({members[0]}): a dangling label")
        if not self.outline_pts:
            problems.append("no outline")
        protection = self._check_protection(problems)
        self.nets = nets
        self.res["steps"]["design"] = {"parts": len(self.parts), "nets": len(nets), "protection": protection,
                                       "problems": problems}
        if problems:
            raise PipelineError(f"{self.name}: design check failed:\n  " + "\n  ".join(problems))
        return self.res["steps"]["design"]

    # =================================================================================================================
    #  Step 3-4: schematic, project files, ERC
    # =================================================================================================================
    def _symbol_body(self, p: dict) -> tuple[str, dict]:
        """Library symbol text (name without lib prefix) and the pin-end offsets {pin: (x, y)} in symbol coordinates."""
        pins = p["pins"]
        n = len(pins)
        left, right = pins[:(n + 1) // 2], pins[(n + 1) // 2:]
        rows = max(len(left), len(right), 1)
        # ---- body: width a multiple of 2 * GRID so pin ends land on the grid; height one grid per pin row + margin
        longest = max([len(nm) for _, nm, _ in pins] + [3])
        half_w = max(2, math.ceil((longest * 0.9 + 2.0) / GRID)) * GRID if n > 2 else GRID
        top = (rows - 1) / 2.0 * GRID
        top = _snap(top) if abs(_snap(top) - top) < 1e-6 else _snap(top + GRID / 2)
        half_h = top + GRID
        ends: dict[str, tuple[float, float]] = {}
        body = []
        name = p["ref"]
        body.append(f'(symbol "{name}" (pin_names (offset 0.508)) (exclude_from_sim no) (in_bom {"yes" if p["in_bom"] else "no"}) (on_board yes)')
        body.append(f' (property "Reference" "{name}" (at 0 {_f(half_h + 1.27)} 0) (effects (font (size 1.27 1.27))))')
        body.append(f' (property "Value" "{_esc(p["value"])}" (at 0 {_f(-half_h - 1.27)} 0) (effects (font (size 1.27 1.27))))')
        body.append(f' (property "Footprint" "{self.lib}:{_esc(self._fp_name(p["fp"]))}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
        body.append(f' (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
        body.append(f' (property "Description" "{_esc(p["mpn"])}" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))')
        body.append(f' (symbol "{name}_0_1" (rectangle (start {_f(-half_w)} {_f(half_h)}) (end {_f(half_w)} {_f(-half_h)})'
                    f' (stroke (width 0.254) (type default)) (fill (type background))))')
        body.append(f' (symbol "{name}_1_1"')
        for side, group in (("L", left), ("R", right)):
            for i, (num, pname, etype) in enumerate(group):
                y = top - i * GRID
                x = -half_w - GRID if side == "L" else half_w + GRID
                ang = 0 if side == "L" else 180
                ends[num] = (x, y)
                body.append(f'  (pin {etype} line (at {_f(x)} {_f(y)} {ang}) (length {_f(GRID)})'
                            f' (name "{_esc(pname)}" (effects (font (size 1.0 1.0)))) (number "{_esc(num)}" (effects (font (size 1.0 1.0)))))')
        body.append(" )")
        body.append(")")
        return "\n".join(body), {"ends": ends, "half_w": half_w, "half_h": half_h}

    def write_schematic(self) -> dict:
        """Label-style schematic + project symbol library + project file + lib tables (all deterministic)."""
        os.makedirs(self.kdir, exist_ok=True)
        lib_syms, items = [], []
        # ---- PWR_FLAG on every net that has power_in pins but no power_out pin (ERC "power pin not driven" otherwise)
        types: dict[str, set[str]] = {}
        for p in self.parts:
            pt = {n: t for n, _, t in p["pins"]}
            for num, net in p["conn"].items():
                types.setdefault(net, set()).add(pt[num])
        flag_nets = sorted(n for n, ts in types.items() if "power_in" in ts and "power_out" not in ts)
        flag_body = ('(symbol "PWR_FLAG" (power) (pin_numbers (hide yes)) (pin_names (offset 0) (hide yes)) (exclude_from_sim no) (in_bom no) (on_board no)\n'
                     ' (property "Reference" "#FLG" (at 0 1.905 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                     ' (property "Value" "PWR_FLAG" (at 0 3.81 0) (effects (font (size 1.27 1.27))))\n'
                     ' (property "Footprint" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                     ' (property "Datasheet" "" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                     ' (property "Description" "power flag" (at 0 0 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                     ' (symbol "PWR_FLAG_0_0" (pin power_out line (at 0 0 90) (length 0) (name "~" (effects (font (size 1.27 1.27)))) (number "1" (effects (font (size 1.27 1.27))))))\n'
                     ' (symbol "PWR_FLAG_0_1" (polyline (pts (xy 0 0) (xy 0 1.27) (xy -1.016 1.905) (xy 0 2.54) (xy 1.016 1.905) (xy 0 1.27))'
                     ' (stroke (width 0) (type default)) (fill (type none))))\n)')
        if flag_nets:
            lib_syms.append(("PWR_FLAG", flag_body))
        # ---- one symbol per part, laid out left to right in rows across the sheet
        x0, y0, sheet_w = 25.4, 30.48, 380.0
        x, y, row_h = x0, y0, 0.0
        for p in self.parts:
            body, geo = self._symbol_body(p)
            lib_syms.append((p["ref"], body))
            span = 2 * (geo["half_w"] + GRID) + 30.0             # room for the labels on both sides
            if x + span > sheet_w:
                x, y, row_h = x0, y + row_h + 15.24, 0.0
            cx, cy = _snap(x + span / 2), _snap(y + geo["half_h"])
            su = self._uuid("sym", p["ref"])
            self.sym_uuid[p["ref"]] = su
            fields = [("Reference", p["ref"], cy - geo["half_h"] - 1.27, False), ("Value", p["value"], cy + geo["half_h"] + 1.27, False),
                      ("Footprint", f"{self.lib}:{self._fp_name(p['fp'])}", cy, True), ("Datasheet", "", cy, True),
                      ("Description", p["mpn"], cy, True), ("MPN", p["mpn"], cy, True)]
            if p["code"]:
                fields.append((p["distributor"] or "LCSC", p["code"], cy, True))
            props = "".join(f' (property "{k}" "{_esc(v)}" (at {_f(cx)} {_f(fy)} 0) (effects (font (size 1.27 1.27)){" (hide yes)" if hide else ""}))\n'
                            for k, v, fy, hide in fields)
            pin_uuids = "".join(f' (pin "{_esc(n)}" (uuid "{self._uuid("pin", p["ref"], n)}"))\n' for n, _, _ in p["pins"])
            items.append(f'(symbol (lib_id "{self.lib}:{p["ref"]}") (at {_f(cx)} {_f(cy)} 0) (unit 1) (exclude_from_sim no)'
                         f' (in_bom {"yes" if p["in_bom"] else "no"}) (on_board yes) (dnp no) (uuid "{su}")\n{props}{pin_uuids}'
                         f' (instances (project "{self.name}" (path "/{self.root_uuid}" (reference "{p["ref"]}") (unit 1)))))\n')
            # ---- a label at every connected pin end, a no-connect flag on every unused pin
            for num, pname, _t in p["pins"]:
                px, py = geo["ends"][num]
                wx, wy = cx + px, cy - py                        # symbol y up -> sheet y down
                if num in p["conn"]:
                    left = px < 0
                    items.append(f'(label "{_esc(p["conn"][num])}" (at {_f(wx)} {_f(wy)} {180 if left else 0}) (fields_autoplaced yes)'
                                 f' (effects (font (size 1.27 1.27)) (justify {"right" if left else "left"} bottom)) (uuid "{self._uuid("label", p["ref"], num)}"))\n')
                else:
                    items.append(f'(no_connect (at {_f(wx)} {_f(wy)}) (uuid "{self._uuid("nc", p["ref"], num)}"))\n')
            x += span
            row_h = max(row_h, 2 * geo["half_h"] + 12.7)
        # ---- power flags in a row under the parts
        fy = _snap(y + row_h + 15.24)
        for i, net in enumerate(flag_nets):
            fx = _snap(x0 + i * 30.48)
            items.append(f'(symbol (lib_id "{self.lib}:PWR_FLAG") (at {_f(fx)} {_f(fy)} 0) (unit 1) (exclude_from_sim no) (in_bom no) (on_board no) (dnp no)'
                         f' (uuid "{self._uuid("flag", net)}")\n'
                         f' (property "Reference" "#FLG{i + 1:02d}" (at {_f(fx)} {_f(fy - 2.54)} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                         f' (property "Value" "PWR_FLAG" (at {_f(fx)} {_f(fy - 5.08)} 0) (effects (font (size 1.27 1.27))))\n'
                         f' (property "Footprint" "" (at {_f(fx)} {_f(fy)} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                         f' (property "Datasheet" "" (at {_f(fx)} {_f(fy)} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                         f' (property "Description" "power flag" (at {_f(fx)} {_f(fy)} 0) (effects (font (size 1.27 1.27)) (hide yes)))\n'
                         f' (pin "1" (uuid "{self._uuid("flagpin", net)}"))\n'
                         f' (instances (project "{self.name}" (path "/{self.root_uuid}" (reference "#FLG{i + 1:02d}") (unit 1)))))\n')
            items.append(f'(label "{_esc(net)}" (at {_f(fx)} {_f(fy)} 0) (fields_autoplaced yes) (effects (font (size 1.27 1.27)) (justify left bottom))'
                         f' (uuid "{self._uuid("flaglabel", net)}"))\n')
        sheet_h = max(297.0, fy + 40.0)
        lib_block = "".join(body.replace(f'(symbol "{nm}"', f'(symbol "{self.lib}:{nm}"', 1) + "\n" for nm, body in lib_syms)
        sch = (f'(kicad_sch (version 20250114) (generator "eeschema") (generator_version "9.0") (uuid "{self.root_uuid}") (paper "User" 420 {_f(sheet_h)})\n'
               f' (title_block (title "{_esc(self.title)}") (rev "{_esc(self.rev)}") (company "{_esc(self.company)}")'
               f' (comment 1 "generated by pcb/tools/board_pipeline.py from the board script; edit the script, not this file"))\n'
               f'(lib_symbols\n{lib_block})\n{"".join(items)}(sheet_instances (path "/" (page "1")))\n(embedded_fonts no)\n)\n')
        sch_path = os.path.join(self.kdir, f"{self.name}.kicad_sch")
        with open(sch_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(sch)
        # ---- the project symbol library holds exactly the symbols the schematic caches (ERC compares them)
        with open(os.path.join(self.kdir, f"{self.name}.kicad_sym"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write('(kicad_symbol_lib (version 20241209) (generator "board_pipeline")\n' + "".join(b + "\n" for _, b in lib_syms) + ")\n")
        with open(os.path.join(self.kdir, "sym-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f'(sym_lib_table (version 7)\n (lib (name "{self.lib}")(type "KiCad")(uri "${{KIPRJMOD}}/{self.name}.kicad_sym")(options "")(descr "{self.name} symbols")))\n')
        # ---- project fp-lib-table: ONLY the board's own library (every footprint was copied there in step 2)
        with open(os.path.join(self.kdir, "fp-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f'(fp_lib_table (version 7)\n (lib (name "{self.lib}")(type "KiCad")(uri "${{KIPRJMOD}}/../{self.lib}.pretty")(options "")(descr "{self.name} footprints")))\n')
        # ---- project file: keep any sections an earlier run's pcbnew save added (design rules live there)
        pro_path = os.path.join(self.kdir, f"{self.name}.kicad_pro")
        pro: dict = {}
        if os.path.isfile(pro_path):
            try:
                with open(pro_path, encoding="utf-8") as fh:
                    pro = json.load(fh)
            except (OSError, ValueError):
                pro = {}
            if not isinstance(pro, dict):
                pro = {}
        pro.update({"meta": {"filename": f"{self.name}.kicad_pro", "version": 3}, "sheets": [[self.root_uuid, "Root"]]})
        pro.setdefault("erc", {"erc_exclusions": []})
        with open(pro_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(pro, fh, indent=2)
            fh.write("\n")
        # ---- *.kicad_prl is per-user state: make sure an old one never lingers next to the generated files
        prl = os.path.join(self.kdir, f"{self.name}.kicad_prl")
        if os.path.isfile(prl):
            os.remove(prl)
        info = {"symbols": len(self.parts), "power_flags": len(flag_nets), "nets": len(types)}
        self.res["steps"]["schematic"] = info
        return info

    def run_erc(self) -> dict:
        """kicad-cli ERC, all severities, JSON + human report; any error or warning stops the run."""
        sch = os.path.join(self.kdir, f"{self.name}.kicad_sch")
        j = os.path.join(self.kdir, f"{self.name}_erc.json")
        self._cli(["sch", "erc", "--format", "json", "--severity-all", "--units", "mm", "-o", j, sch], ok=(0, 5))
        self._cli(["sch", "erc", "--format", "report", "--severity-all", "-o", os.path.join(self.kdir, f"{self.name}_erc.rpt"), sch], ok=(0, 5))
        self._cli(["sch", "export", "pdf", "-o", os.path.join(self.kdir, f"{self.name}_schematic.pdf"), sch], ok=(0,))
        with open(j, encoding="utf-8") as fh:
            data = json.load(fh)
        viol = [v for s in data.get("sheets", []) for v in s.get("violations", [])]
        erc = {"errors": sum(1 for v in viol if v.get("severity") == "error"),
               "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
               "detail": [(v.get("type"), v.get("severity"), v.get("description")) for v in viol][:40]}
        self.res["steps"]["erc"] = erc
        self._log(f"ERC errors {erc['errors']} warnings {erc['warnings']}")
        if erc["errors"] or erc["warnings"]:
            raise PipelineError(f"{self.name}: ERC not clean ({erc['errors']} errors, {erc['warnings']} warnings): {erc['detail'][:6]}")
        return erc

    # =================================================================================================================
    #  Step 5: the board
    # =================================================================================================================
    def net(self, name: str):
        """NETINFO for a schematic net (local labels in the root sheet are named '/<label>' by KiCad)."""
        key = name if name.startswith("unconnected-(") else "/" + name
        if key not in self._nets:
            ni = pcbnew.NETINFO_ITEM(self.B, key)
            self.B.Add(ni)
            self._nets[key] = ni
        return self._nets[key]

    def build_board(self) -> None:
        """Create the board file from the declared data (no routing yet)."""
        self._need_pcbnew()
        pcbnew.KIID.SeedGenerator(KIID_SEED)                 # every object created from here on gets a repeatable KIID
        mm = pcbnew.FromMM
        V = lambda x, y: pcbnew.VECTOR2I(mm(x), mm(y))      # noqa: E731
        pcb_path = os.path.join(self.kdir, f"{self.name}.kicad_pcb")
        # ---- start from an empty board file so LoadBoard attaches the project (design rules are saved into .kicad_pro)
        with open(pcb_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('(kicad_pcb (version 20241229) (generator "pcbnew") (generator_version "9.0"))\n')
        B = pcbnew.LoadBoard(pcb_path)
        if B is None:
            raise PipelineError(f"{self.name}: pcbnew could not open the empty board {pcb_path}")
        self.B = B
        B.SetCopperLayerCount(self.layers)
        ds = B.GetDesignSettings()
        ds.SetBoardThickness(mm(self.thickness))
        r = self.rules
        # ---- fab capability as DRC minimums: KiCad's own DRC enforces the manufacturer's limits
        ds.m_TrackMinWidth = mm(r["track"]); ds.m_MinClearance = mm(r["clearance"])
        ds.m_ViasMinSize = mm(r["min_via"]); ds.m_MinThroughDrill = mm(r["min_drill"])
        ds.m_ViasMinAnnularWidth = mm(r["min_annular"]); ds.m_HoleToHoleMin = mm(r["hole_to_hole"])
        ds.m_HoleClearance = mm(r["hole_clearance"]); ds.m_CopperEdgeClearance = mm(r["edge"])
        nc = ds.m_NetSettings.GetDefaultNetclass()
        nc.SetClearance(mm(r["default_clearance"])); nc.SetTrackWidth(mm(r["default_track"]))
        nc.SetViaDiameter(mm(r["via"])); nc.SetViaDrill(mm(r["drill"]))
        for c in self.netclasses:
            ncl = pcbnew.NETCLASS(c["name"])
            ncl.SetTrackWidth(mm(c["track"])); ncl.SetClearance(mm(c["clearance"]))
            ncl.SetViaDiameter(mm(c["via"])); ncl.SetViaDrill(mm(c["drill"]))
            ds.m_NetSettings.SetNetclass(c["name"], ncl)
            for n in c["nets"]:
                ds.m_NetSettings.SetNetclassPatternAssignment("/" + n, c["name"])
        # ---- outline on Edge.Cuts
        pts = self.outline_pts
        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
            s = pcbnew.PCB_SHAPE(B); s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Edge_Cuts)
            s.SetWidth(mm(0.05)); s.SetStart(V(x0, y0)); s.SetEnd(V(x1, y1)); B.Add(s)
        # ---- footprints from the board's own library, nets from the declared pin map
        for p in self.parts:
            name = self._fp_name(p["fp"])
            fp = pcbnew.FootprintLoad(self.pretty, name)
            if fp is None or not hasattr(fp, "Pads"):
                raise PipelineError(f"{self.name}: footprint {name} not loadable from {self.pretty}")
            fp.SetReference(p["ref"]); fp.SetValue(p["value"])
            fp.SetFPID(pcbnew.LIB_ID(self.lib, name))
            # the symbol link: root-sheet symbols have the path "/<symbol uuid>", which is what parity matches on
            fp.SetPath(pcbnew.KIID_PATH(f"/{self.sym_uuid[p['ref']]}"))
            # every symbol field is mirrored on the footprint (parity compares them)
            for k, v in (("MPN", p["mpn"]), (p["distributor"] or "LCSC", p["code"]), ("Description", p["mpn"])):
                if v:
                    fp.SetField(k, v)
                    fp.GetField(k).SetVisible(False)
            B.Add(fp)
            x, y, rot, side = self.placement[p["ref"]]
            fp.SetPosition(V(x, y))
            if side == "bottom":
                fp.SetLayerAndFlip(pcbnew.B_Cu)
            fp.SetOrientationDegrees(rot)
            pinname = {n: nm for n, nm, _ in p["pins"]}
            for pad in fp.Pads():
                num = pad.GetNumber()
                if not num:
                    continue
                if num in p["conn"]:
                    pad.SetNet(self.net(p["conn"][num]))
                else:
                    # eeschema names a pin with a no-connect flag "unconnected-(<ref>-<pin name>-Pad<n>)"; parity expects it
                    pad.SetNet(self.net(f"unconnected-({p['ref']}-{pinname.get(num, num)}-Pad{num})"))
            self._fps[p["ref"]] = fp
        # ---- mounting holes: board-only footprints (no symbol, not in BOM or CPL)
        for h in self.holes:
            name = self._hole_fp(h["drill"])
            fp = pcbnew.FootprintLoad(self.pretty, name)
            if fp is None:
                raise PipelineError(f"{self.name}: hole footprint {name} not loadable")
            fp.SetReference(h["label"]); fp.SetValue(f"NPTH {h['drill']} mm")
            fp.SetFPID(pcbnew.LIB_ID(self.lib, name))
            fp.SetBoardOnly(True); fp.SetExcludedFromBOM(True); fp.SetExcludedFromPosFiles(True)
            B.Add(fp); fp.SetPosition(V(h["x"], h["y"]))
        # ---- locked stitching vias (the router keeps them; the pours connect them after the fill)
        for net, x, y, vd, dr in self.stitches:
            v = pcbnew.PCB_VIA(B); v.SetPosition(V(x, y))
            v.SetWidth(pcbnew.F_Cu, mm(vd))                 # layer argument: the layer-less overload can assert headless
            v.SetDrill(mm(dr)); v.SetNet(self.net(net)); v.SetLocked(True); B.Add(v)
        self.save(fill=False)
        self.res["steps"]["board"] = {"footprints": len(self.parts), "holes": len(self.holes), "stitching_vias": len(self.stitches)}

    def save(self, fill: bool = True) -> str:
        """Fill zones (optional) and save board + project settings."""
        pcb_path = os.path.join(self.kdir, f"{self.name}.kicad_pcb")
        if fill and self.B.Zones():
            pcbnew.ZONE_FILLER(self.B).Fill(self.B.Zones())
        if not pcbnew.SaveBoard(pcb_path, self.B):
            raise PipelineError(f"{self.name}: SaveBoard failed for {pcb_path}")
        prl = os.path.join(self.kdir, f"{self.name}.kicad_prl")
        if os.path.isfile(prl):
            os.remove(prl)                                  # per-user state, never part of the generated set
        return pcb_path

    # =================================================================================================================
    #  Step 6 + 10: DRC (placement and final)
    # =================================================================================================================
    def _drc(self, tag: str) -> dict:
        """kicad-cli DRC with schematic parity at all severities; returns counts and the first details."""
        pcb_path = os.path.join(self.kdir, f"{self.name}.kicad_pcb")
        j = os.path.join(self.kdir, f"{self.name}_drc_{tag}.json")
        self._cli(["pcb", "drc", "--format", "json", "--severity-all", "--schematic-parity", "--units", "mm", "-o", j, pcb_path], ok=(0, 5))
        self._cli(["pcb", "drc", "--format", "report", "--severity-all", "--schematic-parity", "-o",
                   os.path.join(self.kdir, f"{self.name}_drc_{tag}.rpt"), pcb_path], ok=(0, 5))
        with open(j, encoding="utf-8") as fh:
            data = json.load(fh)
        viol = data.get("violations", [])
        types: dict[str, int] = {}
        for v in viol:
            k = f"{v.get('severity')}/{v.get('type')}"
            types[k] = types.get(k, 0) + 1
        return {"errors": sum(1 for v in viol if v.get("severity") == "error"),
                "warnings": sum(1 for v in viol if v.get("severity") == "warning"),
                "unconnected": len(data.get("unconnected_items", [])), "parity": len(data.get("schematic_parity", [])),
                "types": types,
                "detail": [(v.get("type"), v.get("description"), [i.get("description") for i in v.get("items", [])][:2]) for v in viol][:30],
                "parity_detail": [(v.get("type"), v.get("description")) for v in data.get("schematic_parity", [])][:20]}

    def placement_drc(self) -> dict:
        """DRC on the placed, unrouted board: every violation and parity item must be 0 (unconnected is expected)."""
        d = self._drc("placement")
        # ---- the locked stitching vias connect only once the pours are filled (step 9): before routing they are
        #      "via_dangling" by definition. Exactly those items are set aside here, never any other type or count.
        dangling = d["types"].get("warning/via_dangling", 0)
        if dangling and dangling <= len(self.stitches):
            d["warnings"] -= dangling
            d["types"].pop("warning/via_dangling")
            d["expected_before_pours"] = {"via_dangling": dangling}
        self.res["steps"]["placement_drc"] = d
        self._log(f"placement DRC errors {d['errors']} warnings {d['warnings']} parity {d['parity']} (unconnected {d['unconnected']} before routing)")
        if d["errors"] or d["warnings"] or d["parity"]:
            raise PipelineError(f"{self.name}: placement DRC not clean: {d['types']} parity {d['parity_detail'][:5]} {d['detail'][:5]}")
        return d

    # =================================================================================================================
    #  Step 7-8: routing and remnant clean-up
    # =================================================================================================================
    def route(self, reroute: bool = False) -> dict:
        """
        Freerouting 2.1 on the Specctra export, SES import. Stops with a clear message when the router is missing.
        Frozen route: a successful run copies the SES to <out>/<board>_route.ses. When that file exists (and `reroute`
        is not asked for) it is imported instead of running the router. Freerouting 2.1 is not bit-reproducible even
        with one thread (two runs of the example board gave two different, equally valid track sets), so committing the
        frozen SES with the generator is what makes the routed board and the fab files repeatable. A placement change
        that invalidates the frozen route shows up as unconnected items in the final DRC: rerun with `reroute`.
        """
        frozen = os.path.join(self.out, f"{self.name}_route.ses")
        if os.path.isfile(frozen) and not reroute:
            if not pcbnew.ImportSpecctraSES(self.B, frozen):
                raise PipelineError(f"{self.name}: frozen route {frozen} did not import; rerun with `reroute`")
            info = {"router": "frozen SES (no router run)", "ses": os.path.basename(frozen), "ses_sha256": _sha256(frozen)}
            self.res["steps"]["route"] = info
            return info
        jar = os.environ.get("HIL_FREEROUTING_JAR", "").strip()
        java = _java()
        if not jar or not os.path.isfile(jar):
            raise PipelineError(f"{self.name}: router not installed: set HIL_FREEROUTING_JAR to the Freerouting 2.1 jar "
                                f"(see docs/TOOLS.md); the pipeline stops before fab because the board is unrouted")
        if not java:
            raise PipelineError(f"{self.name}: java 21 not found (set HIL_JAVA or JAVA_HOME)")
        home = os.path.join(self.out, ".router")
        os.makedirs(os.path.join(home, "freerouting"), exist_ok=True)
        # ---- seeded settings: no GUI, no telemetry, no analytics, capped optimizer threads (2.1.0 defaults differ)
        cfg = {"gui": {"enabled": False}, "profile": {"allow_telemetry": False, "allow_contact": False},
               "usage_and_diagnostic_data": {"disable_analytics": True},
               "router": {"max_passes": self.router_passes, "max_threads": self.router_threads,
                          "optimizer": {"max_threads": self.router_threads}}}
        with open(os.path.join(home, "freerouting", "freerouting.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cfg, fh, indent=2)
        dsn, ses = os.path.join(home, f"{self.name}.dsn"), os.path.join(home, f"{self.name}.ses")
        if os.path.isfile(ses):
            os.remove(ses)
        if not pcbnew.ExportSpecctraDSN(self.B, dsn):
            raise PipelineError(f"{self.name}: Specctra DSN export failed")
        # ---- the DSN's first line carries the absolute board path: keep only the base name (no machine paths in outputs)
        with open(dsn, encoding="utf-8", errors="replace") as fh:
            txt = fh.read()
        txt = re.sub(r'^\(pcb\s+"[^"]*"', f'(pcb "{self.name}.dsn"', txt, count=1)
        with open(dsn, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(txt)
        cmd = [java, f"-Djava.io.tmpdir={home}", f"-Duser.home={home}", "-Xmx2g", "-jar", jar,
               "-de", dsn, "-do", ses, "-mp", str(self.router_passes), "-mt", str(self.router_threads),
               "--gui.enabled=false", "--profile.allow_telemetry=false", "--usage_and_diagnostic_data.disable_analytics=true"]
        t0 = time.monotonic()
        rc = run_watched(cmd, os.path.join(home, "freerouting.log"), cwd=home, timeout_s=self.router_timeout_s)
        if rc != 0 or not os.path.isfile(ses):
            raise PipelineError(f"{self.name}: Freerouting failed rc={rc}; see {os.path.join(home, 'freerouting.log')}")
        if not pcbnew.ImportSpecctraSES(self.B, ses):
            raise PipelineError(f"{self.name}: SES import failed")
        shutil.copyfile(ses, frozen)                        # the frozen route: commit it with the generator
        info = {"router": "freerouting 2.1", "passes_max": self.router_passes, "threads": self.router_threads,
                "seconds": round(time.monotonic() - t0, 1), "ses": os.path.basename(frozen), "ses_sha256": _sha256(frozen)}
        self.res["steps"]["route"] = info
        return info

    def clean_dangling(self, include_vias: bool = False, max_rounds: int = 12) -> int:
        """
        Remove router remnants: unlocked track segments with a dangling end (KiCad's own test), repeated until a round
        removes nothing (removing one stub can expose the one behind it). With include_vias, unlocked vias that reach
        fewer than two copper layers go too (run after the zone fill). This is the LAST pcbnew mutation of the run apart
        from the zone fill: no FootprintLoad follows it, and no wrapper of a removed item is kept (fresh list per round).
        """
        removed = 0
        cu = list(pcbnew.LSET.AllCuMask(self.B.GetCopperLayerCount()).Seq())
        for _ in range(max_rounds):
            self.B.BuildConnectivity()
            conn = self.B.GetConnectivity()
            doomed = [t for t in self.B.GetTracks()
                      if t.Type() == pcbnew.PCB_TRACE_T and not t.IsLocked() and conn.TestTrackEndpointDangling(t, False)]
            if include_vias:
                for t in self.B.GetTracks():
                    if t.Type() == pcbnew.PCB_VIA_T and not t.IsLocked():
                        if sum(1 for lid in cu if conn.IsConnectedOnLayer(t, lid)) < 2:
                            doomed.append(t)
            if not doomed:
                break
            for t in doomed:
                self.B.Remove(t)
            removed += len(doomed)
            doomed = []                                     # drop the wrappers of the freed items right away
        return removed

    # =================================================================================================================
    #  Step 9: pours and fill
    # =================================================================================================================
    def add_pours(self) -> int:
        """Copper pours over the inset outline (thermal relief to pads) on each declared layer."""
        mm = pcbnew.FromMM
        n = 0
        xs = [p[0] for p in self.outline_pts]; ys = [p[1] for p in self.outline_pts]
        for spec in self.pours:
            # ---- inset rectangle of the outline's bounding box; the edge clearance rule trims the rest
            i = spec["inset"]
            poly = [(min(xs) + i, min(ys) + i), (max(xs) - i, min(ys) + i), (max(xs) - i, max(ys) - i), (min(xs) + i, max(ys) - i)]
            for lname in spec["layers"]:
                z = pcbnew.ZONE(self.B)
                z.SetLayer(self.B.GetLayerID(lname))
                z.SetNet(self.net(spec["net"]))
                z.SetAssignedPriority(spec["priority"])
                z.SetLocalClearance(mm(spec["clearance"]))
                z.SetMinThickness(mm(spec["min_width"]))
                z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
                z.SetZoneName(f"{spec['net']}_{lname}")
                o = z.Outline(); o.NewOutline()
                for x, y in poly:
                    o.Append(mm(x), mm(y))
                self.B.Add(z)
                n += 1
        return n

    # =================================================================================================================
    #  Step 11-12: verification, fab, STEP, CAD fit, results
    # =================================================================================================================
    def verify_parts(self, offline_ok: bool = False) -> dict:
        """Live distributor verification of every coded line (grouped by MPN + code)."""
        groups: dict[tuple[str, str], list[str]] = {}
        for p in self.parts:
            if p["in_bom"]:
                groups.setdefault((p["mpn"], p["code"]), []).append(p["ref"])
        lines = [(",".join(refs), mpn, code) for (mpn, code), refs in sorted(groups.items())]
        rep = distributor_verify.verify_lines(lines, offline_ok=offline_ok)
        self.res["steps"]["distributor"] = {k: rep[k] for k in ("verified", "mismatch", "failed", "unverified", "warnings", "pass", "date")}
        self.res["steps"]["distributor"]["lines"] = rep["lines"]
        self._log(f"distributor verified {rep['verified']} mismatch {rep['mismatch']} failed {rep['failed']} unverified {rep['unverified']}"
                  + (f" warnings {rep['warnings']}" if rep["warnings"] else ""))
        return rep

    def fab_export(self) -> dict:
        """Gerbers, drill, CPL, JLC BOM, zip; only called when every gate is clean."""
        pcb_path = os.path.join(self.kdir, f"{self.name}.kicad_pcb")
        os.makedirs(self.fab, exist_ok=True)
        # ---- stale outputs of an earlier revision go first (named by extension, inside the fab folder only)
        for fn in os.listdir(self.fab):
            if re.search(r"\.(gtl|gbl|g\d+|gts|gbs|gto|gbo|gtp|gbp|gm1|gbrjob|drl|zip|csv|step)$", fn, re.I):
                os.remove(os.path.join(self.fab, fn))
        cu = ",".join(["F.Cu"] + [f"In{i}.Cu" for i in range(1, self.layers - 1)] + ["B.Cu"])
        self._cli(["pcb", "export", "gerbers", "-o", self.fab + os.sep, "--layers",
                   f"{cu},F.Mask,B.Mask,F.SilkS,B.SilkS,Edge.Cuts,F.Paste,B.Paste", pcb_path])
        self._cli(["pcb", "export", "drill", "-o", self.fab + os.sep, pcb_path])
        pos = os.path.join(self.fab, f"{self.name}_pos.csv")
        self._cli(["pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", pos, pcb_path])
        # ---- CPL in JLCPCB's column names (per-package rotation offsets are checked in the assembly preview before ordering)
        cpl = os.path.join(self.fab, f"CPL_{self.name}.csv")
        with open(pos, encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        with open(cpl, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
            for r in rows:
                w.writerow([r.get("Ref"), f"{float(r.get('PosX', 0)):.4f}mm", f"{float(r.get('PosY', 0)):.4f}mm",
                            "Top" if r.get("Side", "top") == "top" else "Bottom", f"{float(r.get('Rot', 0)):.1f}"])
        # ---- BOM in JLCPCB's assembly format; lines without a code are listed for hand fitting
        bom = os.path.join(self.fab, f"BOM_{self.name}.csv")
        groups: dict[tuple, list[str]] = {}
        for p in self.parts:
            if p["in_bom"]:
                groups.setdefault((p["value"], self._fp_name(p["fp"]), p["code"], p["mpn"]), []).append(p["ref"])
        with open(bom, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Comment", "Designator", "Footprint", "LCSC", "MPN"])
            for (val, fpn, code, mpn), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
                w.writerow([val, ",".join(refs), fpn, code, mpn])
        zp = os.path.join(self.fab, f"{self.name}_gerbers.zip")
        with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
            for fn in sorted(os.listdir(self.fab)):
                if re.search(r"\.(gtl|gbl|g\d+|gts|gbs|gto|gbo|gtp|gbp|gm1|gbrjob|drl)$", fn, re.I):
                    zi = zipfile.ZipInfo(fn, date_time=(2020, 1, 1, 0, 0, 0))   # fixed time stamp: a repeatable zip
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    with open(os.path.join(self.fab, fn), "rb") as src:
                        zf.writestr(zi, src.read())
        files = {fn: _sha256(os.path.join(self.fab, fn)) for fn in sorted(os.listdir(self.fab))}
        # ---- KiCad stamps the export time into Gerber / drill headers, so the raw SHA-256 changes on every run. The
        #      content hash skips exactly those time-stamp lines: equal content hashes = the same fabricated board.
        self.res["steps"]["fab_content_sha256"] = {fn: _content_sha256(os.path.join(self.fab, fn))
                                                   for fn in files if not fn.endswith((".zip", ".step"))}
        self.res["steps"]["fab"] = {"files": files, "count": len(files)}
        return self.res["steps"]["fab"]

    def step_export(self) -> str:
        """STEP of the assembled board for the mechanical CAD (stock 3D models resolved by KiCad)."""
        pcb_path = os.path.join(self.kdir, f"{self.name}.kicad_pcb")
        step = os.path.join(self.fab, f"{self.name}.step")
        self._cli(["pcb", "export", "step", "--subst-models", "--force", "-o", step, pcb_path], ok=(0,), log_name="step_export.log")
        self.res["steps"]["step"] = {"file": os.path.basename(step), "sha256": _sha256(step), "bytes": os.path.getsize(step)}
        return step

    def cad_fit(self) -> dict:
        """Outline, holes and every part's courtyard box + height (frame: x right, y UP seen from the top, mm)."""
        toMM = pcbnew.ToMM
        bodies = []
        for ref, fp in sorted(self._fps.items()):
            cy = fp.GetCourtyard(pcbnew.B_CrtYd if fp.IsFlipped() else pcbnew.F_CrtYd)
            bb = cy.BBox() if cy.OutlineCount() else fp.GetBoundingBox(False)
            part = next(p for p in self.parts if p["ref"] == ref)
            bodies.append({"ref": ref, "side": "bottom" if fp.IsFlipped() else "top",
                           "x": round(toMM(fp.GetPosition().x), 3), "y_up": round(-toMM(fp.GetPosition().y), 3),
                           "rot_deg": round(fp.GetOrientationDegrees(), 1),
                           "box_mm": [round(toMM(bb.GetX()), 3), round(-toMM(bb.GetBottom()), 3),
                                      round(toMM(bb.GetRight()), 3), round(-toMM(bb.GetY()), 3)],
                           "height_mm": part["height_mm"], "value": part["value"], "mpn": part["mpn"]})
        out = {"board": self.name, "rev": self.rev, "frame": "x right, y up, seen from the top (F.Cu) side, mm",
               "thickness_mm": self.thickness, "copper_layers": self.layers,
               "outline": [[x, -y] for x, y in self.outline_pts],
               "holes": [{"x": h["x"], "y_up": -h["y"], "drill": h["drill"], "label": h["label"]} for h in self.holes],
               "max_height_top_mm": max([b["height_mm"] for b in bodies if b["side"] == "top"] or [0.0]),
               "max_height_bottom_mm": max([b["height_mm"] for b in bodies if b["side"] == "bottom"] or [0.0]),
               "bodies": bodies}
        with open(os.path.join(self.out, f"{self.name}_cad_fit.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(out, fh, indent=1)
        return out

    def write_results(self, verdict: str) -> str:
        """<board>_results.json: every count of every step, the SHA-256 of every fab file, the verdict."""
        self.res["verdict"] = verdict
        self.res["kicad"] = pcbnew.Version() if pcbnew else None
        self.res["generated"] = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        path = os.path.join(self.out, f"{self.name}_results.json")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.res, fh, indent=1, ensure_ascii=False)
        return path

    # =================================================================================================================
    #  The whole pipeline
    # =================================================================================================================
    def run(self, argv: list[str] | None = None) -> int:
        """
        Command line of every board script:
          (none)        full run: steps 1-12
          place         stop after the placement DRC (fast iteration on placement)
          reroute       run the router even when a frozen route (<board>_route.ses) exists
          --offline-ok  an unreachable distributor does not fail the run (fab files are still withheld)
        """
        argv = list(sys.argv[1:] if argv is None else argv)
        place_only = "place" in argv
        offline_ok = "--offline-ok" in argv
        reroute = "reroute" in argv
        self._need_pcbnew()
        t0 = time.monotonic()
        verdict = "red"
        try:
            # ---- seed only the stock libraries this board localizes (fast kicad-cli start-up)
            kicad_env.seed_lib_tables(sorted({p["fp"].split(":", 1)[0] for p in self.parts if not p["fp"].startswith("@")}))
            self.write_library()
            self.check_design()
            self.write_schematic()
            self.run_erc()
            self.build_board()
            self.placement_drc()
            if place_only:
                verdict = "placed"
                return 0
            # ---- routing: the only step that needs Freerouting
            self.route(reroute=reroute)
            stubs = self.clean_dangling()
            n_pours = self.add_pours()
            if n_pours:
                pcbnew.ZONE_FILLER(self.B).Fill(self.B.Zones())
                stubs += self.clean_dangling(include_vias=True)
            self.res["steps"]["cleanup"] = {"removed_remnants": stubs, "pours": n_pours}
            self.save(fill=True)
            d = self._drc("final")
            total = max(1, sum(1 for net, m in self.nets.items() if len(m) >= 2))
            d["routed_pct"] = round(100.0 * (1 - min(d["unconnected"], total) / total), 1)
            self.res["steps"]["final_drc"] = d
            self._log(f"final DRC errors {d['errors']} warnings {d['warnings']} unconnected {d['unconnected']} parity {d['parity']}")
            drc_clean = not (d["errors"] or d["warnings"] or d["unconnected"] or d["parity"])
            # ---- live distributor check, then the fab set only if everything is clean
            rep = self.verify_parts(offline_ok=offline_ok)
            all_verified = rep["pass"] and rep["unverified"] == 0
            self.cad_fit()
            if not drc_clean:
                raise PipelineError(f"{self.name}: final DRC not clean: {d['types']} unconnected {d['unconnected']} parity {d['parity_detail'][:5]}")
            if not all_verified:
                raise PipelineError(f"{self.name}: distributor verification did not pass (mismatch {rep['mismatch']}, failed {rep['failed']}, "
                                    f"unverified {rep['unverified']}): no fab files written")
            self.fab_export()
            self.step_export()
            verdict = "green"
            return 0
        except PipelineError as e:
            self._log(f"STOP: {e}")
            self.res["error"] = str(e)
            return 1
        finally:
            # ---- kicad-cli writes a per-user .kicad_prl next to the project on every call: never leave one behind
            prl = os.path.join(self.kdir, f"{self.name}.kicad_prl")
            if os.path.isfile(prl):
                os.remove(prl)
            self.res["seconds"] = round(time.monotonic() - t0, 1)
            if self.B is not None or self.res["steps"]:
                path = self.write_results(verdict)
                self._log(f"verdict {verdict}; results {path}")
