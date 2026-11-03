#!/usr/bin/env bash
# ======================================================================================================================
#  detach.sh - start a long chain script fully detached from the agent session, with done / FAILED markers.
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux).
#  Run: bash harness/swarm/detach.sh <chain.sh> <logfile> [args...]
#
#  Why: Git Bash has no setsid, and `nohup ... & disown` children die when the agent's process tree exits (a whole
#  night of jobs was lost that way once). On Windows the chain is started by PowerShell Start-Process with a hidden
#  window, so its parent is outside the session tree; on Linux setsid + nohup does the same.
#
#  What you get:
#    <logfile>          everything the chain prints (stdout + stderr), appended
#    <logfile>.pid      the detached process id (also printed on stdout)
#    <logfile>.done     written when the chain exits 0   (contents: exit code and end time)
#    <logfile>.FAILED   written when the chain exits non-zero (contents: exit code and end time)
#  Stale markers from an earlier run of the same log are removed before the start, so a watcher never sees an old one.
#  Wait on the markers with harness/swarm/watch.sh, which also flags a hung job (no log growth for 10 minutes).
#
#  The chain itself still takes its machine slots through harness/swarm/locks/slot.sh (passing its own $$ as the
#  owner pid) and runs its heavy steps at low priority with timeouts.
# ======================================================================================================================
set -u
if [ $# -lt 2 ]; then echo "usage: detach.sh <chain.sh> <logfile> [args...]" >&2; exit 2; fi
if [ ! -f "$1" ]; then echo "detach.sh: chain script not found: $1" >&2; exit 2; fi

here="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
script="$(readlink -f "$1")"
logdir="$(dirname "$2")"
mkdir -p "$logdir" || { echo "detach.sh: cannot create log folder $logdir" >&2; exit 2; }
log="$(readlink -f "$logdir")/$(basename "$2")"
shift 2

# ---- clear the markers of an earlier run of the same log
rm -f "$log.done" "$log.FAILED" "$log.pid"

# ---- the wrapper: runs the chain from the caller's working directory and writes exactly one marker at the end.
#      A wrapper file avoids quoting every argument through PowerShell; it lives under harness/swarm/run/ and removes
#      itself when the chain ends.
mkdir -p "$here/run"
wrap="$(mktemp "$here/run/detach_XXXXXX.sh")"
{
  echo '#!/usr/bin/env bash'
  printf 'cd %q || exit 97\n' "$(pwd)"
  printf 'bash %q' "$script"; for a in "$@"; do printf ' %q' "$a"; done; printf ' >> %q 2>&1\n' "$log"
  echo 'rc=$?'
  printf 'if [ "$rc" -eq 0 ]; then m=%q; else m=%q; fi\n' "$log.done" "$log.FAILED"
  echo 'echo "exit=$rc end=$(date "+%Y-%m-%d %H:%M:%S")" > "$m"'
  printf 'rm -f %q\n' "$wrap"
  echo 'exit "$rc"'
} > "$wrap"

# ---- start it outside the session tree
if command -v cygpath >/dev/null 2>&1 && command -v powershell.exe >/dev/null 2>&1; then
  bash_exe="$(cygpath -w "$(command -v bash)")"
  wrap_w="$(cygpath -w "$wrap")"
  pid="$(powershell.exe -NoProfile -Command "(Start-Process -FilePath '$bash_exe' -ArgumentList '\"$wrap_w\"' -WindowStyle Hidden -PassThru).Id" | tr -d '\r')"
else
  setsid nohup bash "$wrap" > /dev/null 2>&1 < /dev/null &
  pid=$!
fi
if [ -z "$pid" ]; then echo "detach.sh: start failed (no pid)" >&2; exit 1; fi
echo "$pid" > "$log.pid"
echo "$pid"
