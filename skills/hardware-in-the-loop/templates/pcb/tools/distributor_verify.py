#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 distributor_verify.py - LIVE check that every purchased BOM line's distributor code is the part the schematic names
=======================================================================================================================
 Interpreter: any Python >= 3.9 (stdlib only), including KiCad 10's bundled python.

 Why this exists
   An assembly house places parts by ITS order code (LCSC "C..." codes for JLCPCB assembly). A hand-typed code whose
   description "looks right" assembles the wrong part: a different value, a different package, a sibling in the same
   family. So every purchased line is resolved live, at build time, and the manufacturer part number (MPN) the
   distributor sells under that code must equal the MPN in the schematic. Nothing is cached across builds.

 Source
   LCSC product detail (public JSON over HTTPS, no key):
       https://wmsc.lcsc.com/ftps/wm/product/detail?productCode=<C...>
   Fields used: result.productModel (MPN), brandNameEn, encapStandard (package), stockNumber, productNameEn.
   Verified working 2026-11-03 (e.g. C51118 -> AP2112K-3.3TRG1, DIODES, SOT-25-5). The keyword search endpoint did not
   return JSON to a plain client on the same day, so this tool only resolves codes, it never searches.

 Rules (deliberately strict)
   * MATCH    normalise(distributor MPN) == normalise(schematic MPN), or the distributor MPN is the schematic MPN plus a
              parenthesised packaging suffix only (JST style "(LF)(SN)"). normalise = upper case, whitespace removed.
   * MISMATCH anything else -> the line FAILS and the build must stop (fix the code or the MPN, never the rule).
   * STOCK 0  a WARNING, never a failure (stock moves daily; the order step decides).
   * OFFLINE  the distributor could not be reached -> the line is "unverified" and the run FAILS, unless --offline-ok
              (library: offline_ok=True) is given; then the run passes with status "unverified" and the board pipeline
              still refuses to write fab files (it requires every line "verified").
   * Requests are spaced (POLITE_GAP_S), time out (TIMEOUT_S) and retry with back-off (RETRIES).

 Usage
   python pcb/tools/distributor_verify.py <bom.csv> [--mpn-col MPN] [--code-col LCSC] [--refs-col Designator]
                                                    [--out verify.json] [--offline-ok]
   python pcb/tools/distributor_verify.py --code C51118 --mpn AP2112K-3.3TRG1
   Library: verify_lines([(refs, mpn, code), ...]) -> report dict (see verify_lines()).
   Exit 0 = every coded line verified (or unverified with --offline-ok), 1 = a mismatch / failure, 2 = bad arguments.
 Paths: command line (the BOM and --out are named on the command line; nothing else is read or written)
=======================================================================================================================
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------------------------------------------------
#  Windows console safety: part descriptions carry Greek letters (ohm, micro) and CJK brand names
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------------------------------------------------
#  Network behaviour: one request at a time, spaced, bounded, retried
# ---------------------------------------------------------------------------------------------------------------------
LCSC_DETAIL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}"
USER_AGENT = "Mozilla/5.0 (compatible; hil-distributor-verify/1.0)"
TIMEOUT_S = 20.0          # per request
RETRIES = 3               # attempts per code
POLITE_GAP_S = 0.6        # minimum spacing between two requests (never hammer a public endpoint)
CODE_RX = re.compile(r"^C\d{1,12}$")
_last_request = [0.0]


def normalise(text: str) -> str:
    """Upper case with all whitespace removed: the only transformation applied before comparing part numbers."""
    return re.sub(r"\s+", "", str(text or "")).upper()


def mpn_matches(schematic_mpn: str, distributor_mpn: str) -> bool:
    """True when the distributor MPN IS the schematic MPN, optionally followed only by parenthesised packaging codes."""
    a, b = normalise(schematic_mpn), normalise(distributor_mpn)
    if not a or not b:
        return False
    if a == b:
        return True
    # ---- JST-style packaging suffix: "B2B-PH-K-S" vs "B2B-PH-K-S(LF)(SN)"; nothing but (...) groups may follow
    return b.startswith(a) and re.fullmatch(r"(\([A-Z0-9.\-]+\))+", b[len(a):]) is not None


def _fetch_json(url: str) -> dict:
    """GET a URL and parse JSON, with spacing, timeout and retries. Raises OSError after the last failed attempt."""
    err: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        # ---- polite spacing between consecutive requests
        wait = POLITE_GAP_S - (time.monotonic() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8", errors="replace"))
            if not isinstance(data, dict):
                raise ValueError("response is not a JSON object")
            return data
        except (urllib.error.URLError, OSError, ValueError) as e:
            err = e
            time.sleep(min(8.0, 1.5 * attempt))        # back-off before the next attempt
    raise OSError(f"{url}: {err}")


def lookup(code: str) -> dict:
    """
    Resolve one LCSC code. Returns {"ok": True, "mpn", "brand", "package", "stock", "name"} when the distributor knows
    the code, {"ok": False, "reason": "unknown code"} when it answered but has no such part, and raises OSError when
    the distributor could not be reached (the caller turns that into "unverified").
    """
    code = str(code or "").strip().upper()
    if not CODE_RX.match(code):
        return {"ok": False, "reason": f"malformed code {code!r}"}
    data = _fetch_json(LCSC_DETAIL.format(code=code))
    res = data.get("result")
    if not isinstance(res, dict) or not res.get("productModel"):
        return {"ok": False, "reason": "unknown code (distributor returned no product)"}
    stock = res.get("stockNumber")
    try:
        stock = int(stock) if stock is not None else None
    except (TypeError, ValueError):
        stock = None
    return {"ok": True, "mpn": str(res.get("productModel")), "brand": str(res.get("brandNameEn") or ""),
            "package": str(res.get("encapStandard") or ""), "stock": stock, "name": str(res.get("productNameEn") or "")}


def verify_lines(lines, offline_ok: bool = False) -> dict:
    """
    Verify a list of (refs, mpn, code) tuples. Lines with an empty code are reported as "not_purchased_here" (hand
    fitted or bought elsewhere) and do not fail. Returns a report:
      {"date", "source", "lines": [...], "verified", "mismatch", "failed", "unverified", "warnings", "pass"}
    where pass is True only when nothing mismatched / failed and (offline_ok or nothing is unverified).
    """
    rep = {"date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "source": "LCSC product detail",
           "lines": [], "verified": 0, "mismatch": 0, "failed": 0, "unverified": 0, "warnings": [], "pass": False}
    for row in lines or ():
        # ---- defensive unpacking: a garbled row is a failure, never a crash
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            rep["lines"].append({"refs": "?", "status": "failed", "reason": f"bad row {row!r}"})
            rep["failed"] += 1
            continue
        refs, mpn, code = (str(x or "").strip() for x in row)
        entry = {"refs": refs, "mpn": mpn, "code": code}
        if not code:
            entry["status"] = "not_purchased_here"
            rep["lines"].append(entry)
            continue
        if not mpn:
            entry.update(status="failed", reason="no MPN in the schematic for a coded line")
            rep["failed"] += 1
            rep["lines"].append(entry)
            continue
        try:
            got = lookup(code)
        except OSError as e:
            entry.update(status="unverified", reason=f"distributor unreachable: {e}")
            rep["unverified"] += 1
            rep["lines"].append(entry)
            continue
        if not got.get("ok"):
            entry.update(status="failed", reason=got.get("reason", "lookup failed"))
            rep["failed"] += 1
        elif mpn_matches(mpn, got["mpn"]):
            entry.update(status="verified", distributor_mpn=got["mpn"], brand=got["brand"], package=got["package"],
                         stock=got["stock"])
            rep["verified"] += 1
            if got["stock"] == 0:
                rep["warnings"].append(f"{refs}: {code} {mpn} has stock 0 at the distributor")
        else:
            entry.update(status="mismatch", distributor_mpn=got["mpn"], brand=got["brand"], package=got["package"],
                         reason=f"code {code} is {got['mpn']!r}, the schematic names {mpn!r}")
            rep["mismatch"] += 1
        rep["lines"].append(entry)
    rep["pass"] = rep["mismatch"] == 0 and rep["failed"] == 0 and (offline_ok or rep["unverified"] == 0)
    rep["offline_ok"] = bool(offline_ok)
    return rep


def _read_bom(path: str, mpn_col: str, code_col: str, refs_col: str) -> list[tuple[str, str, str]]:
    """Read (refs, mpn, code) rows from a CSV BOM; missing columns are an argument error."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rd = csv.DictReader(fh)
        cols = rd.fieldnames or []
        for c in (mpn_col, code_col, refs_col):
            if c not in cols:
                raise SystemExit(f"distributor_verify: column {c!r} not in {path} (columns: {cols})")
        return [(r.get(refs_col, ""), r.get(mpn_col, ""), r.get(code_col, "")) for r in rd]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Live distributor-code to MPN verification (see module docstring).")
    ap.add_argument("bom", nargs="?", help="CSV BOM with MPN, distributor code and designator columns")
    ap.add_argument("--mpn-col", default="MPN")
    ap.add_argument("--code-col", default="LCSC")
    ap.add_argument("--refs-col", default="Designator")
    ap.add_argument("--code", help="verify one code (with --mpn) instead of a BOM")
    ap.add_argument("--mpn", help="the MPN the single --code must resolve to")
    ap.add_argument("--out", help="write the JSON report here")
    ap.add_argument("--offline-ok", action="store_true", help="an unreachable distributor does not fail the run")
    a = ap.parse_args(argv)
    # ---- argument sanity: exactly one input mode
    if a.code:
        lines = [("cli", a.mpn or "", a.code)]
    elif a.bom:
        if not os.path.isfile(a.bom):
            print(f"distributor_verify: no such BOM {a.bom}", file=sys.stderr)
            return 2
        lines = _read_bom(a.bom, a.mpn_col, a.code_col, a.refs_col)
    else:
        ap.print_usage(sys.stderr)
        return 2
    rep = verify_lines(lines, offline_ok=a.offline_ok)
    # ---- human-readable lines, then the totals
    for e in rep["lines"]:
        extra = e.get("distributor_mpn") or e.get("reason") or ""
        stock = f" stock {e['stock']}" if e.get("stock") is not None else ""
        print(f"{e.get('status', '?'):<19} {e.get('code', ''):<10} {e.get('mpn', ''):<28} {extra}{stock}  [{e.get('refs', '')}]")
    for w in rep["warnings"]:
        print(f"WARNING {w}")
    print(f"[distributor_verify] verified {rep['verified']}  mismatch {rep['mismatch']}  failed {rep['failed']}  "
          f"unverified {rep['unverified']}  -> {'PASS' if rep['pass'] else 'FAIL'}")
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rep, fh, indent=1, ensure_ascii=False)
    return 0 if rep["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
