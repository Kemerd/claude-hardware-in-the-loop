#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 kicad_env.py - find the KiCad install and keep KiCad's per-user state out of the home directory
=======================================================================================================================
 Interpreter: KiCad 10's bundled python (C:\\Program Files\\KiCad\\10.0\\bin\\python.exe on Windows, the distro python
              that ships pcbnew on Linux) or any Python >= 3.9 (this module never imports pcbnew itself).

 Why this module exists
   Every kicad-cli call and every `import pcbnew` reads and WRITES KiCad's per-user folders: settings JSON files
   (kicad_common.json, eeschema.json, ...), a documents tree and a cache. By default those live under the user profile.
   A generator pipeline that runs hundreds of times must not touch the profile (it is shared with the GUI the engineer
   uses, it fills up, and two runs racing on one settings file corrupt it). isolate() points KiCad at a scratch folder
   BEFORE pcbnew is imported; child kicad-cli processes inherit the environment.

 What isolate() does
   * KICAD_CONFIG_HOME / KICAD_DOCUMENTS_HOME / KICAD_CACHE_HOME -> <home>/config, <home>/documents, <home>/cache
   * TEMP / TMP -> <home>/tmp (wx writes temp files during DSN export and plotting)
   * seeds <home>/config/<ver>/kicad_common.json with KiCad's git integration switched off. Observed on KiCad 10.0.6:
     the first kicad-cli run writes a "git" section {"enableGit": true, "updatInterval": 5, ...} into kicad_common.json;
     with it on, KiCad tracks the repository status of every project it opens, which costs time on every CLI call and
     is pointless for a scripted run. The seeded file only sets that key; KiCad fills in every other default itself.
   * seed_lib_tables(): writes a global fp-lib-table / sym-lib-table holding ONLY the stock libraries a project names,
     so KiCad never has to enumerate the full stock library set (hundreds of libraries) on a CLI call.

 Overrides (environment)
   HIL_KICAD_ROOT   install root (the folder holding bin/ and share/kicad), e.g. C:\\Program Files\\KiCad\\10.0
   HIL_KICAD_CLI    full path of kicad-cli(.exe)
   HIL_KICAD_HOME   scratch home for the per-user folders (default <repo>/.tmp/kicad_home)

 Paths: the default scratch home resolves from __file__ (<repo>/.tmp/kicad_home); everything else comes from the
        environment or the install search below.
=======================================================================================================================
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

# ---------------------------------------------------------------------------------------------------------------------
#  Windows consoles (cp1252) must never crash on a non-ASCII path or library description
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

# ---------------------------------------------------------------------------------------------------------------------
#  Repository anchors: this file lives at <repo>/pcb/tools/kicad_env.py
# ---------------------------------------------------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_HOME = os.path.join(REPO, ".tmp", "kicad_home")


# =====================================================================================================================
#  Install discovery
# =====================================================================================================================
def _version_key(path: str) -> tuple:
    """Numeric sort key for an install folder name such as '10.0' or '9.0' (so 10.0 sorts above 9.0)."""
    out = []
    for piece in os.path.basename(path.rstrip("\\/")).split("."):
        out.append(int(piece) if piece.isdigit() else -1)
    return tuple(out) if out else (-1,)


def _windows_roots() -> list[str]:
    """KiCad install roots on Windows, newest version first."""
    bases = {os.environ.get("ProgramFiles", ""), r"C:\Program Files"}
    roots: set[str] = set()
    for base in bases:
        if base and os.path.isdir(os.path.join(base, "KiCad")):
            roots.update(p for p in glob.glob(os.path.join(base, "KiCad", "*")) if os.path.isdir(p))
    return sorted(roots, key=_version_key, reverse=True)


def kicad_root() -> str | None:
    """The install root (holding bin/ and share/kicad), or None on a Linux distro install (no single root)."""
    env = os.environ.get("HIL_KICAD_ROOT", "").strip()
    if env:
        if not os.path.isdir(env):
            raise SystemExit(f"kicad_env: HIL_KICAD_ROOT points at a missing folder: {env}")
        return env
    if os.name == "nt":
        for root in _windows_roots():
            if os.path.isfile(os.path.join(root, "bin", "kicad-cli.exe")):
                return root
    return None


def share_dir() -> str:
    """Folder that holds the stock symbols/ and footprints/ libraries."""
    cands = []
    root = kicad_root()
    if root:
        cands.append(os.path.join(root, "share", "kicad"))
    cands += ["/usr/share/kicad", "/usr/local/share/kicad"]
    for c in cands:
        if os.path.isdir(os.path.join(c, "footprints")) and os.path.isdir(os.path.join(c, "symbols")):
            return c
    raise SystemExit("kicad_env: KiCad stock libraries not found; set HIL_KICAD_ROOT to the install root")


def cli() -> str:
    """Path of kicad-cli (env override first, then the install root, then PATH)."""
    env = os.environ.get("HIL_KICAD_CLI", "").strip()
    if env:
        if not os.path.isfile(env):
            raise SystemExit(f"kicad_env: HIL_KICAD_CLI points at a missing file: {env}")
        return env
    root = kicad_root()
    if root:
        exe = os.path.join(root, "bin", "kicad-cli.exe" if os.name == "nt" else "kicad-cli")
        if os.path.isfile(exe):
            return exe
    found = shutil.which("kicad-cli")
    if found:
        return found
    raise SystemExit("kicad_env: kicad-cli not found; install KiCad 10 or set HIL_KICAD_CLI")


def major_version() -> int:
    """Major version of the install (10 for ...\\KiCad\\10.0). Falls back to 10 when the path carries no version."""
    for piece in os.path.normpath(share_dir()).split(os.sep):
        head = piece.split(".")[0]
        if head.isdigit() and int(head) >= 6:
            return int(head)
    return 10


def footprint_dir(lib: str) -> str:
    """Folder of one stock footprint library, e.g. footprint_dir('Resistor_SMD')."""
    if not lib or "/" in lib or "\\" in lib:
        raise SystemExit(f"kicad_env: bad footprint library name {lib!r}")
    path = os.path.join(share_dir(), "footprints", f"{lib}.pretty")
    if not os.path.isdir(path):
        raise SystemExit(f"kicad_env: stock footprint library missing: {path}")
    return path


def fp_uri(lib: str) -> str:
    """Portable URI of a stock footprint library for a lib table: ${KICAD<ver>_FOOTPRINT_DIR}/<lib>.pretty."""
    if not lib:
        raise SystemExit("kicad_env: fp_uri needs a library name")
    return "${KICAD%d_FOOTPRINT_DIR}/%s.pretty" % (major_version(), lib)


def sym_uri(lib: str) -> str:
    """Portable URI of a stock symbol library: ${KICAD<ver>_SYMBOL_DIR}/<lib>.kicad_sym."""
    if not lib:
        raise SystemExit("kicad_env: sym_uri needs a library name")
    return "${KICAD%d_SYMBOL_DIR}/%s.kicad_sym" % (major_version(), lib)


# =====================================================================================================================
#  Home-directory isolation
# =====================================================================================================================
_ISOLATED: dict[str, str] = {}


def _config_version_dir(home: str) -> str:
    """<home>/config/<major>.0, the folder KiCad writes its JSON settings into."""
    try:
        ver = major_version()
    except SystemExit:
        ver = 10                                    # no install found yet: still isolate, KiCad is checked later
    return os.path.join(home, "config", f"{ver}.0")


def _seed_common_json(home: str) -> None:
    """Write (or update) kicad_common.json so KiCad's git integration is off; every other key stays KiCad's."""
    cdir = _config_version_dir(home)
    os.makedirs(cdir, exist_ok=True)
    path = os.path.join(cdir, "kicad_common.json")
    data: dict = {}
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}                               # a torn file from a crashed run: start again from KiCad defaults
    git = data.get("git") if isinstance(data.get("git"), dict) else {}
    if git.get("enableGit") is False:
        return                                      # already seeded: do not rewrite the file on every import
    git["enableGit"] = False
    data["git"] = git
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)                           # atomic: a parallel run never reads half a file


def _inside_profile(path: str) -> bool:
    """True when `path` is the user profile itself or a non-temp folder inside it (the folders isolate() protects)."""
    profile = os.path.normcase(os.path.abspath(os.path.expanduser("~")))
    p = os.path.normcase(os.path.abspath(path))
    if p == profile:
        return True
    if not p.startswith(profile + os.sep):
        return False
    # the OS temp tree under the profile (AppData\Local\Temp) is scratch by definition and allowed
    temp_root = os.path.normcase(os.path.abspath(os.environ.get("TEMP", "") or os.environ.get("TMP", "") or profile))
    return not (temp_root != profile and p.startswith(temp_root))


def isolate(home: str | None = None) -> str:
    """
    Point KiCad's per-user folders (and wx's temp folder) at `home` (default HIL_KICAD_HOME or <repo>/.tmp/kicad_home).
    Call before `import pcbnew`; idempotent. Returns the absolute home path.
    """
    home = os.path.abspath(home or os.environ.get("HIL_KICAD_HOME", "").strip() or DEFAULT_HOME)
    # ---- the user profile is exactly what this module protects: refuse it loudly
    if _inside_profile(home):
        raise SystemExit(f"kicad_env: refusing a KiCad home inside the user profile: {home}")
    for sub, var in (("config", "KICAD_CONFIG_HOME"), ("documents", "KICAD_DOCUMENTS_HOME"), ("cache", "KICAD_CACHE_HOME")):
        path = os.path.join(home, sub)
        os.makedirs(path, exist_ok=True)
        os.environ[var] = path                      # inherited by every kicad-cli child process
        _ISOLATED[var] = path
    # ---- wx temp files (DSN export, plotting) follow; set after the profile check, which reads TEMP
    tmp = os.path.join(home, "tmp")
    os.makedirs(tmp, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = os.environ["TMPDIR"] = tmp
    _seed_common_json(home)
    return home


def seed_lib_tables(fp_libs: list[str] | tuple[str, ...] = (), sym_libs: list[str] | tuple[str, ...] = ()) -> None:
    """
    Write global fp-lib-table / sym-lib-table files into the isolated config folder that list ONLY the given stock
    libraries. Without them KiCad works with no global libraries on the CLI; with the full stock table it enumerates
    every library on every start. Project lib tables still win for the project's own libraries.
    """
    if "KICAD_CONFIG_HOME" not in _ISOLATED:
        isolate()
    cdir = _config_version_dir(os.path.dirname(_ISOLATED["KICAD_CONFIG_HOME"]))
    os.makedirs(cdir, exist_ok=True)
    fp_rows = [f'  (lib (name "{lib}")(type "KiCad")(uri "{fp_uri(lib)}")(options "")(descr "stock"))'
               for lib in sorted(set(fp_libs)) if lib]
    sym_rows = [f'  (lib (name "{lib}")(type "KiCad")(uri "{sym_uri(lib)}")(options "")(descr "stock"))'
                for lib in sorted(set(sym_libs)) if lib]
    with open(os.path.join(cdir, "fp-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("(fp_lib_table\n  (version 7)\n" + "\n".join(fp_rows) + ("\n" if fp_rows else "") + ")\n")
    with open(os.path.join(cdir, "sym-lib-table"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write("(sym_lib_table\n  (version 7)\n" + "\n".join(sym_rows) + ("\n" if sym_rows else "") + ")\n")


def isolated_vars() -> dict[str, str]:
    """The KICAD_*_HOME variables isolate() set (empty before isolate() ran)."""
    return dict(_ISOLATED)


# ---------------------------------------------------------------------------------------------------------------------
#  Self-report: `python pcb/tools/kicad_env.py` prints what was found (a quick install check for setup scripts)
# ---------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    h = isolate()
    print(f"kicad root   {kicad_root()}")
    print(f"share dir    {share_dir()}")
    print(f"kicad-cli    {cli()}")
    print(f"major        {major_version()}")
    print(f"isolated     {h}")
