#!/usr/bin/env bash
# ======================================================================================================================
#  merge_to_dev.sh - lands a lane branch on dev. ALWAYS, even when the lane's gates are red: nothing is lost, and
#                    conflicts between lanes surface the same day instead of at the end. dev is promoted to main only by
#                    promote_dev.sh, and only when dev's CI is green with zero regressions.
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux).
#  Run (inside the LANE'S OWN WORKTREE, never in the owner's main checkout):
#       bash <repo>/harness/swarm/merge_to_dev.sh <agent> [--gate-note "<one line: gate state, e.g. ci red: pcb_drc 3>"]
#
#  Steps:
#    1. refuse to run in the main checkout, on dev or main, or with uncommitted changes (commit first);
#    2. push the lane branch (never forced);
#    3. take MERGE_LOCK (merge_lock.sh);
#    4. fetch, merge origin/dev INTO the lane branch (a conflict stops here: the lane resolves it in its worktree, the
#       lock is released, exit 3);
#    5. push the lane branch again, then push HEAD:dev, which is a fast-forward because origin/dev is now an ancestor;
#    6. append one line to harness/swarm/dev_merges.log (time, agent, branch, sha, gate note);
#    7. release the lock (also on every failure path).
#  Commits use the machine's configured git identity; no attribution lines are added anywhere.
# ======================================================================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
agent="${1:-}"; shift || true
note="gate state not given"
while [ $# -gt 0 ]; do
  case "$1" in --gate-note) note="${2:-}"; shift 2;; *) echo "merge_to_dev.sh: unknown argument $1" >&2; exit 2;; esac
done
if [ -z "$agent" ]; then echo "usage: merge_to_dev.sh <agent> [--gate-note <text>]" >&2; exit 2; fi

# ---- 1. where are we
top="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "merge_to_dev.sh: not inside a git checkout" >&2; exit 2; }
common="$(git rev-parse --path-format=absolute --git-common-dir)"
gitdir="$(git rev-parse --path-format=absolute --git-dir)"
if [ "$common" = "$gitdir" ]; then echo "merge_to_dev.sh: this is the main checkout; run it in your lane's worktree" >&2; exit 2; fi
branch="$(git branch --show-current)"
case "$branch" in ''|dev|main) echo "merge_to_dev.sh: refusing to run on '${branch:-detached HEAD}'" >&2; exit 2;; esac
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then echo "merge_to_dev.sh: uncommitted changes; commit them first" >&2; exit 2; fi

# ---- 2. the lane branch is on the remote before anything else happens
git push -q origin "HEAD:refs/heads/$branch" || { echo "merge_to_dev.sh: push of $branch failed" >&2; exit 1; }

# ---- 3. the lock (released on every exit path below)
bash "$HERE/merge_lock.sh" take "$agent" "$branch" 30 || exit 1
release() { bash "$HERE/merge_lock.sh" release "$agent" >/dev/null; }
trap release EXIT

# ---- 4. bring dev into the lane (conflicts are resolved by the lane, in its own worktree)
git fetch -q origin || { echo "merge_to_dev.sh: fetch failed" >&2; exit 1; }
if git rev-parse -q --verify origin/dev >/dev/null; then
  if ! git merge --no-edit -q origin/dev; then
    git merge --abort 2>/dev/null
    echo "merge_to_dev.sh: CONFLICT merging origin/dev into $branch; resolve in this worktree, commit, run again" >&2
    exit 3
  fi
fi

# ---- 5. publish: lane branch first, then dev as a fast-forward
git push -q origin "HEAD:refs/heads/$branch" || { echo "merge_to_dev.sh: push of $branch failed" >&2; exit 1; }
if ! git push -q origin "HEAD:refs/heads/dev"; then
  echo "merge_to_dev.sh: dev moved during the merge (non fast-forward); run again" >&2
  exit 1
fi

# ---- 6. the merge log (live coordination, not tracked)
sha="$(git rev-parse --short HEAD)"
printf '%s\t%s\t%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$agent" "$branch" "$sha" "$note" >> "$HERE/dev_merges.log"
echo "merge_to_dev.sh: $branch landed on dev at $sha ($note)"
