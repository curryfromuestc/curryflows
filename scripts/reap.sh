#!/usr/bin/env bash
# curryflows: reap a finished/orphan worker's resources.
#
# Resource reaping is a per-tick HARD duty of the operator subagent, not a
# hoped-for cleanup hook (see references/operator-spec.md). This reclaims, in
# order: the detached tmux session, the git worktree, and the curryflows branch.
#
# It acts ONLY on what is passed and refuses dangerous targets (the project root
# as a worktree, the current/main/master branch). The coordinator must already
# have judged the target reclaimable (finished, or human-resolved) before calling
# this -- reap.sh does NOT decide; it executes.
#
# Usage:
#   reap.sh [--session <tmux-session>] [--worktree <path>] \
#           [--branch <name>] [--project <git-dir>] [--dry-run]
#
# Exit codes:
#   0  all requested reclaims succeeded (or dry-run)
#   10 tmux kill-session failed
#   20 git worktree remove/prune failed
#   30 branch delete failed
#   64 usage / unsafe-target error
set -uo pipefail

SESSION="" ; WORKTREE="" ; BRANCH="" ; PROJECT="" ; DRY=0
fail=0

usage() { sed -n '2,30p' "$0" >&2; exit 64; }

while [ $# -gt 0 ]; do
  case "$1" in
    --session)  SESSION="${2:-}"; shift 2 ;;
    --worktree) WORKTREE="${2:-}"; shift 2 ;;
    --branch)   BRANCH="${2:-}"; shift 2 ;;
    --project)  PROJECT="${2:-}"; shift 2 ;;
    --dry-run)  DRY=1; shift ;;
    -h|--help)  usage ;;
    *) echo "reap.sh: unknown arg: $1" >&2; usage ;;
  esac
done

if [ -z "$SESSION" ] && [ -z "$WORKTREE" ] && [ -z "$BRANCH" ]; then
  echo "reap.sh: nothing to reap (need --session/--worktree/--branch)" >&2
  usage
fi

run() {  # echo + execute, honoring --dry-run
  echo "reap: $*" >&2
  if [ "$DRY" -eq 1 ]; then return 0; fi
  "$@"
}

# git dir defaults to the worktree's repo if --project not given
gitdir() {
  if [ -n "$PROJECT" ]; then echo "$PROJECT"; else echo "${WORKTREE:-.}"; fi
}

# 1) tmux session
if [ -n "$SESSION" ]; then
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    if ! run tmux kill-session -t "$SESSION"; then
      echo "reap: FAILED to kill tmux session $SESSION" >&2; fail=10
    fi
  else
    echo "reap: tmux session $SESSION not present (already gone)" >&2
  fi
fi

# 2) worktree (refuse the repo root / nonexistent / non-curryflows path is fine)
if [ -n "$WORKTREE" ]; then
  GD="$(gitdir)"
  ROOT="$(git -C "$GD" rev-parse --show-toplevel 2>/dev/null || true)"
  ABS_WT="$(cd "$WORKTREE" 2>/dev/null && pwd || echo "$WORKTREE")"
  if [ -n "$ROOT" ] && [ "$ABS_WT" = "$ROOT" ]; then
    echo "reap: REFUSING to remove the project root as a worktree: $ABS_WT" >&2
    fail=64
  else
    if [ -d "$WORKTREE" ]; then
      run git -C "$GD" worktree remove --force "$WORKTREE" || {
        echo "reap: FAILED worktree remove $WORKTREE" >&2; fail=20; }
    else
      echo "reap: worktree path $WORKTREE absent; pruning stale refs" >&2
    fi
    run git -C "$GD" worktree prune || { echo "reap: FAILED worktree prune" >&2; fail=20; }
  fi
fi

# 3) branch (never current / main / master; only curryflows/* expected)
if [ -n "$BRANCH" ]; then
  GD="$(gitdir)"
  CUR="$(git -C "$GD" branch --show-current 2>/dev/null || true)"
  case "$BRANCH" in
    main|master) echo "reap: REFUSING to delete protected branch $BRANCH" >&2; fail=64 ;;
    *)
      if [ "$BRANCH" = "$CUR" ]; then
        echo "reap: REFUSING to delete the current branch $BRANCH" >&2; fail=64
      else
        run git -C "$GD" branch -D "$BRANCH" || {
          echo "reap: FAILED branch delete $BRANCH" >&2; fail=30; }
      fi
      ;;
  esac
fi

exit "$fail"
