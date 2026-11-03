#!/usr/bin/env bash
# ======================================================================================================================
#  setup_tools.sh - installs or verifies the hardware-in-the-loop toolchain on Linux (Debian / Ubuntu family), with
#                   pinned versions and checksums, idempotent. The Windows twin of this script is setup_tools.ps1.
#
#  Interpreter: bash 4+.
#  Paths: command line (everything goes under --tools-root)
#  Run: bash scripts/setup_tools.sh --tools-root /opt/hiltools [--verify-only] [--skip kicad,java,freerouting,osscad,cadquery]
#
#    KiCad 10          from the KiCad PPA (ppa:kicad/kicad-10.0-releases) through apt; gives kicad-cli and pcbnew for
#                      the system python3 (sudo needed; the script prints the commands instead when it has no sudo)
#    Java 21           apt openjdk-21-jre-headless
#    Freerouting 2.1.0 <tools-root>/freerouting/freerouting-2.1.0.jar, SHA-256 pinned (see setup_tools.ps1)
#    OSS CAD Suite     the 2026-10-24 Linux x64 build is not pinned by this method (only the Windows archive was
#                      verified); the script prints where to get it and how to record its SHA-256 for your pin
#    CadQuery venv     <tools-root>/cqv (python3 -m venv; pip cache in <tools-root>/cache/pip)
#  Writes <tools-root>/hil_tools.env (HIL_* variables). Exit 0 all present, 1 something missing, 2 bad arguments.
#  Nothing is written into the home directory: pip, Java and the downloads use <tools-root>.
# ======================================================================================================================
set -u
ROOT=""; VERIFY=0; SKIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tools-root) ROOT="${2:-}"; shift 2;;
    --verify-only) VERIFY=1; shift;;
    --skip) SKIP=",${2:-},"; shift 2;;
    *) echo "setup_tools.sh: unknown argument $1" >&2; exit 2;;
  esac
done
if [ -z "$ROOT" ]; then echo "usage: setup_tools.sh --tools-root DIR [--verify-only] [--skip a,b]" >&2; exit 2; fi
ROOT="$(readlink -m "$ROOT")"
case "$ROOT/" in "$(readlink -m "$HOME")"/*) echo "setup_tools.sh: refusing --tools-root inside the home directory ($ROOT)" >&2; exit 2;; esac

FR_URL="https://github.com/freerouting/freerouting/releases/download/v2.1.0/freerouting-2.1.0.jar"
FR_SIZE=66978394
FR_SHA="2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def"

missing=0
skip() { case "$SKIP" in *",$1,"*) return 0;; *) return 1;; esac; }
say() { printf '%-12s %s\n' "$1" "$2"; }
SUDO=""; if [ "$(id -u)" != 0 ]; then command -v sudo >/dev/null 2>&1 && SUDO="sudo -n"; fi
apt_install() {   # apt_install <package...>: only when not verify-only and sudo works without a prompt
  [ "$VERIFY" = 1 ] && return 1
  if [ "$(id -u)" != 0 ] && ! $SUDO true 2>/dev/null; then echo "  run: sudo apt-get install -y $*"; return 1; fi
  $SUDO apt-get install -y "$@"
}
[ "$VERIFY" = 1 ] || mkdir -p "$ROOT/cache/pip" "$ROOT/freerouting"
ENVF="$ROOT/hil_tools.env"; ENVL=""

# ---- KiCad 10
if ! skip kicad; then
  if ! command -v kicad-cli >/dev/null 2>&1; then
    echo "  KiCad: sudo add-apt-repository -y ppa:kicad/kicad-10.0-releases && sudo apt-get update"
    apt_install kicad >/dev/null || true
  fi
  if command -v kicad-cli >/dev/null 2>&1; then
    v="$(kicad-cli version 2>/dev/null | head -1)"
    say kicad "ok kicad-cli $v ($(command -v kicad-cli)); pcbnew in python3: $(python3 -c 'import pcbnew; print("yes")' 2>/dev/null || echo MISSING)"
    ENVL="$ENVL
HIL_KICAD_CLI=$(command -v kicad-cli)"
  else say kicad "MISSING kicad-cli"; missing=1; fi
fi

# ---- Java 21
if ! skip java; then
  j="$(command -v java || true)"; jv=""
  [ -n "$j" ] && jv="$("$j" -version 2>&1 | head -1)"
  if ! echo "$jv" | grep -Eq 'version "(2[1-9]|[3-9][0-9])'; then apt_install openjdk-21-jre-headless >/dev/null || true; j="$(command -v java || true)"; jv="$([ -n "$j" ] && "$j" -version 2>&1 | head -1)"; fi
  if echo "$jv" | grep -Eq 'version "(2[1-9]|[3-9][0-9])'; then say java "ok $jv ($j)"; ENVL="$ENVL
HIL_JAVA=$j"; else say java "MISSING Java >= 21"; missing=1; fi
fi

# ---- Freerouting 2.1.0 (size + SHA-256 pinned)
if ! skip freerouting; then
  jar="$ROOT/freerouting/freerouting-2.1.0.jar"
  ok_jar() { [ -f "$jar" ] && [ "$(stat -c %s "$jar")" = "$FR_SIZE" ] && [ "$(sha256sum "$jar" | cut -d' ' -f1)" = "$FR_SHA" ]; }
  if ! ok_jar && [ "$VERIFY" = 0 ]; then
    echo "  downloading $FR_URL"
    curl -fL --retry 3 -o "$jar.part" "$FR_URL" && mv -f "$jar.part" "$jar"
    ok_jar || { echo "  CHECKSUM MISMATCH: deleting $jar"; rm -f "$jar"; }
  fi
  if ok_jar; then say freerouting "ok 2.1.0 sha256 ${FR_SHA:0:12} ($jar)"; ENVL="$ENVL
HIL_FREEROUTING_JAR=$jar"; else say freerouting "MISSING $jar"; missing=1; fi
fi

# ---- OSS CAD Suite (pointer: pin your own Linux archive)
if ! skip osscad; then
  if command -v yosys >/dev/null 2>&1; then say osscad "ok $(yosys -V 2>/dev/null | head -1)"
  else say osscad "MISSING: download the 2026-10-24 linux-x64 archive from github.com/YosysHQ/oss-cad-suite-build/releases into $ROOT, record its sha256sum in your TOOLS doc, unpack, add bin/ to PATH"; missing=1; fi
fi

# ---- CadQuery venv
if ! skip cadquery; then
  vpy="$ROOT/cqv/bin/python"
  if [ ! -x "$vpy" ] && [ "$VERIFY" = 0 ]; then
    python3 -m venv "$ROOT/cqv" && PIP_CACHE_DIR="$ROOT/cache/pip" "$vpy" -m pip install --upgrade pip \
      && PIP_CACHE_DIR="$ROOT/cache/pip" "$vpy" -m pip install cadquery trimesh matplotlib numpy pillow
  fi
  if [ -x "$vpy" ] && cv="$("$vpy" -I -c 'import cadquery, trimesh; print(cadquery.__version__, trimesh.__version__)' 2>/dev/null)"; then
    say cadquery "ok cadquery/trimesh $cv ($vpy)"; ENVL="$ENVL
HIL_CQ_PYTHON=$vpy"
  else say cadquery "MISSING venv or packages in $ROOT/cqv"; missing=1; fi
fi

if [ "$VERIFY" = 0 ] && [ -n "$ENVL" ]; then
  printf '# written by setup_tools.sh %s; load with: set -a; . %s; set +a%s\n' "$(date -Iseconds)" "$ENVF" "$ENVL" > "$ENVF"
  say environment "$ENVF"
fi
exit "$missing"
