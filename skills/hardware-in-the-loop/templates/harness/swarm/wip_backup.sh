#!/usr/bin/env bash
# ======================================================================================================================
#  wip_backup.sh - crash insurance for in-flight work: snapshots the UNCOMMITTED state of the main checkout and of every
#                  agent worktree to origin every 20 minutes, without touching anybody's working tree, index or stash.
#
#  Interpreter: bash (Git Bash on Windows, bash 4+ on Linux).
#  Run: bash harness/swarm/wip_backup.sh [interval_s=1200] [--once]
#       (start it detached for the day: bash harness/swarm/detach.sh harness/swarm/wip_backup.sh .tmp/wip_backup.log)
#
#  How a snapshot is made (per checkout):
#    1. a THROWAWAY index (GIT_INDEX_FILE) starts as a copy of the checkout's real index (its stat cache means only
#       changed files are re-hashed),
#    2. `git add -A` into that index (.gitignore respected: scratch, build trees and *.kicad_prl never leave the PC),
#    3. any file over 50 MB is dropped from the snapshot (GitHub refuses blobs over 100 MB; the repo rule is 50 MB),
#    4. if the tree equals HEAD's tree there is nothing to back up,
#    5. a commit is written with TWO parents: the previous backup tip (so the backup branch only ever moves forward)
#       and the checkout's HEAD (so the snapshot diffs cleanly against the work it belongs to),
#    6. it is pushed to refs/heads/backup/wip-<branch name>. Because the previous tip is a parent, every push is a
#       fast-forward: this script NEVER force-pushes, like everything else in the method.
#  Restore a file:  git checkout backup/wip-<name> -- <path>
#  It is insurance, not a substitute for committing and pushing your branch after every green gate.
#  Commits use the machine's configured git identity; nothing else is added to the message.
# ======================================================================================================================
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
INTERVAL=1200; ONCE=0
for a in "$@"; do
  case "$a" in
    --once) ONCE=1;;
    *[!0-9]*|'') echo "wip_backup.sh: unknown argument '$a'" >&2; exit 2;;
    *) INTERVAL="$a";;
  esac
done
LIMIT=$((50 * 1024 * 1024))

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

snapshot() {   # $1 checkout dir, $2 backup name
  local dir="$1" name="$2" idx real tree head ref prev commit
  idx="$(mktemp)"
  real="$(git -C "$dir" rev-parse --path-format=absolute --git-path index 2>/dev/null)"
  if [ -n "$real" ] && [ -f "$real" ]; then
    cp "$real" "$idx" || { rm -f "$idx"; return; }
  else
    GIT_INDEX_FILE="$idx" git -C "$dir" read-tree HEAD || { rm -f "$idx"; return; }
  fi
  GIT_INDEX_FILE="$idx" git -C "$dir" add -A 2>/dev/null
  # ---- drop oversize files from the snapshot (they stay on disk, they just never travel)
  GIT_INDEX_FILE="$idx" git -C "$dir" diff --cached --name-only HEAD 2>/dev/null | while IFS= read -r f; do
    if [ -f "$dir/$f" ] && [ "$(stat -c %s "$dir/$f" 2>/dev/null || echo 0)" -gt "$LIMIT" ]; then
      GIT_INDEX_FILE="$idx" git -C "$dir" rm -q --cached -- "$f" && log "skipped (> 50 MB): $name $f"
    fi
  done
  tree="$(GIT_INDEX_FILE="$idx" git -C "$dir" write-tree)"; rm -f "$idx"
  head="$(git -C "$dir" rev-parse HEAD 2>/dev/null)" || return
  [ -n "$tree" ] || return
  [ "$tree" = "$(git -C "$dir" rev-parse "HEAD^{tree}")" ] && return          # nothing uncommitted
  # ---- previous backup tip as first parent keeps the branch fast-forward only
  ref="backup/wip-$name"
  git -C "$dir" fetch -q origin "+refs/heads/$ref:refs/remotes/origin/$ref" 2>/dev/null
  prev="$(git -C "$dir" rev-parse -q --verify "refs/remotes/origin/$ref" 2>/dev/null)"
  if [ -n "$prev" ] && [ "$(git -C "$dir" rev-parse "$prev^{tree}")" = "$tree" ]; then return; fi   # unchanged since last pass
  if [ -n "$prev" ]; then
    commit="$(git -C "$dir" commit-tree "$tree" -p "$prev" -p "$head" -m "WIP backup of $name at $(date '+%Y-%m-%d %H:%M')")"
  else
    commit="$(git -C "$dir" commit-tree "$tree" -p "$head" -m "WIP backup of $name at $(date '+%Y-%m-%d %H:%M')")"
  fi
  [ -n "$commit" ] || { log "commit-tree failed for $name"; return; }
  if git -C "$dir" push -q origin "$commit:refs/heads/$ref"; then
    log "backed up $name -> $ref ($commit)"
  else
    log "push FAILED for $name -> $ref (left local as $commit)"
  fi
}

while true; do
  # ---- every checkout git knows about: the main one first, then every worktree, named after its branch
  git -C "$REPO" worktree list --porcelain | awk '/^worktree /{sub(/^worktree /, ""); print}' | while IFS= read -r wt; do
    [ -d "$wt" ] || continue
    b="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null | tr '/' '_')"
    [ -n "$b" ] && [ "$b" != "HEAD" ] && snapshot "$wt" "$b"
  done
  [ "$ONCE" = 1 ] && break
  sleep "$INTERVAL"
done
