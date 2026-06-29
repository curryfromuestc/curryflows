#!/usr/bin/env bash
#
# interrupt-target.sh -- soft-stop a Codex CLI /goal run in a specific tmux pane
# (addressed as session:window.pane) by sending a single Escape, then verify the
# pane left its Working / streaming state and is back at an idle composer.
#
# This is one of the only two sanctioned WRITES the overseer makes to the target
# (the other is the human-directed instruction via inject-steer.sh). Escape is a
# SOFT interrupt: the Codex process stays alive and the goal context is intact;
# it only stops the in-flight turn so a human can review and re-instruct. The
# script refuses to act on anything that is not a verified live Codex TUI, which
# is what stops an Escape from landing in a plain shell or the monitor's own
# Claude pane.
#
# Usage:
#   interrupt-target.sh <pane>
#
# Exit codes:
#   0  interrupted: pane is a live Codex TUI and is no longer Working
#   10 target missing or dead
#   11 target does not look like a live Codex TUI
#   20 pane busy / capture not stable enough to read a verdict
#   40 still Working after the Escape (soft stop did not take)
#   64 usage / input error
#
# Tunables (env), shared with inject-steer.sh where they overlap:
#   STEER_CMD_ALLOWLIST   regex of pane process names treated as Codex (default: codex)
#   STEER_REQUIRE_CODEX   require the pane to be a Codex process (default 1; 0 relaxes)
#   STEER_PROMPT_GLYPH    input-box prompt glyph (default U+203A)
#   STEER_EVIDENCE_DIR    base dir for evidence (default ./temp/curryflows)
#   INTERRUPT_SETTLE_TRIES  post-Escape poll attempts for "no longer Working" (default 40, ~6s)
#   INTERRUPT_WORKING_RE    regex marking the streaming/Working state (default below)

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Reuse the hardened target health-check and capture helpers. inject-steer.sh
# guards its dispatcher behind a BASH_SOURCE==$0 check, so sourcing only loads
# functions (verify_target, _capture, _meta, _save, _evidence_dir, _norm ...).
# shellcheck source=inject-steer.sh
source "$HERE/inject-steer.sh"

SETTLE_TRIES="${INTERRUPT_SETTLE_TRIES:-40}"
# Codex renders an active turn with a "Working" status line and an "Esc to
# interrupt" affordance; both vanish once the turn is stopped and the composer is
# idle. Either marker present means the pane is still streaming. The regex is a
# tunable so a Codex wording change is a one-line override, not a code edit.
WORKING_RE="${INTERRUPT_WORKING_RE:-Working|Esc to interrupt|esc to interrupt}"

usage() {
  err "usage: interrupt-target.sh <pane>"
  exit 64
}

# True (0) if the captured pane currently shows a Working / streaming marker.
_is_working() { # $1=pane
  local cap
  cap="$(_capture "$1" | _norm)"
  [ -n "$cap" ] || return 1
  printf '%s' "$cap" | grep -qiE "$WORKING_RE"
}

do_interrupt() { # $1=pane
  local pane="$1" vt i

  # Identify the target first: a live Codex TUI, not dead, not in copy mode, input
  # on. verify_target prints its own diagnosis and returns the precise code; we
  # surface that code unchanged so callers branch on 10 (gone) vs 11 (not Codex).
  vt="$(verify_target "$pane")"; rc=$?
  printf '%s\n' "$vt"
  [ "$rc" -eq 0 ] || return "$rc"

  [ -n "$EVID" ] || _evidence_dir >/dev/null
  _meta "$pane"   | _save target.txt
  _capture "$pane" | _save before-esc.txt

  # Single soft stop. No bracketed paste, no Enter -- just the interrupt key.
  if ! tmux send-keys -t "$pane" Escape 2>/dev/null; then
    err "send-keys Escape failed"
    return 20
  fi

  # Watch the Working / streaming markers disappear. Success the moment the pane
  # reads idle; otherwise classify at timeout.
  for ((i=0; i<SETTLE_TRIES; i++)); do
    sleep "$POLL"
    if ! _is_working "$pane"; then
      _capture "$pane" | _save after-esc.txt
      printf 'interrupted pane=%s state=idle\n' "$pane"
      return 0
    fi
  done

  _capture "$pane" | _save after-esc.txt
  err "pane still shows a Working/streaming state after Escape -- soft stop did not take"
  return 40
}

main() {
  [ $# -ge 1 ] || usage
  case "${1:-}" in
    -h|--help|help) usage ;;
  esac
  do_interrupt "$1"
}

# Allow sourcing for unit tests without executing the dispatcher.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
