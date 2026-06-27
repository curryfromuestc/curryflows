#!/usr/bin/env bash
#
# inject-steer.sh -- robust, mechanical steer injection into a Codex CLI TUI
# running in a specific tmux pane (addressed as session:window.pane).
#
# This exists so the inject dance is DETERMINISTIC, not improvised by an LLM
# each time. A TUI's render timing is racy; an observed failure mode is that the
# first Enter after a large paste silently does not register. This script types,
# verifies, submits, and verifies with bounded retries and structured exit codes.
#
# Subcommands (text is ALWAYS passed via a file, never argv, so CJK / quotes /
# parentheses / newlines are never re-parsed by a shell):
#
#   verify-target <pane>          non-mutating health check of the target pane
#   type    <pane> <file>         paste file into the input box, no Enter, verify
#   submit  <pane> [file]         press Enter, verify submission, bounded retry
#   send    <pane> <file>         atomic type + submit (automated path, no pause)
#
# Exit codes:
#   0  success
#   10 target missing or dead
#   11 target does not look like a live Codex TUI
#   20 pane busy / not stable
#   30 paste command failed
#   31 text-landed verification failed
#   40 submit verification failed after bounded retries
#   41 ambiguous post-Enter state, retry suppressed
#   64 usage / input error
#
# Collapsed bracketed paste: Codex's TUI replaces a LONG paste in its composer
# with a single placeholder line, e.g. "[Pasted Content 1097 chars]", instead of
# echoing the literal characters near the input. The full text is still staged
# correctly (the paste SUCCEEDED) -- only the render is collapsed. So `type` and
# `submit` accept that placeholder, when its advertised char count is in the
# right ballpark for the pasted file, as proof the text landed / was sent. Short
# pastes still render inline and use the literal tail-signature path. Char counts
# are Unicode codepoints (what Codex counts), not bytes, so CJK is measured
# correctly. The bracketed-paste mechanism and the exit-code contract above are
# unchanged.
#
# Tunables (env):
#   STEER_CMD_ALLOWLIST   regex of pane process names treated as Codex (default: codex)
#   STEER_STABLE_TRIES    pane-stability poll attempts (default 20, ~3s)
#   STEER_STABLE_SCOPE    stability scope: input (default, composer-only) | pane
#   STEER_LAND_TRIES      text-landed poll attempts   (default 40, ~6s)
#   STEER_SUBMIT_TRIES    per-Enter submit poll attempts (default 15, ~2.25s)
#   STEER_MAX_ENTERS      max Enter presses (default 2)
#   STEER_SIG_LEN         tail-signature length in normalized chars (default 40)
#   STEER_REQUIRE_CODEX   require the pane to be a Codex process (default 1; 0 relaxes)
#   STEER_PROMPT_GLYPH    input-box prompt glyph used to find the live input line (default U+203A)
#   STEER_EVIDENCE_DIR    base dir for evidence (default ./temp/codex-goal-overseer)

set -uo pipefail

POLL=0.15
STABLE_TRIES="${STEER_STABLE_TRIES:-20}"
LAND_TRIES="${STEER_LAND_TRIES:-40}"
SUBMIT_TRIES="${STEER_SUBMIT_TRIES:-15}"
MAX_ENTERS="${STEER_MAX_ENTERS:-2}"
SIG_LEN="${STEER_SIG_LEN:-40}"
CMD_ALLOWLIST="${STEER_CMD_ALLOWLIST:-codex}"
REQUIRE_CODEX="${STEER_REQUIRE_CODEX:-1}"
PROMPT_GLYPH="${STEER_PROMPT_GLYPH:-$'›'}"
STABLE_SCOPE="${STEER_STABLE_SCOPE:-input}"

err() { printf '%s\n' "inject-steer: $*" >&2; }

usage() {
  err "usage: inject-steer.sh {verify-target|type|submit|send} <pane> [file]"
  exit 64
}

# ---- evidence ---------------------------------------------------------------

EVID=""
_evidence_dir() {
  local base="${STEER_EVIDENCE_DIR:-./temp/codex-goal-overseer}"
  local stamp; stamp="$(date +%Y%m%d-%H%M%S)-$$"
  if mkdir -p "$base/steer-$stamp" 2>/dev/null; then
    EVID="$base/steer-$stamp"
  else
    EVID="$(mktemp -d 2>/dev/null || echo /tmp/steer-$stamp)"
    mkdir -p "$EVID" 2>/dev/null || true
  fi
  printf '%s' "$EVID"
}
_save() { # $1=name ; stdin -> evidence file (best effort)
  [ -n "$EVID" ] || return 0
  cat > "$EVID/$1" 2>/dev/null || true
}

# ---- capture + normalize ----------------------------------------------------

_capture() { # $1=pane -> visible pane text (joined wraps)
  tmux capture-pane -p -J -t "$1" 2>/dev/null
}
_capture_tail() { # $1=pane $2=rows -> bottom rows (fallback input region)
  tmux capture-pane -p -J -t "$1" 2>/dev/null | tail -n "${2:-12}"
}

# The live input box, normalized. Codex echoes a just-submitted message into the
# conversation area (also prompt-prefixed), so "bottom N rows" is too coarse --
# it catches that echo. The live input box is the LAST prompt-glyph line; before
# submit it holds the typed text, after submit it is the empty placeholder. With
# -J the wrapped input collapses to one logical line, so the signature is intact.
_input_line() { # $1=pane -> normalized content of the live input line
  local cap line
  cap="$(tmux capture-pane -p -J -t "$1" 2>/dev/null)"
  line="$(printf '%s\n' "$cap" | grep -- "$PROMPT_GLYPH" | tail -1)"
  [ -n "$line" ] || line="$(printf '%s\n' "$cap" | tail -3)"  # fallback if glyph not found
  printf '%s' "$line" | _norm
}
_norm() { # stdin -> CRLF folded, all whitespace runs collapsed to one space
  tr '\r' '\n' | tr -s '[:space:]' ' '
}
# The LIVE composer BLOCK: from the last prompt-glyph line to the end of the
# captured pane, normalized. Unlike _input_line (a single glyph line), this also
# captures the wrapped continuation lines of a long INLINE paste -- a CJK steer
# that Codex renders in full in the composer instead of collapsing it to a
# "[Pasted Content N chars]" placeholder, so the tail-signature lands on a
# continuation line that carries no glyph. The conversation echo of an
# already-sent message sits ABOVE the last glyph line, so it stays excluded;
# trailing footer/status lines are harmless to a substring containment test.
_input_region() { # $1=pane -> normalized live composer block
  local cap
  cap="$(tmux capture-pane -p -J -t "$1" 2>/dev/null)"
  printf '%s\n' "$cap" | awk -v g="$PROMPT_GLYPH" '
    { line[NR]=$0; if (index($0,g)) last=NR }
    END { if (last=="") last=(NR>3?NR-2:1); for (i=last;i<=NR;i++) print line[i] }
  ' | _norm
}

# Literal substring test (quoting the needle disables glob specials).
_contains() { # $1=haystack $2=needle
  case "$2" in "") return 0 ;; esac
  [[ "$1" == *"$2"* ]]
}

# Tail signature of the message file, normalized.
_signature() { # $1=file -> echoes signature
  local n; n="$(_norm < "$1")"
  # trim leading/trailing single spaces
  n="${n# }"; n="${n% }"
  local len=${#n}
  if (( len > SIG_LEN )); then
    printf '%s' "${n: -SIG_LEN}"
  else
    printf '%s' "$n"
  fi
}

# ---- collapsed bracketed paste ----------------------------------------------
#
# A long paste does not appear character-for-character in Codex's composer; the
# TUI collapses it to a placeholder like "[Pasted Content 1097 chars]" on the
# live input line. We therefore corroborate "text landed" two ways: the literal
# tail-signature (short, inline pastes) OR this placeholder whose advertised
# count matches the file we pasted (long, collapsed pastes).

# Extract N from a "Pasted Content <N> chars" placeholder in $1 (case-insensitive,
# tolerant of the surrounding brackets / spacing). Echoes N, or empty if absent.
_paste_placeholder_count() { # $1=text -> echoes N or ""
  printf '%s' "$1" \
    | grep -oiE 'pasted[[:space:]]+content[^0-9]{1,6}[0-9]+[[:space:]]*char' 2>/dev/null \
    | grep -oE '[0-9]+' | tail -1
}

# Unicode codepoint count of a file (locale-independent). Codex counts scalar
# values ("chars"), not bytes, so a CJK paste must NOT be measured in bytes.
_file_charcount() { # $1=file -> echoes codepoint count (best effort)
  local n=""
  if command -v python3 >/dev/null 2>&1; then
    n="$(python3 -c 'import sys; print(len(sys.stdin.buffer.read().decode("utf-8","replace")))' < "$1" 2>/dev/null)"
  fi
  [ -n "$n" ] || n="$(LC_ALL=C.UTF-8 wc -m < "$1" 2>/dev/null | tr -d ' ')"
  [ -n "$n" ] || n="$(wc -m < "$1" 2>/dev/null | tr -d ' ')"
  [ -n "$n" ] || n="$(wc -c < "$1" 2>/dev/null | tr -d ' ')"
  printf '%s' "$n"
}

# True (0) if $1 holds a collapsed-paste placeholder whose count is ~ $2 chars.
# The tolerance (10%, floor 16) absorbs a trailing newline and small counting
# differences while still rejecting a stale/unrelated placeholder of a very
# different size. With no expected count ($2 empty/0) any placeholder matches.
_placeholder_matches() { # $1=text $2=expected_charcount -> 0 if matching placeholder present
  local n want="$2" tol lo hi
  n="$(_paste_placeholder_count "$1")"
  [ -n "$n" ] || return 1
  { [ -n "$want" ] && [[ "$want" =~ ^[0-9]+$ ]] && (( want > 0 )); } || return 0
  tol=$(( want / 10 )); (( tol < 16 )) && tol=16
  lo=$(( want - tol )); hi=$(( want + tol ))
  (( n >= lo && n <= hi ))
}

# True (0) if the steer text still occupies the LIVE input line, in either form:
# the literal tail-signature, or a collapsed-paste placeholder for this file.
# Used by submit to watch the steer LEAVE the composer after Enter.
_steer_in_input() { # $1=pane $2=sig $3=expected_charcount
  local line; line="$(_input_region "$1")"
  { [ -n "$2" ] && _contains "$line" "$2"; } && return 0
  _placeholder_matches "$line" "$3" && return 0
  return 1
}

# ---- target health ----------------------------------------------------------

# One field per line, so empty fields or spaces/paths never misalign.
_meta() { # $1=pane -> 7 lines (dead,inputoff,inmode,alt,cmd,tty,path) or empty
  tmux display-message -p -t "$1" \
'#{pane_dead}
#{pane_input_off}
#{pane_in_mode}
#{alternate_on}
#{pane_current_command}
#{pane_tty}
#{pane_current_path}' \
    2>/dev/null
}

_looks_like_codex() { # $1=cmd $2=tty -> 0 if matches allowlist
  printf '%s' "$1" | grep -qiE "$CMD_ALLOWLIST" && return 0
  local t="${2#/dev/}"
  [ -n "$t" ] && ps -t "$t" -o args= 2>/dev/null | grep -qiE "$CMD_ALLOWLIST" && return 0
  return 1
}

# Snapshot used for the stability gate. Whole-pane scope ('pane') treats ANY
# frame change as instability -- which never settles while Codex is Working (the
# spinner animates, the "Working (Xm Ys)" timer ticks, output streams above the
# composer), even though the input box itself is perfectly ready to receive a
# paste. The default 'input' scope watches only the live input line, which stays
# stable during active work; the paste still lands and is verified afterward by
# the input-line land check (do_type) and the submit watch (_poll_submit).
_stable_snap() { # $1=pane -> scoped, normalized snapshot
  case "$STABLE_SCOPE" in
    pane) _capture "$1" | _norm ;;
    *)    _input_line "$1" ;;
  esac
}
_wait_stable() { # $1=pane -> 0 when two consecutive (scoped) snapshots match
  local a b i
  a="$(_stable_snap "$1")"
  for ((i=0; i<STABLE_TRIES; i++)); do
    sleep "$POLL"
    b="$(_stable_snap "$1")"
    [[ "$a" == "$b" ]] && return 0
    a="$b"
  done
  return 1
}

verify_target() { # $1=pane ; prints diagnosis, returns code
  local pane="$1" meta dead inputoff inmode alt cmd tty path
  meta="$(_meta "$pane")"
  if [ -z "$meta" ]; then
    err "target '$pane' does not resolve"
    return 10
  fi
  { read -r dead; read -r inputoff; read -r inmode; read -r alt
    read -r cmd; read -r tty; read -r path; } <<EOF
$meta
EOF
  if [ "$dead" = "1" ]; then err "pane is dead"; return 10; fi
  if [ "$inputoff" = "1" ]; then err "pane input is off"; return 11; fi
  if [ "$inmode" = "1" ]; then err "pane is in copy/view mode -- not accepting input"; return 11; fi
  # Codex's TUI runs on the PRIMARY screen (alternate_on=0), unlike Claude Code
  # (alternate_on=1), so alternate-screen is NOT a usable discriminator. Identify
  # the target by its process tree instead -- this is what stops a steer from
  # ever landing in a plain shell or in the monitor's own Claude pane.
  if ! _looks_like_codex "$cmd" "$tty"; then
    if [ "$REQUIRE_CODEX" = "1" ]; then
      err "pane process '$cmd' (tty $tty) is not a Codex process per allowlist '$CMD_ALLOWLIST' -- refusing (set STEER_REQUIRE_CODEX=0 to override)"
      return 11
    fi
    err "warning: pane is not identified as Codex (override active, continuing)"
  fi
  if [ -z "$(_capture "$pane")" ]; then err "pane capture is empty"; return 20; fi
  printf 'ok pane=%s cmd=%s alt=%s cwd=%s\n' "$pane" "$cmd" "$alt" "$path"
  return 0
}

# ---- type -------------------------------------------------------------------

_check_file() { # $1=file
  [ -n "$1" ] || { err "missing <file>"; return 64; }
  [ -f "$1" ] || { err "file not found: $1"; return 64; }
  [ -s "$1" ] || { err "file is empty: $1"; return 64; }
  if [ "$(tr -cd '\000' < "$1" | wc -c)" -ne 0 ]; then
    err "file contains NUL bytes -- not a valid text payload"; return 64
  fi
  return 0
}

_paste() { # $1=pane $2=file -> 0 on success (load/paste/delete buffer)
  local buf="steer-$$-$(date +%s%N)"
  tmux load-buffer -b "$buf" "$2" 2>/dev/null || { err "load-buffer failed"; return 1; }
  # -p bracketed paste; -r prevents LF->CR (which would submit mid-paste)
  if ! tmux paste-buffer -p -r -b "$buf" -t "$1" 2>/dev/null; then
    tmux delete-buffer -b "$buf" 2>/dev/null || true
    err "paste-buffer failed"; return 1
  fi
  tmux delete-buffer -b "$buf" 2>/dev/null || true
  return 0
}

do_type() { # $1=pane $2=file [internal: skip evidence dir if EVID set]
  local pane="$1" file="$2" sig nchars hay i
  _check_file "$file" || return $?
  [ -n "$EVID" ] || _evidence_dir >/dev/null
  _meta "$pane" | _save target.txt
  _capture "$pane" | _save before.txt

  if ! _wait_stable "$pane"; then
    err "pane is not stable (still rendering) -- refusing to inject"
    return 20
  fi
  sig="$(_signature "$file")"
  nchars="$(_file_charcount "$file")"

  _paste "$pane" "$file" || return 30

  for ((i=0; i<LAND_TRIES; i++)); do
    sleep "$POLL"
    hay="$(_input_region "$pane")"
    # Short pastes echo inline -> match the literal tail-signature.
    if _contains "$hay" "$sig"; then
      _capture "$pane" | _save after-paste.txt
      printf 'typed pane=%s bytes=%s sig-landed=yes\n' "$pane" "$(wc -c <"$file" | tr -d ' ')"
      return 0
    fi
    # Long pastes collapse to "[Pasted Content <N> chars]" -> match the placeholder.
    if _placeholder_matches "$hay" "$nchars"; then
      _capture "$pane" | _save after-paste.txt
      printf 'typed pane=%s bytes=%s chars=%s sig-landed=collapsed-paste\n' \
        "$pane" "$(wc -c <"$file" | tr -d ' ')" "$nchars"
      return 0
    fi
  done
  _capture "$pane" | _save after-paste.txt
  err "text-landed verification failed (neither literal signature nor a matching [Pasted Content N chars] placeholder visible near input)"
  return 31
}

# ---- submit -----------------------------------------------------------------

# Poll after an Enter. Echoes one of: success | missed | ambiguous.
# Success = the steer text left the LIVE INPUT LINE -- detected as the literal
# tail-signature OR a collapsed "[Pasted Content N chars]" placeholder leaving
# the box (it may still echo in the conversation above, which is fine). A long
# paste never shows its literal chars in the composer, so without the placeholder
# check this would report success on the FIRST poll -- before Enter even had a
# chance to register -- masking a silently-dropped Enter. When no file was
# supplied (sig and nchars both empty) we fall back to a pane delta. The delta
# only decides, at timeout, whether a still-present steer means "Enter never
# registered" (retry-safe) or "something changed but text stuck" (ambiguous).
_poll_submit() { # $1=pane $2=sig $3=nchars $4=pre_full
  local pane="$1" sig="$2" nchars="$3" pre="$4" i full delta have_text=0
  { [ -n "$sig" ] || [ -n "$nchars" ]; } && have_text=1
  for ((i=0; i<SUBMIT_TRIES; i++)); do
    sleep "$POLL"
    if (( have_text==0 )); then
      full="$(_capture "$pane" | _norm)"
      [[ "$full" != "$pre" ]] && { echo success; return; }
      continue
    fi
    _steer_in_input "$pane" "$sig" "$nchars" || { echo success; return; }  # left the box -> submitted
    # still in input box -> keep polling; classify at timeout
  done
  full="$(_capture "$pane" | _norm)"
  delta=0; [[ "$full" != "$pre" ]] && delta=1
  if (( have_text==0 )); then (( delta==1 )) && echo success || echo missed; return; fi
  _steer_in_input "$pane" "$sig" "$nchars" || { echo success; return; }
  (( delta==0 )) && { echo missed; return; }      # nothing changed + still in box -> safe retry
  echo ambiguous                                  # changed but still in box -> stop
}

do_submit() { # $1=pane [$2=file]
  local pane="$1" file="${2:-}" sig="" nchars="" pre attempt verdict
  [ -n "$EVID" ] || _evidence_dir >/dev/null
  if [ -n "$file" ] && [ -f "$file" ]; then sig="$(_signature "$file")"; nchars="$(_file_charcount "$file")"; fi

  if ! _wait_stable "$pane"; then
    err "pane is not stable before submit -- refusing"
    return 20
  fi
  pre="$(_capture "$pane" | _norm)"

  for ((attempt=1; attempt<=MAX_ENTERS; attempt++)); do
    tmux send-keys -t "$pane" Enter 2>/dev/null || { err "send-keys Enter failed"; return 40; }
    verdict="$(_poll_submit "$pane" "$sig" "$nchars" "$pre")"
    _capture "$pane" | _save "after-enter-$attempt.txt"
    case "$verdict" in
      success)
        printf 'submitted pane=%s enters=%s\n' "$pane" "$attempt"
        return 0 ;;
      ambiguous)
        err "ambiguous post-Enter state (pane changed but text remains) -- retry suppressed"
        return 41 ;;
      missed)
        # no delta + text still in box: safe to retry once more
        pre="$(_capture "$pane" | _norm)"
        continue ;;
    esac
  done
  err "submit verification failed after $MAX_ENTERS Enter attempt(s)"
  return 40
}

# ---- dispatch ---------------------------------------------------------------

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    verify-target)
      [ $# -ge 1 ] || usage
      verify_target "$1" ;;
    type)
      [ $# -ge 2 ] || usage
      verify_target "$1" >/dev/null || return $?
      do_type "$1" "$2" ;;
    submit)
      [ $# -ge 1 ] || usage
      verify_target "$1" >/dev/null || return $?
      do_submit "$1" "${2:-}" ;;
    send)
      [ $# -ge 2 ] || usage
      verify_target "$1" >/dev/null || return $?
      do_type "$1" "$2" || return $?
      do_submit "$1" "$2" ;;
    -h|--help|help|"")
      usage ;;
    *)
      err "unknown subcommand: $sub"; usage ;;
  esac
}

# Allow sourcing for unit tests without executing the dispatcher.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
