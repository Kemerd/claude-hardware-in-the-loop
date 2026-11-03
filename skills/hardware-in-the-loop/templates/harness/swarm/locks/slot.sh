#!/usr/bin/env bash
# ======================================================================================================================
#  slot.sh - the machine-safety gate every heavy job passes before it starts (builds, sims, CAD chains, KiCad batches,
#            routers, renders, Unreal). Keeps the PC responsive for the person sitting at it.
#
#  Interpreter: bash (Git Bash on Windows, any bash 4+ on Linux). Run: bash harness/swarm/locks/slot.sh take|release|status
#
#  TAKE (blocks until the gate opens, then prints shell exports to eval):
#     eval "$(bash harness/swarm/locks/slot.sh take <kind> <agent> "<job>" <expected_GB> [owner_pid])"
#     ... run the job (BelowNormal priority, with a timeout) ...
#     bash harness/swarm/locks/slot.sh release "$HIL_SLOT_HEAVY" "$HIL_SLOT_KIND"
#  A detached chain passes its own $$ as owner_pid so the slot lives exactly as long as the chain.
#
#  STATUS: bash harness/swarm/locks/slot.sh status      (held slots, queue, the meter reading)
#
#  The gate (all must hold, re-checked every loop):
#    free RAM  - RAM promised to takes of the last 3 minutes - this job's expected GB  >=  HIL_RAM_RESERVE_GB
#    total CPU load  <  HIL_CPU_MAX percent
#    the job's kind has a free slot (per-kind capacity: unreal / ubt / gpu are single-instance)
#    at least HIL_START_GAP seconds since the previous successful take (so the next gate reads the CPU after the
#    previous job ramped up instead of all waiters passing on the same idle reading)
#    no OLDER waiting ticket could start right now (fairness queue); a ticket older than HIL_STARVE_S holds younger
#    takes back even when it does not fit yet, so running jobs drain until it does (starvation guard, bounded to
#    needs up to HIL_STARVE_MAX_GB so an impossible ticket cannot stall everyone)
#
#  Slots are directories (mkdir is atomic on every file system); owner.txt records pid / agent / job / expected GB.
#  A slot whose owner pid is dead is stale and reclaimed. A waiting take whose caller died gives up (exit 3), so an
#  agent whose tool call timed out never leaves an orphan ticket that blocks the queue.
#
#  Configuration (environment, else harness/swarm/machine.env written by the onboarding interview, else defaults):
#    HIL_RAM_RESERVE_GB  4      GB that must stay free for the operating system and the person at the PC
#    HIL_CPU_MAX         90     do not START a job at or above this total CPU percent
#    HIL_START_GAP       15     seconds between two successful takes
#    HIL_STARVE_S        1800   age after which a waiting ticket holds younger takes back
#    HIL_STARVE_MAX_GB   8      starvation guard applies to needs up to this size
#    HIL_HEAVY_MAX       64     global slot count (RAM and CPU are the real limit)
# ======================================================================================================================
set -u
DIR="$(cd "$(dirname "$0")" && pwd)"

# ---- configuration: machine.env one level up (harness/swarm/machine.env), environment wins
if [ -f "$DIR/../machine.env" ]; then
  # only KEY=VALUE lines with HIL_ keys are read; nothing else in the file is executed
  while IFS='=' read -r k v; do
    case "$k" in HIL_[A-Z_]*) [ -z "${!k:-}" ] && export "$k=${v//[^0-9A-Za-z_.-]/}";; esac
  done < <(grep -E '^HIL_[A-Z_]+=' "$DIR/../machine.env")
fi
RAM_RESERVE=${HIL_RAM_RESERVE_GB:-4}
CPU_MAX=${HIL_CPU_MAX:-90}
START_GAP=${HIL_START_GAP:-15}
STARVE_S=${HIL_STARVE_S:-1800}
STARVE_MAX_GB=${HIL_STARVE_MAX_GB:-8}
HEAVY_MAX=${HIL_HEAVY_MAX:-64}
METER_TTL=10
QDIR="$DIR/.queue"

# ---- per-kind capacity: single-instance kinds are 1, everything else is limited by RAM / CPU only
cap_of() { case "$1" in unreal|ubt|gpu|isaac) echo 1;; *) echo 64;; esac; }

# ---- free RAM in whole GB (Windows CIM via PowerShell; Linux /proc/meminfo MemAvailable)
free_gb() {
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "[int]((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory/1MB)" 2>/dev/null | tr -d '\r'
  else
    awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo
  fi
}

# ---- total CPU load in percent (Windows CIM; Linux: 1-minute load average over the core count)
cpu_pct() {
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "[int]((Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average)" 2>/dev/null | tr -d '\r'
  else
    awk -v n="$(nproc)" '{print int($1 * 100 / n)}' /proc/loadavg
  fi
}

# ---- one shared meter reading for METER_TTL seconds: twenty waiters must not run forty CIM queries per loop
meter() {
  local m="$DIR/.meter" age f c
  age=$(( $(date +%s) - $(stat -c %Y "$m" 2>/dev/null || echo 0) ))
  if [ "$age" -lt "$METER_TTL" ] && [ -s "$m" ]; then cat "$m"; return; fi
  f=$(free_gb); c=$(cpu_pct)
  echo "${f:-0} ${c:-100}" > "$m.$$" && mv -f "$m.$$" "$m"          # unreadable CPU counts as busy: never start blind
  echo "${f:-0} ${c:-100}"
}

# ---- RAM promised to slots taken in the last 3 minutes (the job may not have allocated it yet)
pending_gb() {
  local sum=0 g d now; now=$(date +%s)
  for d in "$DIR"/heavy_*; do                                      # plain glob + stat: no find over anything
    [ -d "$d" ] || continue
    [ $((now - $(stat -c %Y "$d" 2>/dev/null || echo 0))) -lt 180 ] || continue
    g=$(grep -o 'expected_GB=[0-9]*' "$d/owner.txt" 2>/dev/null | head -1 | cut -d= -f2)
    sum=$((sum + ${g:-0}))
  done
  echo "$sum"
}

# ---- is a Windows or POSIX pid alive?
pid_alive() {
  local pid="$1"
  [ -z "$pid" ] && return 0                                        # unknown pid: assume alive, never reclaim on a guess
  if command -v tasklist.exe >/dev/null 2>&1; then
    tasklist.exe //FI "PID eq $pid" 2>/dev/null | grep -q " $pid " && return 0
  fi
  kill -0 "$pid" 2>/dev/null
}

# ---- is the owner recorded in a slot directory alive?
slot_alive() { pid_alive "$(grep -o 'pid=[0-9]*' "$1/owner.txt" 2>/dev/null | head -1 | cut -d= -f2)"; }

# ---- try one slot directory: atomic mkdir, reclaim when the owner is dead
try_slot() {   # $1 slot name, $2 owner line
  local d="$DIR/$1"
  if mkdir "$d" 2>/dev/null; then echo "$2" > "$d/owner.txt"; return 0; fi
  if ! slot_alive "$d"; then
    rm -rf "${d:?}"
    mkdir "$d" 2>/dev/null && { echo "$2" > "$d/owner.txt"; return 0; }
  fi
  return 1
}

# ---- fairness: is there an OLDER live ticket that could start now (or is starving)?
older_runnable() {   # $1 my ticket, $2 free GB, $3 pending GB
  local t name pid tneed tkind tage
  for t in "$QDIR"/*; do
    [ -f "$t" ] || continue
    name=$(basename "$t")
    [[ "$name" < "$(basename "$1")" ]] || continue                # zero-padded epoch first: string order = age order
    pid=$(grep -o 'pid=[0-9]*' "$t" 2>/dev/null | cut -d= -f2)
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then rm -f "$t"; continue; fi   # its waiter died: drop it
    tneed=$(grep -o 'need=[0-9]*' "$t" 2>/dev/null | cut -d= -f2); tneed=${tneed:-4}
    tkind=$(grep -o 'kind=[a-z_]*' "$t" 2>/dev/null | cut -d= -f2)
    tage=$(( $(date +%s) - 10#${name%%_*} ))
    if [ $(($2 - $3 - tneed)) -lt "$RAM_RESERVE" ]; then
      # does not fit: it only holds the queue when it is starving and small enough to ever fit
      [ "$tage" -ge "$STARVE_S" ] && [ "$tneed" -le "$STARVE_MAX_GB" ] || continue
    fi
    if [ "$(cap_of "$tkind")" = "1" ] && [ -d "$DIR/${tkind}_1" ] && slot_alive "$DIR/${tkind}_1"; then continue; fi
    return 0
  done
  return 1
}

cmd="${1:-}"
case "$cmd" in
take)
  if [ $# -lt 5 ]; then echo "usage: slot.sh take <kind> <agent> <job> <expected_GB> [owner_pid]" >&2; exit 2; fi
  kind="$2"; agent="$3"; job="$4"; need="$5"
  case "$kind" in *[!a-z_]*|''|heavy) echo "slot.sh: kind must be lower-case letters and not \"heavy\" (got '$kind')" >&2; exit 2;; esac
  case "$need" in *[!0-9]*|'') echo "slot.sh: expected_GB must be a whole number (got '$need')" >&2; exit 2;; esac
  # ---- whose life the slot follows: an explicit owner pid wins; else the caller of `eval "$(...)"`, which is the
  #      grandparent (the command substitution subshell exits as soon as take returns). Git Bash pids are mapped to
  #      Windows pids so tasklist can see them.
  opid="${6:-}"
  if [ -z "$opid" ]; then
    gp=$(cat "/proc/$PPID/ppid" 2>/dev/null || true)
    if [ -n "$gp" ] && [ "$gp" != "1" ]; then opid="$gp"; else opid="$PPID"; fi
  fi
  wpid=$(cat "/proc/$opid/winpid" 2>/dev/null || true); [ -n "$wpid" ] && opid="$wpid"
  owner="pid=$opid agent=$agent job=$job expected_GB=$need kind=$kind taken=$(date '+%Y-%m-%d %H:%M:%S')"
  mkdir -p "$QDIR"
  ticket="$QDIR/$(printf '%011d' "$(date +%s)")_$$"
  echo "pid=$$ need=$need kind=$kind agent=$agent job=$job" > "$ticket"
  trap 'rm -f "$ticket"' EXIT
  while true; do
    # ---- the caller is gone (tool timeout, closed session): give up instead of becoming an orphan in the queue
    if ! pid_alive "$opid"; then echo "slot.sh: caller pid $opid is gone, dropping the ticket for '$job'" >&2; exit 3; fi
    # ---- start mutex: check + stamp is atomic; a mutex older than 2 min belongs to a dead take and is reclaimed
    if ! mkdir "$DIR/.start_mutex" 2>/dev/null; then
      [ $(( $(date +%s) - $(stat -c %Y "$DIR/.start_mutex" 2>/dev/null || date +%s) )) -gt 120 ] && rmdir "$DIR/.start_mutex" 2>/dev/null
      sleep 3; continue
    fi
    last=$(stat -c %Y "$DIR/.last_start" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - last )) -lt "$START_GAP" ]; then rmdir "$DIR/.start_mutex"; sleep 3; continue; fi
    read -r fg cp <<< "$(meter)"; fg=${fg:-0}; cp=${cp:-100}
    pg=$(pending_gb); pg=${pg:-0}
    if [ $((fg - pg - need)) -ge "$RAM_RESERVE" ] && [ "$cp" -lt "$CPU_MAX" ]; then
      if older_runnable "$ticket" "$fg" "$pg"; then
        rmdir "$DIR/.start_mutex" 2>/dev/null
        echo "slot.sh: gate open but an older waiter goes first ($kind, $need GB)" >&2
        sleep 5; continue
      fi
      h=""; for n in $(seq 1 "$HEAVY_MAX"); do try_slot "heavy_$n" "$owner" && { h="heavy_$n"; break; }; done
      if [ -n "$h" ]; then
        k=""; for n in $(seq 1 "$(cap_of "$kind")"); do try_slot "${kind}_$n" "$owner" && { k="${kind}_$n"; break; }; done
        if [ -n "$k" ]; then
          touch "$DIR/.last_start"; rmdir "$DIR/.start_mutex" 2>/dev/null
          echo "export HIL_SLOT_HEAVY=$h HIL_SLOT_KIND=$k"
          exit 0
        fi
        rm -rf "${DIR:?}/$h"                                       # kind busy: give the global slot back
      fi
    fi
    rmdir "$DIR/.start_mutex" 2>/dev/null
    echo "slot.sh: waiting (free ${fg} GB, pending ${pg} GB, need ${need} + reserve ${RAM_RESERVE}; CPU ${cp} % vs < ${CPU_MAX} %; or ${kind} busy)" >&2
    sleep 10
  done
  ;;
release)
  shift
  for s in "$@"; do
    case "$s" in heavy_[0-9]*|[a-z]*_[0-9]*) rm -rf "${DIR:?}/$s";; '') ;; *) echo "slot.sh: refusing to release '$s'" >&2;; esac
  done
  ;;
status)
  echo "reserve ${RAM_RESERVE} GB, CPU cap ${CPU_MAX} %, start gap ${START_GAP} s; meter (free GB, CPU %): $(meter)"
  for d in "$DIR"/*_[0-9]*; do
    [ -d "$d" ] || continue
    if slot_alive "$d"; then st=live; else st=STALE; fi
    echo "  $(basename "$d") [$st] $(cat "$d/owner.txt" 2>/dev/null)"
  done
  for t in "$QDIR"/*; do [ -f "$t" ] && echo "  queued $(basename "$t"): $(cat "$t")"; done
  ;;
*)
  echo "usage: slot.sh take <kind> <agent> <job> <expected_GB> [owner_pid] | release <slot>... | status" >&2
  exit 2
  ;;
esac
