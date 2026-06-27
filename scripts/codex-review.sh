#!/usr/bin/env bash
#
# codex-review.sh -- bounded codex review lane (the cross-model "codex leg").
#
# Launches codex in a fresh tmux pane WITH the review prompt seeded as codex's
# initial positional PROMPT argument (`codex [OPTIONS] [PROMPT]`), so codex starts
# working immediately. This avoids the racy "type into a live TUI" injection: the
# send-keys line only references the prompt FILE name (`"$(cat <file>)"`), and the
# pane's own shell reads the multi-line content as one argument. codex stays in
# tmux (survives SSH reconnect); no /goal, no overseer -- this is BOUNDED mode.
# The review writes its findings to a declared file (file-deliverable pattern =
# structured output without scraping the TUI); we wait for that file to stabilize.
#
# Usage:
#   codex-review.sh --cwd <dir> --prompt-file <f> --out <findings-path>
#                   [--name <tmux-session>] [--effort low|medium|high|xhigh]
#                   [--timeout <s>] [--keep]
#
# Exit codes:
#   0  findings written to --out (path echoed to stdout)
#   10 codex never launched (pane died immediately, no findings)
#   30 timed out waiting for the findings file
#   64 usage / input error
set -uo pipefail

NAME=""
CWD=""
PROMPT_FILE=""
OUT=""
EFFORT="medium"
TIMEOUT=1800
KEEP=0
FULL=""

die() { echo "codex-review: $*" >&2; exit 64; }

while [ $# -gt 0 ]; do
  case "$1" in
    --name)          NAME="$2"; shift 2;;
    --cwd)           CWD="$2"; shift 2;;
    --prompt-file)   PROMPT_FILE="$2"; shift 2;;
    --out)           OUT="$2"; shift 2;;
    --effort)        EFFORT="$2"; shift 2;;
    --timeout)       TIMEOUT="$2"; shift 2;;
    --ready-timeout) shift 2;;   # accepted for compat, unused (launch-with-prompt)
    --keep)          KEEP=1; shift;;
    *) die "unknown arg: $1";;
  esac
done

[ -n "$CWD" ] && [ -d "$CWD" ] || die "--cwd must be an existing directory"
[ -n "$PROMPT_FILE" ] && [ -f "$PROMPT_FILE" ] || die "--prompt-file must be a file"
[ -n "$OUT" ] || die "--out is required"
command -v codex >/dev/null 2>&1 || die "codex CLI not on PATH"
command -v tmux  >/dev/null 2>&1 || die "tmux not on PATH"

[ -n "$NAME" ] || NAME="cfx_rev_$$"
OUT_ABS="$(cd "$(dirname "$OUT")" 2>/dev/null && pwd)/$(basename "$OUT")"
[ -n "$OUT_ABS" ] || die "cannot resolve --out parent directory"
rm -f "$OUT_ABS"

# Compose the full prompt: the task + a strict bounded-review output contract.
FULL="$(mktemp /tmp/cfx_review_prompt.XXXXXX)"
{
  cat "$PROMPT_FILE"
  cat <<EOF

## Hard output contract (curryflows bounded review lane)
- This is a READ-ONLY review. Do NOT edit, create, or delete any repository file.
- Write your complete findings as markdown to EXACTLY this path: $OUT_ABS
- That findings file is your ONLY permitted write. Use file:line evidence and, for
  any claimed defect, a concrete reproducer or a specific test gap; rank findings.
- When the findings file is fully written, stop. Do not start new work.
EOF
} > "$FULL"

cleanup() {
  [ "$KEEP" -eq 0 ] && tmux kill-session -t "$NAME" 2>/dev/null || true
  rm -f "$FULL" 2>/dev/null || true
}

# 1. launch codex in a detached tmux pane WITH the prompt seeded.
tmux new-session -d -s "$NAME" -c "$CWD" 2>/dev/null || die "tmux new-session failed (name in use?)"
PANE="$(tmux list-panes -t "$NAME" -F '#{session_name}:#{window_index}.#{pane_index}' 2>/dev/null | head -1)"
[ -n "$PANE" ] || { cleanup; die "could not resolve pane for session $NAME"; }

# Only the prompt FILE NAME appears in send-keys; the pane's shell reads the
# multi-line content via command substitution (robust to quotes/newlines/CJK).
tmux send-keys -t "$PANE" "codex --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=$EFFORT \"\$(cat $FULL)\"" Enter

# 2. wait for the findings file to appear and stabilize; short-circuit if codex
#    exits before producing it (pane command no longer codex + no file).
STABLE_NEEDED=3
INTERVAL=5
prev=-1
stable=0
gone=0
elapsed=0
# give codex a moment to take over the pane
sleep 5
while [ "$elapsed" -lt "$TIMEOUT" ]; do
  if [ -f "$OUT_ABS" ]; then
    cur="$(wc -c < "$OUT_ABS" 2>/dev/null || echo 0)"
    if [ "$cur" -gt 0 ] && [ "$cur" -eq "$prev" ]; then
      stable=$((stable + 1))
      if [ "$stable" -ge "$STABLE_NEEDED" ]; then
        cleanup
        echo "$OUT_ABS"
        exit 0
      fi
    else
      stable=0
    fi
    prev="$cur"
  else
    # liveness: codex runs as a `node` process, so a live codex pane shows
    # pane_current_command=node. Only treat it as crashed when the pane has
    # returned to a SHELL (codex exited) or the session is gone, with no file.
    panecmd="$(tmux list-panes -t "$NAME" -F '#{pane_current_command}' 2>/dev/null | head -1)"
    if [ -z "$panecmd" ] || printf '%s' "$panecmd" | grep -qiE '^(bash|sh|zsh|fish|dash|-bash)$'; then
      gone=$((gone + 1))
      if [ "$gone" -ge 3 ]; then
        echo "codex-review: codex exited to a shell before writing findings (pane cmd='$panecmd')" >&2
        cleanup
        exit 10
      fi
    else
      gone=0
    fi
  fi
  sleep "$INTERVAL"
  elapsed=$((elapsed + INTERVAL))
done

echo "codex-review: timed out after ${TIMEOUT}s waiting for $OUT_ABS" >&2
cleanup
exit 30
