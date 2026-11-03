#!/usr/bin/env bash
# ======================================================================================================================
#  watch.sh - wait for a detached job and wake the agent on success AND on failure.
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux).
#  Run: bash harness/swarm/watch.sh <logfile> [--hang-min 10] [--timeout-min 0] [--poll-s 30]
#  Paths: command line (the log and its marker files are named by the caller)
#
#  Start it as a background task of the agent session (Claude Code: run_in_background) right after detach.sh, so the
#  session gets a notification when this script exits. It exits on the FIRST of:
#    0  <logfile>.done appeared                         the chain finished green
#    1  <logfile>.FAILED appeared                       the chain exited non-zero (its exit code is printed)
#    3  the log matched a crash pattern (Traceback, assertion, Segmentation fault, "FATAL", a KiCad assert) and the
#       process is gone without a marker
#    4  no log growth for --hang-min minutes (default 10): the job is treated as hung; the pid is printed so the agent
#       that STARTED it can stop it by exact pid (nobody else's process is ever touched)
#    5  the pid in <logfile>.pid is gone and no marker was written (killed, machine sleep, crash)
#    6  --timeout-min elapsed (0 = no overall timeout)
#  On every exit it prints the last 15 log lines, so the wake-up message already holds the evidence.
# ======================================================================================================================
set -u
if [ $# -lt 1 ]; then echo "usage: watch.sh <logfile> [--hang-min N] [--timeout-min N] [--poll-s N]" >&2; exit 2; fi
log="$1"; shift
hang_min=10; timeout_min=0; poll_s=30
while [ $# -gt 0 ]; do
  case "$1" in
    --hang-min) hang_min="${2:-10}"; shift 2;;
    --timeout-min) timeout_min="${2:-0}"; shift 2;;
    --poll-s) poll_s="${2:-30}"; shift 2;;
    *) echo "watch.sh: unknown option $1" >&2; exit 2;;
  esac
done
for v in "$hang_min" "$timeout_min" "$poll_s"; do
  case "$v" in *[!0-9]*|'') echo "watch.sh: numeric options only" >&2; exit 2;; esac
done
# ---- helpers
size_of() { stat -c %s "$1" 2>/dev/null || echo 0; }
pid_alive() {
  local pid="$1"
  [ -z "$pid" ] && return 0
  if command -v tasklist.exe >/dev/null 2>&1; then
    tasklist.exe //FI "PID eq $pid" 2>/dev/null | grep -q " $pid " && return 0 || return 1
  fi
  kill -0 "$pid" 2>/dev/null
}
finish() { echo "---- last lines of $log"; tail -n 15 "$log" 2>/dev/null; exit "$1"; }

start=$(date +%s); last_size=$(size_of "$log"); last_change=$start
pid="$(cat "$log.pid" 2>/dev/null | tr -d '\r\n ')"
echo "watch.sh: watching $log (pid ${pid:-unknown}, hang ${hang_min} min, timeout ${timeout_min} min)"
while true; do
  # ---- markers first: a finished job is reported as finished even if its pid is gone
  if [ -f "$log.done" ]; then echo "DONE: $(cat "$log.done")"; finish 0; fi
  if [ -f "$log.FAILED" ]; then echo "FAILED: $(cat "$log.FAILED")"; finish 1; fi
  now=$(date +%s)
  # ---- log growth (hang detection)
  sz=$(size_of "$log")
  if [ "$sz" != "$last_size" ]; then last_size=$sz; last_change=$now; fi
  if [ $(( now - last_change )) -ge $(( hang_min * 60 )) ]; then
    echo "HUNG: no log growth for ${hang_min} min (pid ${pid:-unknown}; stop it by exact pid if you started it)"; finish 4
  fi
  # ---- process gone without a marker
  if [ -n "$pid" ] && ! pid_alive "$pid"; then
    sleep 2                                                         # the wrapper may be writing its marker right now
    [ -f "$log.done" ] && { echo "DONE: $(cat "$log.done")"; finish 0; }
    [ -f "$log.FAILED" ] && { echo "FAILED: $(cat "$log.FAILED")"; finish 1; }
    if grep -qE 'Traceback \(most recent call last\)|Assertion|assert(ion)? failed|Segmentation fault|FATAL' "$log" 2>/dev/null; then
      echo "CRASHED: pid $pid is gone and the log holds a crash pattern"; finish 3
    fi
    echo "GONE: pid $pid exited without a done / FAILED marker"; finish 5
  fi
  # ---- overall timeout
  if [ "$timeout_min" -gt 0 ] && [ $(( now - start )) -ge $(( timeout_min * 60 )) ]; then
    echo "TIMEOUT: ${timeout_min} min elapsed (pid ${pid:-unknown} still running)"; finish 6
  fi
  sleep "$poll_s"
done
