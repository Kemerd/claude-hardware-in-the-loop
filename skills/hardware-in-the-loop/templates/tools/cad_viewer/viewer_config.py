#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 viewer_config.py - the one place the CAD viewer reads the project's own facts from
=======================================================================================================================
 Interpreter: any Python >= 3.9 (stdlib only). Imported by pack_mesh.py and render_boards.py; running it by hand
              checks a config file and prints what it resolved to.
 Run: python tools/cad_viewer/viewer_config.py [--config tools/cad_viewer/viewer.config.json]
 Paths: the default config sits beside this file (resolved from __file__); --config and --root override.

 Why a config file
   The packer and the board renderer must not know a single part name. Everything that differs between projects
   (which CAD part is which KiCad board, which parts to leave out of the pack, how a material tag maps to a look,
   which parts are conformal-coat shells) is data, and it lives in viewer.config.json next to this file. A project
   with no config still packs: parts get a generic look, no boards are textured, nothing is skipped.

 Keys of viewer.config.json (keys that start with "_" are comments and are ignored):
   title             text shown in the page title                                   (default "CAD viewer")
   skip_parts        [regex, ...]  parts whose name matches are left out of the pack (and of its mass / CG)
   look              [{"class": c, "name": rx, "material": rx, "text": rx}, ...]  project rules for the part look,
                     tried in order BEFORE the built-in rules; a rule matches when every regex it names matches
                     (case-insensitive search); "text" is searched in "<name> <material>". Classes: coat, fr4,
                     copper, alu, ti, steel, print, cf, soft, film, glass, wire, plain.
   look_defaults     false = use only the project's `look` rules                      (default true)
   coat_prefix       conformal-coat shells are parts named <prefix><board part>      (default "coat_"; "" = none)
   board_name_regex  parts that count as boards for PCB X-ray even without a KiCad file
   wire_name_regex   parts that count as wires, cables, flex tails and harness ties for Wire X-ray
   wire_not_regex    parts that match wire_name_regex but are not wires (screws, inserts, clamps ...)
   boards            {"<CAD part name>": "<repo-relative .kicad_pcb>"}  parts that get a KiCad render laid on them
   board_no_file     [{"name": rx, "reason": text}]  board-like parts known to have no KiCad file (listed, not textured)
   board_frames      {"<part>": {"u": [x,y,z], "n": [x,y,z], "source": text}}  the builder's own rule for how the
                     board picture sits on the part (image-right axis and top-face normal, model frame); it wins a
                     tie between equally good silhouette fits
   board_gate        {"size_abs_mm", "size_rel", "min_iou", "tie"}  the gates a render must pass to be laid on a part
 Everything is validated here, so a typo in a regex fails with the key name instead of inside the browser.
=======================================================================================================================
"""
import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "viewer.config.json")

# the vocabulary of looks; index.html maps each name to a PBR recipe
CLASSES = ("coat", "fr4", "copper", "alu", "ti", "steel", "print", "cf", "soft", "film", "glass", "wire", "plain")

# built-in rules, used after the project's own; generic material words only, no project part names
DEFAULT_LOOK = [
    {"class": "fr4", "material": r"fr-?4|pcb|laminate"},
    {"class": "copper", "text": r"copper|\bcu\b|brass|bronze|busbar"},
    {"class": "alu", "text": r"alumin|6061|7075|anodi"},
    {"class": "ti", "text": r"titan|\bti\b|ti-?6al"},
    {"class": "steel", "text": r"steel|stainless|\biron\b|\b304\b|\b316\b|screw|bolt|washer|bearing"},
    {"class": "glass", "text": r"glass|\blens\b|mirror|window"},
    {"class": "print", "text": r"\bpla\b|petg|\babs\b|\basa\b|nylon|\bpa-?(6|11|12|66|612)|\bpc\b|resin|printed"},
    {"class": "cf", "text": r"carbon|\bcf\b|cfrp"},
    {"class": "film", "text": r"kapton|polyimide|mylar|\bffc\b|flex"},
    {"class": "soft", "text": r"tpu|tpe|rubber|silicone|foam|gasket|elastomer|epdm|viton|fkm|o-?ring"},
    {"class": "wire", "text": r"wire|cable|harness|\bawg\b"},
]

DEFAULTS = {
    "title": "CAD viewer",
    "skip_parts": [],
    "look": [],
    "look_defaults": True,
    "coat_prefix": "coat_",
    "board_name_regex": r"pcb|pcba|_board|_fr4",
    "wire_name_regex": r"^harness_|wire|cable|awg|pigtail|coax|_ffc|fpc_tail|flex_tail|(^|_)leads?(_|$)",
    "wire_not_regex": r"screw|insert|clamp|bolt|nut",
    "boards": {},
    "board_no_file": [],
    "board_frames": {},
    "board_gate": {"size_abs_mm": 0.8, "size_rel": 0.025, "min_iou": 0.90, "tie": 0.004},
}


def _bad(msg):
    """Stop with a message that names the config key at fault."""
    raise SystemExit(f"viewer config: {msg}")


def _rx(pattern, where):
    """Compile a case-insensitive regex or stop naming `where`."""
    if not isinstance(pattern, str) or not pattern:
        _bad(f"{where}: a regex must be a non-empty string")
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        _bad(f"{where}: bad regex {pattern!r}: {e}")


def _vec3(v, where):
    """Three finite numbers or stop."""
    if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) and x == x for x in v)):
        _bad(f"{where}: expected [x, y, z]")
    return [float(x) for x in v]


def _rules(rows, where):
    """Validate a list of look rules; returns [(class, {field: compiled regex})]."""
    out = []
    if not isinstance(rows, list):
        _bad(f"{where}: expected a list")
    for i, r in enumerate(rows):
        w = f"{where}[{i}]"
        if not isinstance(r, dict) or r.get("class") not in CLASSES:
            _bad(f"{w}: needs a 'class' from {', '.join(CLASSES)}")
        fields = {k: _rx(r[k], f"{w}.{k}") for k in ("name", "material", "text") if k in r}
        if not fields:
            _bad(f"{w}: name at least one of name / material / text")
        out.append((r["class"], fields))
    return out


def load(path=None):
    """
    The resolved config as a dict: DEFAULTS overlaid with the file's keys, regexes compiled under '_rules',
    '_skip', '_board_rx', '_no_file'. A missing default file is fine (defaults apply); a missing explicit path is not.
    """
    cfg = json.loads(json.dumps(DEFAULTS))                       # deep copy, plain JSON types
    src = path or DEFAULT_CONFIG
    if os.path.isfile(src):
        try:
            with open(src, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError) as e:
            _bad(f"{src} unreadable: {e}")
        if not isinstance(raw, dict):
            _bad(f"{src}: top level must be an object")
        for k, v in raw.items():
            if k.startswith("_"):
                continue                                          # comment keys
            if k not in DEFAULTS:
                _bad(f"{src}: unknown key {k!r} (known: {', '.join(DEFAULTS)})")
            cfg[k] = v
    elif path:
        _bad(f"{path} not found")
    cfg["_source"] = src if os.path.isfile(src) else None

    if not isinstance(cfg["title"], str) or not cfg["title"].strip():
        _bad("title: expected text")
    if not isinstance(cfg["coat_prefix"], str):
        _bad("coat_prefix: expected text")
    cfg["_skip"] = [_rx(p, f"skip_parts[{i}]") for i, p in enumerate(cfg["skip_parts"])] if isinstance(cfg["skip_parts"], list) else _bad("skip_parts: expected a list")
    cfg["_rules"] = _rules(cfg["look"], "look") + (_rules(DEFAULT_LOOK, "DEFAULT_LOOK") if cfg["look_defaults"] else [])
    cfg["_board_rx"] = _rx(cfg["board_name_regex"], "board_name_regex")
    cfg["_wire_rx"] = _rx(cfg["wire_name_regex"], "wire_name_regex")
    cfg["_wire_not_rx"] = _rx(cfg["wire_not_regex"], "wire_not_regex")
    if not isinstance(cfg["boards"], dict) or not all(isinstance(k, str) and isinstance(v, str) and v.lower().endswith(".kicad_pcb") for k, v in cfg["boards"].items()):
        _bad("boards: expected {\"<part>\": \"<path>.kicad_pcb\"}")
    cfg["_no_file"] = []
    if not isinstance(cfg["board_no_file"], list):
        _bad("board_no_file: expected a list")
    for i, r in enumerate(cfg["board_no_file"]):
        if not isinstance(r, dict) or not isinstance(r.get("reason"), str):
            _bad(f"board_no_file[{i}]: needs 'name' and 'reason'")
        cfg["_no_file"].append((_rx(r.get("name"), f"board_no_file[{i}].name"), r["reason"]))
    if not isinstance(cfg["board_frames"], dict):
        _bad("board_frames: expected an object")
    for part, f in cfg["board_frames"].items():
        if not isinstance(f, dict):
            _bad(f"board_frames.{part}: expected {{u, n, source}}")
        f["u"], f["n"] = _vec3(f.get("u"), f"board_frames.{part}.u"), _vec3(f.get("n"), f"board_frames.{part}.n")
        f["source"] = str(f.get("source") or "config")
    gate = dict(DEFAULTS["board_gate"])
    if isinstance(cfg["board_gate"], dict):
        gate.update(cfg["board_gate"])
    for k, v in gate.items():
        if k not in DEFAULTS["board_gate"] or isinstance(v, bool) or not isinstance(v, (int, float)) or not v >= 0:
            _bad(f"board_gate.{k}: expected a non-negative number from {list(DEFAULTS['board_gate'])}")
    cfg["board_gate"] = gate
    return cfg


def part_class(cfg, name, material):
    """The look class of one part: coat shell first, then the project's rules, then the built-ins, else 'plain'."""
    pre = cfg["coat_prefix"]
    if pre and name.startswith(pre):
        return "coat"
    for cls, fields in cfg["_rules"]:
        vals = {"name": name, "material": material or "", "text": f"{name} {material or ''}"}
        if all(rx.search(vals[k]) for k, rx in fields.items()):
            return cls
    return "plain"


def is_board_part(cfg, name):
    """True for a part that PCB X-ray keeps solid: named in `boards`, or matching board_name_regex (never a coat shell)."""
    pre = cfg["coat_prefix"]
    if pre and name.startswith(pre):
        return False
    return name in cfg["boards"] or bool(cfg["_board_rx"].search(name))


def is_wire_part(cfg, name):
    """True for a part that Wire X-ray keeps solid: matches wire_name_regex and not wire_not_regex."""
    return bool(cfg["_wire_rx"].search(name)) and not cfg["_wire_not_rx"].search(name)


def skipped(cfg, name):
    """True when the part is excluded from the pack by skip_parts."""
    return any(rx.search(name) for rx in cfg["_skip"])


def project_root(arg=None):
    """The project root that board paths are relative to: --root, else two folders above this file (tools/cad_viewer)."""
    return os.path.abspath(arg) if arg else os.path.abspath(os.path.join(HERE, "..", ".."))


def pcb_key(rel):
    """File key of a repo-relative board path: '<folders without pcb/kicad>__<stem>' (unique over a pcb tree)."""
    p = rel.replace("\\", "/").split("/")
    parent = [d for d in p[:-1] if d not in ("pcb", "kicad", ".", "..")]
    return ("_".join(parent) + "__" + os.path.splitext(p[-1])[0]).replace(" ", "_")


def main():
    """Check a config file and print what it resolves to."""
    ap = argparse.ArgumentParser(description="Validate a viewer.config.json")
    ap.add_argument("--config", help="config file (default: viewer.config.json beside this script)")
    a = ap.parse_args()
    cfg = load(a.config)
    print(f"viewer config: {cfg['_source'] or '(none, defaults)'}")
    print(f"  title {cfg['title']!r}, {len(cfg['skip_parts'])} skip rules, {len(cfg['look'])} project look rules"
          f"{' + built-ins' if cfg['look_defaults'] else ' only'}, coat prefix {cfg['coat_prefix']!r}")
    print(f"  {len(cfg['boards'])} board files mapped, {len(cfg['board_no_file'])} no-file notes, {len(cfg['board_frames'])} builder frames, gate {cfg['board_gate']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
