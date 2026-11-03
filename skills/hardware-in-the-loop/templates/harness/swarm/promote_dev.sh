#!/usr/bin/env bash
# ======================================================================================================================
#  promote_dev.sh - the integrator's dev -> main promotion: a fast-forward of main to a dev commit whose CI matrix is
#                   green with zero regressions against the last report taken on main.
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux) + python (stdlib, for the JSON reports).
#  Run (in the integrator's worktree, checked out at the dev commit that was tested):
#       bash <repo>/harness/swarm/promote_dev.sh <agent> <dev ci_report.json>
#  where the report came from:
#       pwsh -File windows\ci.ps1 -Compare <main ci_report.json> -FailOn regressions
#  run on that same commit.
#
#  Refuses (exit 1) unless ALL of these hold:
#    - the report's git.head is this worktree's HEAD, and HEAD is the tip of origin/dev (nothing untested slips in),
#    - verdict == "green",
#    - compare is present, compare.regressions is empty, and compare.against_head is the current origin/main,
#    - origin/main is an ancestor of HEAD (the push is a fast-forward; main is never rewritten).
#  Then, under MERGE_LOCK, it pushes HEAD:main and records the promotion in harness/swarm/promotions.log.
# ======================================================================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
agent="${1:-}"; report="${2:-}"
if [ -z "$agent" ] || [ -z "$report" ]; then echo "usage: promote_dev.sh <agent> <dev ci_report.json>" >&2; exit 2; fi
if [ ! -f "$report" ]; then echo "promote_dev.sh: report not found: $report" >&2; exit 2; fi
PY="$(command -v python || command -v python3)"
if [ -z "$PY" ]; then echo "promote_dev.sh: python not found" >&2; exit 2; fi

git fetch -q origin || { echo "promote_dev.sh: fetch failed" >&2; exit 1; }
head="$(git rev-parse HEAD)"
dev="$(git rev-parse -q --verify origin/dev)" || { echo "promote_dev.sh: no origin/dev" >&2; exit 1; }
main="$(git rev-parse -q --verify origin/main)" || { echo "promote_dev.sh: no origin/main" >&2; exit 1; }

# ---- read the report (python, so the JSON is parsed, never grepped)
facts="$("$PY" -I -c '
import json, sys
try:
    r = json.load(open(sys.argv[1], encoding="utf-8-sig"))
except Exception as e:
    print("ERR " + str(e).replace(" ", "_")); sys.exit(0)
c = r.get("compare") or {}
regs = c.get("regressions") or []
print((r.get("git") or {}).get("head", ""), r.get("verdict", ""), "yes" if r.get("compare") else "no", len(regs), c.get("against_head") or "")
' "$report")"
read -r rhead verdict hascmp nreg against <<< "$facts"
case "$rhead" in ERR*) echo "promote_dev.sh: unreadable report: $rhead" >&2; exit 1;; esac

fail=0
[ "$rhead" = "$head" ]   || { echo "REFUSED: the report is for ${rhead:-?}, HEAD is $head"; fail=1; }
[ "$head" = "$dev" ]     || { echo "REFUSED: HEAD $head is not the tip of origin/dev ($dev)"; fail=1; }
[ "$verdict" = "green" ] || { echo "REFUSED: verdict is '${verdict:-?}', not green"; fail=1; }
[ "$hascmp" = "yes" ]    || { echo "REFUSED: the report has no -Compare against a main report"; fail=1; }
[ "${nreg:-1}" = "0" ]   || { echo "REFUSED: $nreg regression(s) against main"; fail=1; }
[ "$against" = "$main" ] || { echo "REFUSED: compared against ${against:-nothing}, origin/main is $main"; fail=1; }
git merge-base --is-ancestor "$main" "$head" || { echo "REFUSED: origin/main is not an ancestor of HEAD (not a fast-forward)"; fail=1; }
[ "$fail" = 0 ] || exit 1

# ---- promote under the merge lock
bash "$HERE/merge_lock.sh" take "$agent" "dev->main" 30 || exit 1
trap 'bash "$HERE/merge_lock.sh" release "$agent" >/dev/null' EXIT
git push -q origin "$head:refs/heads/main" || { echo "promote_dev.sh: push to main failed" >&2; exit 1; }
printf '%s\t%s\t%s -> %s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$agent" "${main:0:10}" "${head:0:10}" "$report" >> "$HERE/promotions.log"
echo "promote_dev.sh: main fast-forwarded ${main:0:10} -> ${head:0:10}"
