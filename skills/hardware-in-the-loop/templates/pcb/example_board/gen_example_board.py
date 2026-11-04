#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 gen_example_board.py - a small, real board that exercises the whole generator pipeline end to end
=======================================================================================================================
 Interpreter: KiCad 10's bundled python ("C:\\Program Files\\KiCad\\10.0\\bin\\python.exe")
 Run:         "C:\\Program Files\\KiCad\\10.0\\bin\\python.exe" pcb\\example_board\\gen_example_board.py [place|reroute] [--out DIR] [--offline-ok]
              (set HIL_FREEROUTING_JAR to the Freerouting 2.1 jar for the full run; `place` needs no router)

 The circuit: a 2-layer 3.3 V LDO breakout, 34 x 26 mm, four M2 holes.
     J1 (JST PH 2-pin, 5 V in) -> D1 TVS across the input -> F1 resettable PTC -> VIN_PROT
     VIN_PROT -> C1 1 uF -> U1 TPS70933 (VIN rated 32 V abs max, above the TVS clamp) -> +3V3 -> C2 10 uF
     +3V3 -> R1 1 k -> D2 power LED
     VIN_PROT -> R2 / R3 22 k : 10 k divider -> VSENSE (VIN_PROT x 10 / 32) on the output header, so the host MCU
                 sees a tripped PTC (the rail collapses) instead of a silently dead board (rule PROT-03); VSENSE
                 also drives U1's EN (1.56 V at 5 V, above its 0.9 V threshold; 3.2 V at the 10.3 V clamp, below
                 EN's 7 V absolute maximum and safe for a 3.3 V ADC on the host)
     J2 2 x 3 header: +3V3 on pins 1 and 2, GND on 3, 4 and 6 (two or more contacts per power / ground, RED-01),
                 VSENSE on pin 5
 Why these protection parts: no one-time fuse (PROT-01: a blown fuse is invisible to firmware), a PTC with a sense line
 instead (PROT-03), a TVS on the connector that brings power onto the board (TVS-01), and every part behind the TVS
 rated above its clamp voltage (TVS-02, checked by the pipeline from the ratings declared below). An earlier revision
 used an AP2112 (6.5 V absolute maximum) behind this 10.3 V clamp; TVS-02 now fails that design.
 Pin tables are transcribed from the datasheets named next to each part; distributor codes were resolved live on
 2026-11-03 and are re-verified on every full run (distributor_verify.py), so a wrong code fails the build.

 Outputs (all under --out, default: this folder): kicad/ (schematic, board, project, ERC / DRC reports),
 <board>_lib.pretty/, fab/ (Gerbers, drill, CPL, BOM, zip, STEP), <board>_results.json, <board>_cad_fit.json.
 In a real project those outputs are committed with the generator; in the template repository they are ignored
 (example_board/.gitignore) so a test run cannot leave generated files behind.
 Paths: this script resolves the pipeline from __file__; --out names the output folder.
=======================================================================================================================
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------------------------------------------------
#  Console UTF-8 (part descriptions carry micro / ohm signs) and the shared pipeline next door in pcb/tools
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))

import board_pipeline as bp  # noqa: E402  (re-executes with PYTHONHASHSEED=0, isolates KiCad's home folders)


def _out_dir(argv: list[str]) -> str:
    """--out DIR from the command line, else this script's folder."""
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv) or argv[i + 1].startswith("-"):
            raise SystemExit("gen_example_board: --out needs a folder")
        return os.path.abspath(argv[i + 1])
    return HERE


def build(out: str) -> bp.Board:
    """Declare the board. Every number below is either a datasheet value or a reasoned placement."""
    b = bp.Board("ldo_breakout", "3.3 V LDO breakout (pipeline example)", "A", out, layers=2, thickness=1.6)

    # =================================================================================================================
    #  Parts: (number, name, electrical type) pin tables from the datasheets
    # =================================================================================================================
    # ---- J1: JST PH 2-pin vertical header, B2B-PH-K-S (JST PH datasheet: pin 1 / pin 2, 2.0 mm pitch)
    b.part("J1", "5V IN", "B2B-PH-K-S", "Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
           [("1", "VIN", "passive"), ("2", "GND", "passive")], {"1": "VIN", "2": "GND"},
           code="C131337", height_mm=6.0)
    # ---- D1: SMAJ6.0CA bidirectional TVS, SMA, right at the input connector. Datasheet: VRWM 6.0 V, VBR 6.67 V min,
    #      VC 10.3 V at IPP: everything behind it must be rated above 10.3 V (TVS-02)
    b.part("D1", "SMAJ6.0CA", "SMAJ6.0CA", "Diode_SMD:D_SMA",
           [("1", "A1", "passive"), ("2", "A2", "passive")], {"1": "VIN", "2": "GND"},
           code="C78403", height_mm=2.4, tvs={"vrwm": 6.0, "vbr_min": 6.67, "vc_max": 10.3})
    # ---- F1: resettable PTC, 250 mA hold / 24 V (TECHFUSE nSMD025-24V, 1206); never a one-time fuse (PROT-01).
    #      series=True: a fast transient passes it, so VIN_PROT shares the clamp as its worst case
    b.part("F1", "PTC 250mA", "nSMD025-24V", "Fuse:Fuse_1206_3216Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "VIN", "2": "VIN_PROT"},
           code="C70069", height_mm=1.2, series=True, abs_max_v={"1": 24.0, "2": 24.0})
    # ---- C1: LDO input capacitor, 1 uF 16 V X7R 0603 (TPS709 datasheet: >= 0.1 uF ceramic on IN; 1 uF used);
    #      16 V rating: above the clamp, and 5 V is 31 % of it (DER-01: <= 50 %)
    b.part("C1", "1uF 16V", "CC0603KRX7R7BB105", "Capacitor_SMD:C_0603_1608Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "VIN_PROT", "2": "GND"},
           code="C106248", height_mm=0.9, abs_max_v={"1": 16.0, "2": 16.0})
    # ---- U1: TPS70933DBVR, 3.3 V 150 mA LDO, SOT-23-5. TI TPS709 datasheet SBVS186H: table 5-1 DBV pin functions
    #      (1 IN, 2 GND, 3 EN, 4 NC, 5 OUT); 6.1 absolute maximum IN 32 V, EN 7 V; 6.3 recommended IN 2.7..30 V;
    #      dropout 650 mV max at 50 mA, 1.4 V max at 150 mA (so 150 mA needs VIN >= 4.7 V); COUT >= 2.2 uF ceramic
    b.part("U1", "TPS70933", "TPS70933DBVR", "Package_TO_SOT_SMD:SOT-23-5",
           [("1", "IN", "power_in"), ("2", "GND", "power_in"), ("3", "EN", "input"), ("4", "NC", "no_connect"),
            ("5", "OUT", "power_out")],
           {"IN": "VIN_PROT", "GND": "GND", "EN": "VSENSE", "OUT": "+3V3"},
           code="C89347", height_mm=1.3, abs_max_v={"IN": 32.0, "EN": 7.0})
    # ---- C2: LDO output capacitor, 10 uF 10 V X5R 0603 (TPS709: >= 2.2 uF ceramic on OUT; 10 uF for load steps)
    b.part("C2", "10uF 10V", "CL10A106KP8NNNC", "Capacitor_SMD:C_0603_1608Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "+3V3", "2": "GND"},
           code="C19702", height_mm=0.9)
    # ---- R1 + D2: power LED, 1 k from 3.3 V (about 1.2 mA at a 2.0 V forward drop: visible, not wasteful)
    b.part("R1", "1k", "0603WAF1001T5E", "Resistor_SMD:R_0603_1608Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "+3V3", "2": "LED_A"},
           code="C21190", height_mm=0.5)
    b.part("D2", "LED yellow", "KT-0805Y", "LED_SMD:LED_0805_2012Metric",
           [("1", "K", "passive"), ("2", "A", "passive")], {"K": "GND", "A": "LED_A"},
           code="C2296", height_mm=0.8)
    # ---- R2 / R3: VIN_PROT sense divider, 22 k : 10 k -> VSENSE = VIN_PROT x 10 / 32: 1.56 V at 5 V, 3.22 V at the
    #      10.3 V clamp (declared below with net_limits so TVS-02 checks EN and the header pin against it)
    b.part("R2", "22k", "0603WAF2202T5E", "Resistor_SMD:R_0603_1608Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "VIN_PROT", "2": "VSENSE"},
           code="C31850", height_mm=0.5)
    b.part("R3", "10k", "0603WAF1002T5E", "Resistor_SMD:R_0603_1608Metric",
           [("1", "1", "passive"), ("2", "2", "passive")], {"1": "VSENSE", "2": "GND"},
           code="C25804", height_mm=0.5)
    # ---- J2: 2 x 3 header, 2.54 mm; two +3V3 contacts, three GND contacts (RED-01), VSENSE on pin 5
    b.part("J2", "OUT 2x3", "2.54-2*3P", "Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical",
           [("1", "+3V3_a", "passive"), ("2", "+3V3_b", "passive"), ("3", "GND_a", "passive"), ("4", "GND_b", "passive"),
            ("5", "VSENSE", "passive"), ("6", "GND_c", "passive")],
           {"1": "+3V3", "2": "+3V3", "3": "GND", "4": "GND", "5": "VSENSE", "6": "GND"},
           code="C65114", height_mm=8.5)

    # =================================================================================================================
    #  Net limits for TVS-02: the input rail's normal maximum, and the divider node's transient worst case
    # =================================================================================================================
    b.net_limits("VIN", operating_max=5.5, basis="5 V input +10 %")
    b.net_limits("VSENSE", worst=10.3 * 10.0 / 32.0, basis="R2 / R3 divider (10 / 32) of the D1 10.3 V clamp")

    # =================================================================================================================
    #  Mechanics: 34 x 26 mm outline (KiCad frame, y down), M2 holes 3 mm in from each corner
    # =================================================================================================================
    b.outline([(0, 0), (34, 0), (34, 26), (0, 26)])
    for i, (x, y) in enumerate(((3, 3), (31, 3), (3, 23), (31, 23)), start=1):
        b.hole(x, y, 2.2, f"H{i}")

    # =================================================================================================================
    #  Hand placement: power flows left to right along y = 13 so the high-current path is short and straight
    # =================================================================================================================
    b.place("J1", 6.0, 13.0, 0)       # input on the left edge, pads on the flow line
    b.place("D1", 8.0, 19.5, 0)       # TVS right below the connector: shortest loop from the pins to the clamp
    b.place("F1", 13.5, 13.0, 0)      # PTC directly after the connector, in series on the flow line
    b.place("C1", 17.0, 16.5, 90)     # input cap next to U1's VIN / GND pins (left column of the SOT-23-5)
    b.place("U1", 19.5, 13.0, 0)      # regulator in the middle; VIN / GND / EN on the left, VOUT on the right
    b.place("C2", 22.5, 16.5, 90)     # output cap next to VOUT, short return to GND
    b.place("J2", 27.0, 10.5, 0)      # output header on the right edge, 2.54 mm grid, pin 1 top left
    b.place("R1", 16.0, 21.5, 0)      # LED pair along the bottom edge, away from the flow line
    b.place("D2", 20.0, 21.5, 0)
    b.place("R2", 24.5, 20.0, 0)      # divider stacked near the header's VSENSE pin (pin 5, x 27, y 15.58)
    b.place("R3", 24.5, 22.5, 0)

    # =================================================================================================================
    #  Copper: power net class, GND pour on both layers, stitching vias tying the two GND planes together
    # =================================================================================================================
    b.netclass("POWER", track=0.5, clearance=0.2, via=0.6, drill=0.3, nets=["VIN", "VIN_PROT", "+3V3", "GND"])
    b.pour("GND", ("F.Cu", "B.Cu"))
    b.stitch("GND", [(12.0, 5.5), (22.0, 5.5), (12.0, 24.0), (29.0, 19.0)])
    return b


if __name__ == "__main__":
    args = sys.argv[1:]
    board = build(_out_dir(args))
    sys.exit(board.run([a for a in args if a in ("place", "reroute", "--offline-ok")]))
