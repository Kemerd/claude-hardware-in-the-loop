#!/usr/bin/env bash
# ======================================================================================================================
#  merge_lock.sh - serialises merges into the shared branches (dev, and the dev -> main promotion).
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux).
#  Run: bash harness/swarm/merge_lock.sh take <agent> <branch> [wait_min=30]
#       bash harness/swarm/merge_lock.sh release <agent>
#       bash harness/swarm/merge_lock.sh status
#
#  The lock is the directory harness/swarm/MERGE_LOCK (mkdir is atomic). owner.txt inside records the agent id, the
#  branch being merged, the time and the pid of the shell that took it. Only the agent named in owner.txt may release
#  it. A lock whose pid is dead AND that is older than 60 minutes is stale: it is archived to
#  harness/swarm/archive/merge_lock_<time>/ (never just deleted) and taken over.
#  take exits 0 when the lock is held, 1 after wait_min minutes without it, 2 on bad arguments.
# ======================================================================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="$HERE/MERGE_LOCK"
STALE_MIN=60

pid_alive() {
  local pid="$1"
  [ -z "$pid" ] && return 0
  if command -v tasklist.exe >/dev/null 2>&1; then
    tasklist.exe //FI "PID eq $pid" 2>/dev/null | grep -q " $pid " && return 0
  fi
  kill -0 "$pid" 2>/dev/null
}
field() { grep -o "^$1=.*" "$LOCK/owner.txt" 2>/dev/null | head -1 | cut -d= -f2-; }

case "${1:-}" in
take)
  agent="${2:-}"; branch="${3:-}"; wait_min="${4:-30}"
  if [ -z "$agent" ] || [ -z "$branch" ]; then echo "usage: merge_lock.sh take <agent> <branch> [wait_min]" >&2; exit 2; fi
  case "$wait_min" in *[!0-9]*) echo "merge_lock.sh: wait_min must be a number" >&2; exit 2;; esac
  # the pid that owns the lock: the caller's shell (Windows pid when Git Bash can map it)
  opid="$PPID"; w="$(cat "/proc/$opid/winpid" 2>/dev/null || true)"; [ -n "$w" ] && opid="$w"
  deadline=$(( $(date +%s) + wait_min * 60 ))
  while true; do
    if mkdir "$LOCK" 2>/dev/null; then
      printf 'agent=%s\nbranch=%s\ntaken=%s\npid=%s\n' "$agent" "$branch" "$(date '+%Y-%m-%d %H:%M:%S')" "$opid" > "$LOCK/owner.txt"
      echo "merge_lock.sh: MERGE_LOCK held by $agent for $branch"
      exit 0
    fi
    # ---- stale: owner pid dead and the lock older than STALE_MIN
    age_min=$(( ( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || date +%s) ) / 60 ))
    if [ "$age_min" -ge "$STALE_MIN" ] && ! pid_alive "$(field pid)"; then
      mkdir -p "$HERE/archive"
      mv "$LOCK" "$HERE/archive/merge_lock_$(date '+%Y%m%d_%H%M%S')" 2>/dev/null && echo "merge_lock.sh: archived a stale lock ($age_min min, owner gone)"
      continue
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "merge_lock.sh: still held by $(field agent) for $(field branch) since $(field taken); gave up after $wait_min min" >&2
      exit 1
    fi
    sleep 15
  done
  ;;
release)
  agent="${2:-}"
  if [ -z "$agent" ]; then echo "usage: merge_lock.sh release <agent>" >&2; exit 2; fi
  if [ ! -d "$LOCK" ]; then echo "merge_lock.sh: not held"; exit 0; fi
  if [ "$(field agent)" != "$agent" ]; then echo "merge_lock.sh: held by $(field agent), not $agent: refusing" >&2; exit 1; fi
  rm -f "$LOCK/owner.txt" && rmdir "$LOCK" && echo "merge_lock.sh: released by $agent"
  ;;
status)
  if [ -d "$LOCK" ]; then echo "held: $(tr '\n' ' ' < "$LOCK/owner.txt" 2>/dev/null)"; else echo "free"; fi
  ;;
*)
  echo "usage: merge_lock.sh take <agent> <branch> [wait_min] | release <agent> | status" >&2; exit 2
  ;;
esac
