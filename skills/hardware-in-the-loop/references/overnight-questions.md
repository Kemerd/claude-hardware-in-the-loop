# Overnight session: the detached chain and the morning questions

Before the owner leaves for the night, the lead does two things: it prepares a detached chain that keeps the machine
busy with long, safe work, and it writes the questions the owner will answer over coffee. Neither waits for the other:
the night runs on the recommended defaults, and every default taken is recorded.

## 1. The night chain

`harness/swarm/night_<date>.sh`, started with `bash harness/swarm/detach.sh harness/swarm/night_<date>.sh
.tmp/night_<date>/chain.log`, watched with `bash harness/swarm/watch.sh .tmp/night_<date>/chain.log --hang-min 20`.

```bash
#!/usr/bin/env bash
# Run: bash harness/swarm/night_<date>.sh   (detached; see overnight-questions.md)
set -u
cd "$(dirname "$0")/../.."                          # repo root (main checkout or the integrator worktree)
step() {   # step <name> <expected_GB> <timeout_min> <command...>: one slot per step, released even on failure
  local name="$1" gb="$2" tmo="$3"; shift 3
  eval "$(bash harness/swarm/locks/slot.sh take build night "$name" "$gb" $$)" || return 1
  echo "=== $(date '+%H:%M') start $name"
  timeout "${tmo}m" "$@"; local rc=$?
  bash harness/swarm/locks/slot.sh release "$HIL_SLOT_HEAVY" "$HIL_SLOT_KIND"
  echo "=== $(date '+%H:%M') end $name rc=$rc"
  return $rc
}
fails=0
step ci_dev      6  90 pwsh -NoProfile -File windows/ci.ps1 -Out .tmp/night_<date>/ci || fails=$((fails+1))
step board_a     8 120 "<KiCad python>" pcb/board_a/gen_board_a.py --out pcb/board_a || fails=$((fails+1))
step renders     4  60 python -I cad/render.py --parts-json cad/asm/parts.json --stl-dir cad/asm/stl --out docs/cad_decisions/<date>/asm/overview.png || fails=$((fails+1))
echo "night chain: $fails failed step(s)"
exit $fails
```

Rules for the chain: independent steps keep going after a failure (count them, exit non-zero at the end); every step
has a slot, a timeout and a log line with start and end; nothing in it needs a human; nothing launches Unreal or the
GPU unless the owner said so for this night; outputs go to `.tmp/night_<date>/` and results to `docs/data/`.

## 2. The morning questions

Appended to the plan as "## MORNING QUESTIONS (collected <time>; defaults in brackets, the night proceeds on them)".
Each question is self-contained: the owner should not have to open a file to answer it.

```markdown
## MORNING QUESTIONS for the owner (collected 22:40; the night proceeds on the defaults)

1. <Topic>: <the decision in one sentence>.
   Context: <the numbers that make it a question, with paths: docs/data/<topic>/<date>/summary.csv, image path>.
   Options: (a) <option, consequence, cost> (b) <option, consequence, cost> (c) <...>
   Recommended: (a), because <one line>.
   Assumed tonight: (a) <what was done on that basis, so the owner sees what changes if the answer differs>.
```

In the morning: ask them in the question dialog in batches of up to four, recommended option first. Answers go into
`AGENT_RULES.md` "Standing owner decisions" with the date; a default that is overridden gets its follow-up step in
the plan.
