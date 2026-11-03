#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 usage_statusline.py - Claude Code status line + usage tripwire for long agent sessions.

 Interpreter: any Python >= 3.9 (stdlib only). Wired as the "statusLine" command in .claude/settings.local.json:
     "statusLine": {"type": "command", "command": "python -I harness/swarm/usage_statusline.py"}

 Claude Code pipes a JSON blob describing the session into the status-line command on every render. This script:
   1. keeps the latest blob in harness/swarm/statusline_last.json (atomic replace), so the lead can see what the
      harness actually sends (the schema has changed between versions),
   2. finds the weekly (7-day) utilisation and the 5-hour session utilisation in it: the known key
      rate_limits.seven_day.used_percentage first, then a walk of the whole tree for any weekly / seven_day key holding
      a percentage (a 0..1 "utilization" fraction is scaled to percent),
   3. at or above the threshold creates harness/swarm/HANDOFF_NOW once (with a reason line) and logs the trip. The flag
      arms handoff_hook.sh, which tells the lead and every subagent to stop starting work and write a resume document,
   4. prints one short status line: "<project> | <model> | week 87% | 5h 40% | HANDOFF".

 Thresholds (environment, else harness/swarm/machine.env, else default):
   HIL_HANDOFF_PCT     95    weekly percentage that trips the handoff (101 = never)
   HIL_HANDOFF_5H_PCT  101   5-hour percentage that trips it (off by default: a 5-hour window resets by itself)
   HIL_PROJECT_NAME    the repository folder name
 It never deletes HANDOFF_NOW (the lead removes it after the reset), never reads credentials, never makes a network
 call, and never fails the status line: any error degrades to "week ?".
=======================================================================================================================
"""
import json
import os
import sys
import time

# ---------------------------------------------------------------------------------------------------------------------
#  Console: the status line is rendered by the harness; UTF-8 with replacement keeps a cp1252 console alive
# ---------------------------------------------------------------------------------------------------------------------
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

SWARM = os.path.dirname(os.path.abspath(__file__))                     # harness/swarm
REPO = os.path.abspath(os.path.join(SWARM, "..", ".."))
LAST = os.path.join(SWARM, "statusline_last.json")
FLAG = os.path.join(SWARM, "HANDOFF_NOW")
LOG = os.path.join(SWARM, "usage_watch.log")
MACHINE_ENV = os.path.join(SWARM, "machine.env")


def machine_env():
    """KEY=VALUE pairs of harness/swarm/machine.env (HIL_ keys only); missing or unreadable file = empty dict."""
    out = {}
    try:
        with open(MACHINE_ENV, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("HIL_") and "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def setting(name, default):
    """Environment first, then machine.env, then the default; always a string."""
    v = os.environ.get(name, "").strip()
    if v:
        return v
    return machine_env().get(name, default)


def as_pct(value):
    """A float percentage, or None for anything that is not a sane number (NaN, negative, absurd, text)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f < 0 or f > 1000:
        return None
    return f


def known_pct(blob, window):
    """rate_limits.<window>.used_percentage (the documented spelling), or None."""
    try:
        return as_pct(((blob.get("rate_limits") or {}).get(window) or {}).get("used_percentage"))
    except AttributeError:
        return None


def weekly_pct(blob):
    """Weekly utilisation: the documented key first, then any weekly-looking key anywhere in the tree."""
    pct = known_pct(blob, "seven_day")
    if pct is not None:
        return pct, "rate_limits/seven_day/used_percentage"
    stack = [((), blob)]
    while stack:
        path, node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).lower()
                p = path + (str(k),)
                weekly = any(t in kl for t in ("seven_day", "sevenday", "weekly", "week", "7d"))
                if weekly and isinstance(v, dict):
                    for pk in ("used_percentage", "utilization", "used_pct", "percent", "percentage"):
                        if pk in v:
                            pct = as_pct(v[pk])
                            if pct is not None:
                                if pk == "utilization" and pct <= 1.0:
                                    pct *= 100.0                       # a 0..1 fraction
                                return pct, "/".join(p + (pk,))
                if isinstance(v, (dict, list)):
                    stack.append((p, v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((path + (str(i),), v))
    return None, None


def trip(reason):
    """Create HANDOFF_NOW once, with the reason, and log it. Never raises."""
    if os.path.exists(FLAG):
        return
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(FLAG, "w", encoding="utf-8") as fh:
            fh.write(f"{reason} at {stamp}\n")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{stamp} TRIPPED {reason}\n")
    except OSError:
        pass


def main():
    # ---- read the blob (empty or garbled input is an empty dict, never a crash)
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        blob = json.loads(raw) if raw.strip() else {}
        if not isinstance(blob, dict):
            blob = {}
    except ValueError:
        blob = {}

    # ---- keep the latest blob for inspection (atomic replace)
    try:
        tmp = LAST + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(raw if raw else "{}")
        os.replace(tmp, LAST)
    except OSError:
        pass

    # ---- thresholds
    week_limit = as_pct(setting("HIL_HANDOFF_PCT", "95")) or 95.0
    five_limit = as_pct(setting("HIL_HANDOFF_5H_PCT", "101")) or 101.0

    # ---- tripwires
    week, key = weekly_pct(blob)
    five = known_pct(blob, "five_hour")
    if week is not None and week >= week_limit:
        trip(f"weekly usage {week:.1f}% >= {week_limit:.0f}% ({key})")
    if five is not None and five >= five_limit:
        trip(f"5-hour usage {five:.1f}% >= {five_limit:.0f}%")

    # ---- the status line itself
    try:
        model = (blob.get("model") or {}).get("display_name") or "claude"
    except AttributeError:
        model = "claude"
    project = setting("HIL_PROJECT_NAME", os.path.basename(REPO) or "project")
    parts = [project, str(model), f"week {week:.0f}%" if week is not None else "week ?"]
    if five is not None:
        parts.append(f"5h {five:.0f}%")
    if os.path.exists(FLAG):
        parts.append("HANDOFF")
    print(" | ".join(parts))


if __name__ == "__main__":
    main()
