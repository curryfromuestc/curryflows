#!/usr/bin/env bash
#
# locate-codex.sh -- read-only discovery aid for curryflows (oversee op).
#
# Lists tmux panes that look like a live Codex TUI (live, accepting input -- Codex
# runs on the PRIMARY screen, so that is the structural signal, not alternate
# screen), and recent Codex /goal session transcripts, so a human can confirm the
# exact <session:window.pane> + <codex-session-id> pairing before attaching.
#
# Strictly read-only: no tmux state is mutated, no files are written.
#
# Usage: locate-codex.sh
#
# Env:
#   STEER_CMD_ALLOWLIST   regex of process names treated as Codex (default: codex)
#   CODEX_SESSIONS_DIR    sessions root (default: ~/.codex/sessions)

set -uo pipefail

CMD_ALLOWLIST="${STEER_CMD_ALLOWLIST:-codex}"
SESSIONS_DIR="${CODEX_SESSIONS_DIR:-$HOME/.codex/sessions}"

# ---- candidate tmux panes ---------------------------------------------------

echo "== Candidate tmux panes =="
if ! tmux info >/dev/null 2>&1; then
  echo "  (no tmux server running)"
else
  fmt='#{session_name}:#{window_index}.#{pane_index}|#{pane_current_command}|#{alternate_on}|#{pane_in_mode}|#{pane_dead}|#{pane_tty}|#{pane_current_path}'
  tmux list-panes -a -F "$fmt" 2>/dev/null | while IFS='|' read -r target cmd alt inmode dead tty cwd; do
    # Codex runs on the primary screen (alt=0), so "live + accepting input" is
    # the structural signal, NOT alternate-screen.
    live="-"
    [ "$dead" = "0" ] && [ "$inmode" = "0" ] && live="live"
    # process-identity hint from the foreground command or the pane's tty
    hint="-"
    if printf '%s' "$cmd" | grep -qiE "$CMD_ALLOWLIST"; then
      hint="codex?"
    else
      t="${tty#/dev/}"
      if [ -n "$t" ] && ps -t "$t" -o args= 2>/dev/null | grep -qiE "$CMD_ALLOWLIST"; then
        hint="codex?"
      fi
    fi
    # surface panes that are a Codex-process hit, or otherwise a live pane
    [ "$hint" = "-" ] && [ "$live" = "-" ] && continue
    printf '  %-24s cmd=%-12s [%s %s]\n      cwd=%s\n' \
      "$target" "$cmd" "$live" "$hint" "$cwd"
  done
fi

# ---- recent Codex /goal transcripts -----------------------------------------

echo
echo "== Recent Codex session transcripts ($SESSIONS_DIR) =="
if [ ! -d "$SESSIONS_DIR" ]; then
  echo "  (sessions dir not found)"
else
  # newest 12 rollout files; show session id, mtime, last goal state if present
  find "$SESSIONS_DIR" -name 'rollout-*.jsonl' -printf '%T@|%p\n' 2>/dev/null \
    | sort -rn | head -12 | while IFS='|' read -r ts path; do
      id="$(basename "$path" | grep -oE '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1)"
      when="$(date -d "@${ts%.*}" '+%Y-%m-%d %H:%M' 2>/dev/null || echo '?')"
      # best-effort last goal state (thread_goal_updated payloads)
      state="$(grep -o '"state":"[a-zA-Z_]*"' "$path" 2>/dev/null | tail -1 | sed -E 's/.*:"([a-zA-Z_]+)".*/\1/')"
      printf '  id=%s  updated=%s  goal-state=%s\n      %s\n' \
        "${id:-?}" "$when" "${state:-?}" "$path"
    done
fi

echo
echo "Pick the pane whose cwd matches the transcript's project, then register it"
echo "to the board so the coordinator's per-tick reviewers audit it (oversee op):"
echo "  curryflows oversee <session-id> <session:window.pane>"
