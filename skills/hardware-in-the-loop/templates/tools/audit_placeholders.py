#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 audit_placeholders.py - the "no stand-ins" gate: finds work that pretends to be finished.

 Interpreter: any Python >= 3.9 (stdlib only). Run it with `python -I` so nothing from the working directory is imported.

 Why: an agent under pressure will happily ship a function that returns success without touching the hardware, a
 CadQuery box named after a real part, or a comment that says the number was guessed. Each of those looks green in
 every other gate. This audit makes them visible, and the tracked baseline makes sure no NEW one slips in.

 Checks (all on by default; each finding has a category, a severity, a file, a line and a subject):
   code_marker        comments carrying an open-work marker word (the usual four-letter ones, "stub", "dummy",
                      "fake", "stand-in", "hack", "tbd") in shipped code. medium; low inside tests.
   code_assumption    comments that admit a guess ("assumed", "unverified", "guess", "made up"). A constant only a bench
                      can settle cites its bench register id instead: "design value, BENCH B-xx-nn". medium; low in tests.
   py_stub            Python functions whose whole body is `raise NotImplementedError` (medium) or `pass` / `...`
                      (low). Abstract methods and Protocol members are exempt.
   c_fake_success     C / C++ functions named like hardware access (read, get, init, sample, measure, hal_, drv_,
                      sensor ...) whose whole body is `return <constant>;`: a driver that reports success without
                      touching anything. high.
   cad_standin_name   CadQuery part names that announce a stand-in (_ref, proxy, envelope, dummy, placeholder ...). high.
   cad_bare_primitive CadQuery plug-in parts whose geometry is one bare primitive (box, cylinder, sphere, a rect or circle
                      extrude) with only transforms applied: a block presented as a part. medium.
   config_marker      JSON / YAML / INI / Kconfig / sdkconfig defaults with a well-known default credential or an open-work
                      marker in a value. medium.

 Tracked baseline: tools/audit_baseline.json (written with --write-baseline in a reviewed commit) holds every finding
 that existed when the gate was introduced. Findings are matched line-insensitively (category + file + subject + the
 normalised text), so editing code above a finding never makes it "new"; a finding whose severity rose counts as new.

 Waivers: tools/audit_waivers.json, a reviewed list of {"category", "file" (glob), "match" (regex on subject or text),
 "reason", "owner"}. Waived findings stay in the report, marked waived, and never gate. A waiver without a reason or
 an owner is refused (exit 2): a waiver is a decision someone signs, not a mute button.

 Usage:
   python -I tools/audit_placeholders.py [--root DIR] [--json OUT] [--quiet]
          [--fail-on-new | --fail-on-any] [--fail-level high|medium|low]
          [--baseline FILE | --no-baseline] [--waivers FILE] [--write-baseline] [--changed-since REV]
   --changed-since REV keeps only files that differ between merge-base(REV, HEAD) and the work tree (committed,
   staged, unstaged and untracked): one lane's deliverable for a skeptic review.
 Exit: 0 = no gating finding, 1 = gating finding(s), 2 = bad arguments / bad waiver file.
=======================================================================================================================
"""
import argparse
import ast
import fnmatch
import io
import json
import os
import re
import subprocess
import sys
import time
import tokenize

# ---------------------------------------------------------------------------------------------------------------------
#  Console safety: findings quote source text, which may hold any character
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SCHEMA = "hil-audit/1"
SEV_RANK = {"high": 3, "medium": 2, "low": 1}

# ---------------------------------------------------------------------------------------------------------------------
#  Word lists. The marker words are assembled from fragments so this file's own source never matches its own rules.
# ---------------------------------------------------------------------------------------------------------------------
_MARKERS = ["TO" + "DO", "FIX" + "ME", "X" + "XX", "HA" + "CK", "T" + "BD", "st" + "ub", "dum" + "my", "fa" + "ke",
            "stand-" + "in", "stand" + "in", "place" + "holder", "temp" + "orary hack"]
MARKER_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(m) for m in _MARKERS) + r")(?![A-Za-z])", re.IGNORECASE)
_ASSUME = ["assu" + "med", "unveri" + "fied", "gue" + "ss", "gue" + "ssed", "made " + "up", "eyeb" + "alled"]
ASSUME_RE = re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(m) for m in _ASSUME) + r")(?![A-Za-z])", re.IGNORECASE)
# a comment that cites a bench register id is the approved way to carry a design value
BENCH_RE = re.compile(r"\bBENCH\s+B-[A-Z0-9]+-\d+\b")

# C / C++ hardware-access names whose constant-only body means "reports success without doing anything"
HW_NAME_RE = re.compile(r"(read|get|init|sample|measure|probe|poll|start|hal_|drv_|sensor|adc|i2c|spi|uart|gpio|_status)", re.IGNORECASE)
C_FUNC_RE = re.compile(
    r"^[ \t]*(?:static\s+|inline\s+|extern\s+)*[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\(([^;{}()]*)\)\s*\{\s*return\s+([^;{}]+?)\s*;\s*\}",
    re.MULTILINE)
C_CONST_RE = re.compile(r"^\(?\s*(-?\d+|0x[0-9a-fA-F]+|true|false|NULL|nullptr|[A-Z][A-Z0-9_]*)\s*\)?$")

# CadQuery
CAD_NAME_RE = re.compile(r"(^|_)(ref|proxy|envelope|" + "dum" + "my|" + "place" + "holder|" + "st" + "ub|" + "fa" + "ke|standin|stand_in|blockout|mock)(_|$)", re.IGNORECASE)
CQ_PRIMITIVES = {"box", "cylinder", "sphere", "cone", "wedge", "torus"}
CQ_SKETCH = {"rect", "circle", "polygon", "ellipse"}
CQ_EXTRUDE = {"extrude"}
CQ_TRANSFORMS = {"Workplane", "workplane", "translate", "rotate", "rotateAboutCenter", "transformed", "center", "moveTo",
                 "move", "mirror", "located", "moved", "val", "findSolid", "edges", "faces", "fillet", "chamfer"}

# config files
CONFIG_EXT = (".json", ".yaml", ".yml", ".ini", ".cfg", ".toml")
CONFIG_NAMES = ("Kconfig", "sdkconfig.defaults")
DEFAULT_CRED_RE = re.compile(r"(password|passwd|pwd|secret|token|api_key)\W{0,4}[\"']?\s*[:=]\s*[\"']?(admin|password|changeme|1234|12345|0000|root|default)\b", re.IGNORECASE)

# file kinds
PY_EXT = (".py",)
C_EXT = (".c", ".h", ".cpp", ".hpp", ".cc", ".cxx", ".hh", ".ino")
SLASH_EXT = C_EXT + (".js", ".mjs", ".ts", ".tsx", ".jsx", ".cs", ".java", ".rs", ".go", ".v", ".sv")
HASH_EXT = (".sh", ".ps1", ".psm1", ".cmake", ".yaml", ".yml", ".toml")

SKIP_DIRS = {".git", ".tmp", "harness", "node_modules", "__pycache__", ".venv", "venv", "build", "dist",
             "third_party", "vendor", "external", ".claude"}


# =====================================================================================================================
#  Small helpers
# =====================================================================================================================
def is_test_path(rel):
    """True for test code: a tests/ folder, test_*.ext, *_test.ext, *.test.ts and friends."""
    low = rel.lower()
    base = os.path.basename(low)
    return ("/test/" in "/" + low or "/tests/" in "/" + low or base.startswith("test_")
            or re.search(r"[._-]test\.[a-z]+$", base) is not None)


def norm_text(s):
    """Whitespace-collapsed, lower-case text: the line-insensitive identity of a finding."""
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()[:200]


def finding(cat, sev, rel, line, subject, text):
    """One finding record (ordered keys so the JSON reads well)."""
    return {"category": cat, "severity": sev, "file": rel, "line": int(line or 0), "subject": str(subject or ""),
            "text": str(text or "").strip()[:240], "new": None, "waived": False, "waiver": None}


def key_of(f):
    """Line-insensitive identity used to compare against the baseline."""
    return "|".join((f["category"], f["file"], f["subject"], norm_text(f["text"])))


# =====================================================================================================================
#  Comment extraction per language
# =====================================================================================================================
def py_comments(src):
    """[(line, text)] of every Python comment (tokenize: strings and docstrings are never comments)."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string.lstrip("#").strip()))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        # an unparsable file still gets scanned line by line, so a broken file cannot hide a marker
        for i, line in enumerate(src.splitlines(), 1):
            pos = line.find("#")
            if pos >= 0:
                out.append((i, line[pos + 1:].strip()))
    return out


def slash_comments(src):
    """[(line, text)] of // and /* */ comments; string literals are blanked first so a URL never looks like a comment."""
    out = []
    # blank out string and char literals (keeps the line structure intact)
    blank = re.sub(r'"(\\.|[^"\\\n])*"', lambda m: '"' + " " * (len(m.group(0)) - 2) + '"', src)
    blank = re.sub(r"'(\\.|[^'\\\n])*'", lambda m: "'" + " " * (len(m.group(0)) - 2) + "'", blank)
    for m in re.finditer(r"//[^\n]*|/\*.*?\*/", blank, re.DOTALL):
        line = blank.count("\n", 0, m.start()) + 1
        raw = src[m.start():m.end()]
        for k, part in enumerate(raw.splitlines()):
            text = part.strip().lstrip("/").lstrip("*").rstrip("/").rstrip("*").strip()
            if text:
                out.append((line + k, text))
    return out


def hash_comments(src, powershell=False):
    """[(line, text)] of # comments (shell, PowerShell, YAML, CMake); PowerShell block comments <# #> included."""
    out = []
    lines = src.splitlines()
    in_block = False
    for i, line in enumerate(lines, 1):
        s = line.strip()
        if powershell:
            if in_block:
                out.append((i, s.replace("#>", "").strip()))
                if "#>" in s:
                    in_block = False
                continue
            if s.startswith("<#"):
                in_block = "#>" not in s[2:]
                out.append((i, s.strip("<#> ").strip()))
                continue
        # a # inside quotes is not a comment: drop quoted spans first
        bare = re.sub(r"\"(\\.|[^\"\\])*\"|'[^']*'", "", line)
        pos = bare.find("#")
        if pos >= 0 and not bare[:pos].rstrip().endswith("$"):        # $# in bash is the argument count
            out.append((i, bare[pos + 1:].strip()))
    return out


# =====================================================================================================================
#  Checks
# =====================================================================================================================
def check_comments(rel, comments, test):
    """code_marker and code_assumption findings from extracted comments."""
    res = []
    for line, text in comments:
        if not text:
            continue
        m = MARKER_RE.search(text)
        if m:
            res.append(finding("code_marker", "low" if test else "medium", rel, line, m.group(1).lower(), text))
        a = ASSUME_RE.search(text)
        if a and not BENCH_RE.search(text):
            res.append(finding("code_assumption", "low" if test else "medium", rel, line, a.group(1).lower(), text))
    return res


def check_py_stubs(rel, src, test):
    """py_stub findings: functions that only raise NotImplementedError, pass, or hold an ellipsis."""
    res = []
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return [finding("parse_error", "low", rel, getattr(exc, "lineno", 0), "syntax", str(exc))]
    protocol_classes = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
            if bases & {"Protocol", "ABC"}:
                protocol_classes.update(id(n) for n in node.body)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decos = {getattr(d, "id", getattr(d, "attr", "")) for d in node.decorator_list}
        if decos & {"abstractmethod", "abstractproperty", "overload"} or id(node) in protocol_classes:
            continue                                                   # an interface declaration, not an implementation
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            body = body[1:]                                            # docstring
        if len(body) != 1:
            continue
        st = body[0]
        if isinstance(st, ast.Raise):
            exc = st.exc.func if isinstance(st.exc, ast.Call) else st.exc
            if getattr(exc, "id", "") == "NotImplementedError":
                res.append(finding("py_stub", "low" if test else "medium", rel, node.lineno, node.name,
                                   "body is only raise NotImplementedError"))
        elif isinstance(st, ast.Pass) or (isinstance(st, ast.Expr) and isinstance(getattr(st, "value", None), ast.Constant)
                                          and st.value.value is Ellipsis):
            res.append(finding("py_stub", "low", rel, node.lineno, node.name, "body is only pass / ..."))
    return res


def check_c_fake_success(rel, src, test):
    """c_fake_success findings: hardware-named C / C++ functions whose whole body returns a constant."""
    if test:
        return []                                                      # test doubles are allowed in tests
    res = []
    for m in C_FUNC_RE.finditer(src):
        name, ret = m.group(1), m.group(3).strip()
        if name in ("if", "while", "for", "switch", "return", "sizeof"):
            continue
        if HW_NAME_RE.search(name) and C_CONST_RE.match(ret):
            line = src.count("\n", 0, m.start()) + 1
            res.append(finding("c_fake_success", "high", rel, line, name, f"whole body is 'return {ret};'"))
    return res


def _call_chain(node):
    """Method names of a call chain like cq.Workplane('XY').box(1, 2, 3).translate(...), outermost last."""
    names = []
    cur = node
    while isinstance(cur, ast.Call):
        fn = cur.func
        if isinstance(fn, ast.Attribute):
            names.append(fn.attr)
            cur = fn.value
        elif isinstance(fn, ast.Name):
            names.append(fn.id)
            break
        else:
            break
    return list(reversed(names))


def _is_bare_primitive(expr, assigns):
    """True when expr (or the variable it names, assigned once in the same function) is one primitive + transforms."""
    if isinstance(expr, ast.Name) and expr.id in assigns:
        expr = assigns[expr.id]
    if not isinstance(expr, ast.Call):
        return False
    chain = _call_chain(expr)
    if not chain:
        return False
    names = set(chain)
    has_prim = bool(names & CQ_PRIMITIVES) or (bool(names & CQ_SKETCH) and bool(names & CQ_EXTRUDE))
    allowed = CQ_PRIMITIVES | CQ_SKETCH | CQ_EXTRUDE | CQ_TRANSFORMS
    prim_count = sum(1 for n in chain if n in CQ_PRIMITIVES or n in CQ_EXTRUDE)
    return has_prim and names <= allowed and prim_count == 1


def check_cad(rel, src):
    """cad_standin_name and cad_bare_primitive findings in CadQuery files (AST only, CadQuery is never imported)."""
    if not re.search(r"^\s*(import cadquery|from cadquery|import cadquery as)", src, re.MULTILINE):
        return []
    res = []
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # single assignments in this function: name -> expression (to follow `wp = ...; return [("x", wp, ...)]`)
        assigns = {}
        for st in ast.walk(fn):
            if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                assigns[st.targets[0].id] = st.value
        # part rows: tuples whose first element is a string literal name and whose second is geometry
        for node in ast.walk(fn):
            if not isinstance(node, ast.Tuple) or len(node.elts) < 2:
                continue
            first = node.elts[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            pname = first.value
            if CAD_NAME_RE.search(pname):
                res.append(finding("cad_standin_name", "high", rel, node.lineno, pname, "part name announces a stand-in"))
            if fn.name in ("build", "build_parts", "parts") and _is_bare_primitive(node.elts[1], assigns):
                res.append(finding("cad_bare_primitive", "medium", rel, node.lineno, pname,
                                   "geometry is one bare primitive with only transforms"))
    return res


def check_config(rel, src):
    """config_marker findings: default credentials and open-work markers in configuration values."""
    res = []
    for i, line in enumerate(src.splitlines(), 1):
        if DEFAULT_CRED_RE.search(line):
            res.append(finding("config_marker", "medium", rel, i, "default credential", line.strip()))
            continue
        m = MARKER_RE.search(line)
        if m and not line.lstrip().startswith(("#", "//")):
            res.append(finding("config_marker", "medium", rel, i, m.group(1).lower(), line.strip()))
    return res


# =====================================================================================================================
#  Scope, baseline, waivers, git
# =====================================================================================================================
def scope_files(root):
    """Repo-relative paths of every file the audit reads."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not os.path.isfile(os.path.join(dirpath, d, "pyvenv.cfg")))
        for name in filenames:
            low = name.lower()
            if low.endswith(PY_EXT + SLASH_EXT + HASH_EXT + CONFIG_EXT) or name in CONFIG_NAMES:
                out.append(os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/"))
    return sorted(out)


def audit_file(root, rel):
    """Every finding in one file."""
    path = os.path.join(root, rel)
    try:
        if os.path.getsize(path) > 4 * 1024 * 1024:
            return []                                                  # generated blobs are not hand-written code
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            src = fh.read()
    except OSError as exc:
        return [finding("read_error", "low", rel, 0, "read", str(exc))]
    low = rel.lower()
    test = is_test_path(rel)
    res = []
    if low.endswith(PY_EXT):
        res += check_comments(rel, py_comments(src), test)
        res += check_py_stubs(rel, src, test)
        res += check_cad(rel, src)
    elif low.endswith(SLASH_EXT):
        res += check_comments(rel, slash_comments(src), test)
        if low.endswith(C_EXT):
            res += check_c_fake_success(rel, src, test)
    elif low.endswith(HASH_EXT):
        res += check_comments(rel, hash_comments(src, powershell=low.endswith((".ps1", ".psm1"))), test)
    # configuration values (JSON etc. have no comments; their values are the content)
    if (low.endswith(CONFIG_EXT) or os.path.basename(rel) in CONFIG_NAMES) and not low.endswith((".yaml", ".yml", ".toml")):
        if not low.endswith(("audit_baseline.json", "audit_waivers.json")):
            res += check_config(rel, src)
    return res


def load_json(path, what):
    """Parsed JSON or a clear SystemExit(2)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[audit] ERROR: cannot read {what} {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)


def load_waivers(path):
    """Validated waiver list; a waiver without category / file / reason / owner, or with a bad regex, is refused."""
    if not path or not os.path.isfile(path):
        return []
    data = load_json(path, "waivers")
    rows = data.get("waivers", []) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        print(f"[audit] ERROR: {path}: expected a list of waivers", file=sys.stderr)
        raise SystemExit(2)
    good = []
    for i, w in enumerate(rows):
        if not isinstance(w, dict) or not all(str(w.get(k, "")).strip() for k in ("category", "file", "reason", "owner")):
            print(f"[audit] ERROR: {path} waiver #{i}: category, file, reason and owner are all required", file=sys.stderr)
            raise SystemExit(2)
        try:
            w["_rx"] = re.compile(w.get("match") or ".*")
        except re.error as exc:
            print(f"[audit] ERROR: {path} waiver #{i}: bad match regex: {exc}", file=sys.stderr)
            raise SystemExit(2)
        good.append(w)
    return good


def apply_waivers(findings, waivers):
    """Mark waived findings in place."""
    for f in findings:
        for w in waivers:
            if w["category"] not in (f["category"], "*"):
                continue
            if not fnmatch.fnmatch(f["file"], w["file"]):
                continue
            if w["_rx"].search(f["subject"]) or w["_rx"].search(f["text"]):
                f["waived"] = True
                f["waiver"] = {"reason": w["reason"], "owner": w["owner"]}
                break


def git_changed(root, rev):
    """Repo-relative files changed since merge-base(rev, HEAD): committed, staged, unstaged and untracked."""
    def run(args):
        r = subprocess.run(["git", "-C", root] + args, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"[audit] ERROR: git {' '.join(args)}: {r.stderr.strip()[:300]}", file=sys.stderr)
            raise SystemExit(2)
        return [l.strip() for l in r.stdout.splitlines() if l.strip()]
    base = run(["merge-base", rev, "HEAD"])
    if not base:
        print(f"[audit] ERROR: no merge base between {rev} and HEAD", file=sys.stderr)
        raise SystemExit(2)
    files = set(run(["diff", "--name-only", base[0]]))                 # committed + staged + unstaged vs the base
    files |= set(run(["ls-files", "--others", "--exclude-standard"]))  # untracked
    return {f.replace("\\", "/") for f in files}


# =====================================================================================================================
#  Main
# =====================================================================================================================
def main(argv=None):
    here = os.path.dirname(os.path.abspath(__file__))
    ap = argparse.ArgumentParser(description="No-stand-in audit (see the module docstring)")
    ap.add_argument("--root", default=os.path.abspath(os.path.join(here, "..")))
    ap.add_argument("--json", default=None, help="machine-readable report")
    ap.add_argument("--quiet", action="store_true")
    gate = ap.add_mutually_exclusive_group()
    gate.add_argument("--fail-on-new", action="store_true", help="exit 1 on a NEW unwaived finding at or above --fail-level")
    gate.add_argument("--fail-on-any", action="store_true", help="exit 1 on ANY unwaived finding at or above --fail-level")
    ap.add_argument("--fail-level", choices=("high", "medium", "low"), default="medium")
    bl = ap.add_mutually_exclusive_group()
    bl.add_argument("--baseline", default=None, help="baseline report (default <root>/tools/audit_baseline.json)")
    bl.add_argument("--no-baseline", action="store_true", help="compare against nothing (every finding is new)")
    ap.add_argument("--waivers", default=None, help="waiver file (default <root>/tools/audit_waivers.json)")
    ap.add_argument("--write-baseline", action="store_true", help="write the current findings as the tracked baseline")
    ap.add_argument("--changed-since", default=None, help="only files changed since merge-base(REV, HEAD)")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if not os.path.isdir(root):
        print(f"[audit] ERROR: --root {root} is not a directory", file=sys.stderr)
        return 2
    t0 = time.time()

    # ---- scan
    files = scope_files(root)
    if args.changed_since:
        changed = git_changed(root, args.changed_since)
        files = [f for f in files if f in changed]
    findings = []
    for rel in files:
        findings += audit_file(root, rel)

    # ---- waivers
    waiver_path = args.waivers or os.path.join(root, "tools", "audit_waivers.json")
    apply_waivers(findings, load_waivers(waiver_path))

    # ---- baseline comparison
    baseline_path = None if args.no_baseline else (args.baseline or os.path.join(root, "tools", "audit_baseline.json"))
    base_keys = {}
    if baseline_path and os.path.isfile(baseline_path) and not args.write_baseline:
        data = load_json(baseline_path, "baseline")
        for f in (data.get("findings", []) if isinstance(data, dict) else []):
            if isinstance(f, dict) and {"category", "file", "subject", "text", "severity"} <= set(f):
                base_keys[key_of(f)] = SEV_RANK.get(f["severity"], 0)
    for f in findings:
        k = key_of(f)
        f["new"] = (k not in base_keys) or (SEV_RANK[f["severity"]] > base_keys[k])

    # ---- gate
    level = SEV_RANK[args.fail_level]
    gating = [f for f in findings if not f["waived"] and SEV_RANK[f["severity"]] >= level
              and (args.fail_on_any or f["new"])]
    by_sev = {s: sum(1 for f in findings if f["severity"] == s and not f["waived"]) for s in SEV_RANK}
    report = {
        "schema": SCHEMA, "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "root": root,
        "elapsed_s": round(time.time() - t0, 2), "files_scanned": len(files),
        "baseline": baseline_path if base_keys else None, "changed_since": args.changed_since,
        "totals": {"findings": len(findings), "waived": sum(1 for f in findings if f["waived"]),
                   "new": sum(1 for f in findings if f["new"] and not f["waived"]),
                   "new_gate": sum(1 for f in findings if f["new"] and not f["waived"] and SEV_RANK[f["severity"]] >= level),
                   "gating": len(gating), "by_severity": by_sev},
        "findings": findings,
    }

    # ---- outputs
    if args.write_baseline:
        out = baseline_path or os.path.join(root, "tools", "audit_baseline.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        keep = [{k: f[k] for k in ("category", "severity", "file", "line", "subject", "text")} for f in findings]
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            json.dump({"schema": SCHEMA, "written": report["generated"], "findings": keep}, fh, indent=1, ensure_ascii=False)
        print(f"[audit] baseline written: {out} ({len(keep)} findings)")
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        with open(args.json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(report, fh, indent=1, ensure_ascii=False)
    if not args.quiet:
        for f in sorted(findings, key=lambda x: (-SEV_RANK[x["severity"]], x["file"], x["line"])):
            mark = "waived" if f["waived"] else ("NEW" if f["new"] else "base")
            print(f"{f['severity']:<6} {mark:<6} {f['category']:<18} {f['file']}:{f['line']}  [{f['subject']}] {f['text'][:120]}")
    t = report["totals"]
    print(f"[audit] {len(files)} files, {t['findings']} findings ({t['waived']} waived), "
          f"high {by_sev['high']} medium {by_sev['medium']} low {by_sev['low']}, new {t['new']}, gating {t['gating']}")
    if args.fail_on_new or args.fail_on_any:
        return 1 if gating else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
