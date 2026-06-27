#!/usr/bin/env bash
#
# test-inject-steer.sh -- offline unit tests for inject-steer.sh.
#
# These exercise the pure verification helpers (no tmux, no live pane). The focus
# is the COLLAPSED BRACKETED PASTE case: Codex's TUI renders a long paste in its
# composer as a single placeholder line "[Pasted Content <N> chars]" instead of
# echoing the literal text. The literal tail-signature is then never visible, so
# `type` must accept the placeholder (when N ~ the file's codepoint count), and
# `submit` must watch THAT placeholder leave the composer after Enter rather than
# the literal signature (which was never in the box). Short pastes still render
# inline and use the literal tail-signature path; both paths are covered here.
#
# Run:  bash scripts/test-inject-steer.sh   (exit 0 = all pass)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source the scripts under test. Their dispatchers are guarded by a
# BASH_SOURCE==$0 check, so sourcing loads the functions without running main().
# interrupt-target.sh re-sources inject-steer.sh, so this single source pulls in
# both inject-steer's verification helpers and interrupt-target's _is_working.
# shellcheck source=interrupt-target.sh
source "$HERE/interrupt-target.sh"

PASS=0 FAIL=0
GLYPH=$'›'   # the same prompt glyph the script keys on (U+203A)

_ok()   { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
_bad()  { FAIL=$((FAIL+1)); printf 'FAIL: %s\n' "$1"; }

eq()    { if [ "$2" = "$3" ]; then _ok "$1"; else _bad "$1 (want [$3] got [$2])"; fi; }
true_() { local label="$1"; shift; if "$@"; then _ok "$label"; else _bad "$label (expected rc 0)"; fi; }
false_(){ local label="$1"; shift; if "$@"; then _bad "$label (expected nonzero rc)"; else _ok "$label"; fi; }

tmpd="$(mktemp -d)"; trap 'rm -rf "$tmpd"' EXIT

# --- _paste_placeholder_count: extraction from the real Codex render ----------
# Real captured line from a 1097-char steer: "> [Pasted Content 1097 chars]".
real_line="$GLYPH [Pasted Content 1097 chars]"
eq "placeholder count from real render line" "$(_paste_placeholder_count "$real_line")" "1097"
eq "placeholder count is case-insensitive"   "$(_paste_placeholder_count "$GLYPH [pasted CONTENT 42 Chars]")" "42"
eq "no placeholder in an ordinary input line" "$(_paste_placeholder_count "$GLYPH please verify the area numbers")" ""
eq "no placeholder in a CJK input line"        "$(_paste_placeholder_count "$GLYPH 请核实面积证据")" ""

# --- _file_charcount: codepoints, not bytes ----------------------------------
printf '%s' "$(printf 'a%.0s' $(seq 1 1097))" > "$tmpd/ascii.txt"
eq "ascii file charcount" "$(_file_charcount "$tmpd/ascii.txt")" "1097"
# 5 Han codepoints == 15 UTF-8 bytes; Codex would say "5 chars", not "15".
printf '%s' '面积证据核' > "$tmpd/cjk.txt"
eq "cjk file charcount is codepoints (5), not bytes (15)" "$(_file_charcount "$tmpd/cjk.txt")" "5"

# --- _placeholder_matches: count corroboration with tolerance ----------------
true_  "matches exact count"               _placeholder_matches "$real_line" 1097
true_  "matches within tolerance (+1 newline)" _placeholder_matches "$real_line" 1096
true_  "matches within 10% tolerance"      _placeholder_matches "$GLYPH [Pasted Content 1000 chars]" 1050
false_ "rejects gross undercount"          _placeholder_matches "$real_line" 40
false_ "rejects gross overcount"           _placeholder_matches "$real_line" 9000
false_ "no placeholder -> no match"        _placeholder_matches "$GLYPH ordinary text here" 1097
true_  "empty expected count accepts any placeholder" _placeholder_matches "$real_line" ""

# --- _steer_in_input: submit-side predicate (mock the live composer block) ----
# _steer_in_input reads the composer via _input_region (the live glyph block,
# which also captures wrapped continuation lines), NOT _input_line -- so the mock
# must override _input_region to drive the composer state by hand.
MOCK_INPUT=""
_input_region() { printf '%s' "$MOCK_INPUT"; }

# Collapsed paste: placeholder present before Enter, gone after.
MOCK_INPUT="$GLYPH [Pasted Content 1097 chars]"
true_  "collapsed: steer present in composer pre-Enter"  _steer_in_input pane "" 1097
MOCK_INPUT="$GLYPH"
false_ "collapsed: steer left composer post-Enter"       _steer_in_input pane "" 1097

# Short literal paste: tail-signature present before Enter, gone after.
MOCK_INPUT="$GLYPH ...trailing tail signature XYZ"
true_  "inline: literal signature present pre-Enter"      _steer_in_input pane "signature XYZ" ""
MOCK_INPUT="$GLYPH"
false_ "inline: literal signature left composer post-Enter" _steer_in_input pane "signature XYZ" ""

# A stale placeholder of a very different size must NOT read as our steer.
MOCK_INPUT="$GLYPH [Pasted Content 50 chars]"
false_ "collapsed: mismatched stale placeholder is not our steer" _steer_in_input pane "" 1097

# --- interrupt-target.sh _is_working: the post-Escape "still streaming" gate ---
# Drive the pane snapshot by hand. _is_working calls _capture | _norm, so mock
# _capture to return a chosen frame; the real _norm collapses whitespace.
MOCK_PANE=""
_capture() { printf '%s' "$MOCK_PANE"; }

# An active turn shows a Working status line and/or an "Esc to interrupt" hint.
MOCK_PANE="$GLYPH  Working (1m 4s)  Esc to interrupt"
true_  "working: Working status line is detected"        _is_working pane
MOCK_PANE="codex output streaming... Esc to interrupt"
true_  "working: 'Esc to interrupt' affordance detected"  _is_working pane
# An idle composer with no markers must read as NOT working (lets interrupt pass).
MOCK_PANE="$GLYPH"
false_ "idle: bare composer is not working"              _is_working pane
MOCK_PANE="$GLYPH ready for input"
false_ "idle: ordinary composer text is not working"     _is_working pane
# An empty capture is treated as not-working (no streaming markers present).
MOCK_PANE=""
false_ "idle: empty capture is not working"              _is_working pane

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
