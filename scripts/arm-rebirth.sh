#!/usr/bin/env bash
#
# arm-rebirth.sh -- schedule (and later fire) the coordinator's own /clear.
#
# curryflows runs the coordinator on a fresh context per tick: a session-scoped
# cron re-injects the tick prompt, and the LAST step of every tick arms this
# script so that, shortly after the turn ends, the tmux SERVER types /clear into
# the coordinator's own pane. The next cron firing then lands on an empty
# context. There is deliberately NO resident watcher daemon: the one-shot is
# handed to the tmux server (`tmux run-shell -b`), which outlives the arming
# turn and the /clear itself -- the same survival mechanism detached codex
# workers rely on. (Plain `nohup`/`&` backgrounding is killed by the tool
# sandbox; handing the job to the tmux server is not.)
#
# Subcommands:
#   arm  --pane <target> --project <dir> [--delay <s>]   schedule a one-shot fire
#   fire --pane <target> --project <dir>                  guards, then send /clear
#
# fire guards (all must pass, else skip -- a skipped clear is SAFE: the next
# tick simply starts on the previous context and re-arms at its end):
#   * pane resolves and is not dead
#   * pane process matches REBIRTH_CMD_ALLOWLIST (never type into a plain shell)
#   * pane is not in copy/view mode (keys would act as copy-mode commands)
#   * pane is not mid-turn (busy marker visible)
#   * no pause file at <project>/.curryflows/pause (human takeover switch)
#
# There is no post-fire verification by design: /clear is the most misfire-
# tolerant payload in the system (all cross-tick state lives on the board), and
# a swallowed clear self-heals one tick later. Every outcome is appended to
# <project>/.curryflows/temp/rebirth.log so tick step-0 can self-audit liveness.
#
# Exit codes:
#   0  armed (arm) / fired (fire)
#  10  pane missing or dead
#  11  pane process not in allowlist (refused; never types into a shell)
#  20  skipped: pane busy (in-flight turn)
#  21  skipped: pause file present
#  22  skipped: pane in copy/view mode
#  64  usage / input error
#
# Tunables (env):
#   REBIRTH_CMD_ALLOWLIST  regex of pane process names treated as the
#                          coordinator TUI (default: claude|node)
#   REBIRTH_BUSY_RE        busy-marker regex looked up in the visible pane
#                          (default: esc to interrupt)

set -uo pipefail

ALLOWLIST="${REBIRTH_CMD_ALLOWLIST:-claude|node}"
BUSY_RE="${REBIRTH_BUSY_RE:-esc to interrupt}"
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"

err() { printf '%s\n' "arm-rebirth: $*" >&2; }

usage() {
  err "usage: arm-rebirth.sh {arm|fire} --pane <target> --project <dir> [--delay <s>]"
  exit 64
}

log_outcome() { # $1=project $2=message ; best effort, never fatal
  local dir="$1/.curryflows/temp"
  mkdir -p "$dir" 2>/dev/null || return 0
  printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$2" >> "$dir/rebirth.log" 2>/dev/null || true
}

do_fire() { # $1=pane $2=project
  local pane="$1" project="$2" meta dead inmode cmd
  meta="$(tmux display-message -p -t "$pane" \
'#{pane_dead}
#{pane_in_mode}
#{pane_current_command}' 2>/dev/null)"
  if [ -z "$meta" ]; then
    err "target '$pane' does not resolve"
    log_outcome "$project" "skip pane-missing pane=$pane"
    return 10
  fi
  { read -r dead; read -r inmode; read -r cmd; } <<EOF
$meta
EOF
  if [ "$dead" = "1" ]; then
    err "pane is dead"
    log_outcome "$project" "skip pane-dead pane=$pane"
    return 10
  fi
  if ! printf '%s' "$cmd" | grep -qiE "$ALLOWLIST"; then
    err "pane process '$cmd' is not the coordinator TUI per allowlist '$ALLOWLIST' -- refusing"
    log_outcome "$project" "refuse not-coordinator cmd=$cmd pane=$pane"
    return 11
  fi
  if [ "$inmode" = "1" ]; then
    err "pane is in copy/view mode -- keys would be eaten; skipping this cycle"
    log_outcome "$project" "skip copy-mode pane=$pane"
    return 22
  fi
  if [ -f "$project/.curryflows/pause" ]; then
    err "pause file present -- human takeover; skipping"
    log_outcome "$project" "skip paused pane=$pane"
    return 21
  fi
  if tmux capture-pane -p -t "$pane" 2>/dev/null | grep -qiE "$BUSY_RE"; then
    err "pane is mid-turn (busy marker) -- skipping this cycle"
    log_outcome "$project" "skip busy pane=$pane"
    return 20
  fi
  tmux send-keys -t "$pane" -l '/clear' 2>/dev/null || { err "send-keys failed"; return 10; }
  sleep 0.3
  tmux send-keys -t "$pane" Enter 2>/dev/null || { err "send-keys Enter failed"; return 10; }
  log_outcome "$project" "fired pane=$pane"
  printf 'fired pane=%s\n' "$pane"
  return 0
}

do_arm() { # $1=pane $2=project $3=delay
  local pane="$1" project="$2" delay="$3"
  # Validate now, fail-closed: a one-shot that can never fire is a silent stall.
  # NOTE: on bad targets tmux display-message can exit 0 with empty output
  # (observed on 3.2a), so resolution is judged by output, not exit code.
  if [ -z "$(tmux display-message -p -t "$pane" '#{pane_id}' 2>/dev/null)" ]; then
    err "target '$pane' does not resolve -- refusing to arm"
    return 10
  fi
  [ -d "$project" ] || { err "project dir not found: $project"; return 64; }
  # The allowlist in effect now is serialized into the one-shot: run-shell
  # executes under the tmux server's environment, not the caller's.
  tmux run-shell -b \
    "sleep $delay; REBIRTH_CMD_ALLOWLIST='$ALLOWLIST' REBIRTH_BUSY_RE='$BUSY_RE' exec bash '$SELF' fire --pane '$pane' --project '$project'" \
    2>/dev/null || { err "tmux run-shell failed (is a tmux server running?)"; return 10; }
  log_outcome "$project" "armed delay=${delay}s pane=$pane"
  printf 'armed pane=%s delay=%ss\n' "$pane" "$delay"
  return 0
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in arm|fire) ;; -h|--help|help|"") usage ;; *) err "unknown subcommand: $sub"; usage ;; esac

  local pane="" project="" delay=20
  while [ $# -gt 0 ]; do
    case "$1" in
      --pane)    pane="${2:-}"; shift 2 || usage ;;
      --project) project="${2:-}"; shift 2 || usage ;;
      --delay)   delay="${2:-}"; shift 2 || usage ;;
      *) err "unknown option: $1"; usage ;;
    esac
  done
  [ -n "$pane" ] && [ -n "$project" ] || usage
  case "$delay" in ''|*[!0-9]*) err "--delay must be a non-negative integer"; usage ;; esac

  case "$sub" in
    arm)  do_arm "$pane" "$project" "$delay" ;;
    fire) do_fire "$pane" "$project" ;;
  esac
}

main "$@"
