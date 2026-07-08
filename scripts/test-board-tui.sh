#!/usr/bin/env bash
#
# test-board-tui.sh -- offline tests for board.py `summary`/`boards` and the
# read-only board-tui.py.
#
# The fixture board is populated ONLY through the board.py CLI (which doubles
# as a write-path test and self-registers the board in the global registry,
# pointed at a temp file via CURRYFLOWS_REGISTRY for the whole run), then read
# back through the two terminal surfaces: the T0 one-line `summary` and the T1
# headless `--render` frames (including no-arg board auto-discovery and the
# `boards` picker table). The resolve step exercises board.py's decision
# resolution -- explicitly the COORDINATOR's path; the TUI has zero write
# paths, and a tripwire asserts that string never reappears in its source.
# The final step deliberately corrupts threads.jsonl by hand to assert
# corruption is surfaced (stderr names file and line, exit 1), never hidden.
#
# The interactive curses layer (including the registry picker) needs a real
# TTY and is NOT covered here (see the NOTE at the end); only the headless
# surfaces are exercised.
#
# Run:  bash scripts/test-board-tui.sh   (exit 0 = all pass; first FAIL exits 1)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY=python3
BOARD_PY="$HERE/board.py"
TUI_PY="$HERE/board-tui.py"

PASS=0
_ok()  { PASS=$((PASS+1)); printf 'PASS: %s\n' "$1"; }
_die() { printf 'FAIL: %s\n' "$1"; exit 1; }

# assert_in <label> <needle> <haystack>
assert_in() {
  if printf '%s\n' "$3" | grep -qF -- "$2"; then _ok "$1"; else
    printf '--- output was ---\n%s\n------------------\n' "$3"
    _die "$1 (missing [$2])"
  fi
}
# assert_not_in <label> <needle> <haystack>
assert_not_in() {
  if printf '%s\n' "$3" | grep -qF -- "$2"; then
    printf '--- output was ---\n%s\n------------------\n' "$3"
    _die "$1 (unexpected [$2])"
  else _ok "$1"; fi
}

tmpd="$(mktemp -d)"; trap 'rm -rf "$tmpd"' EXIT
BOARD="$tmpd/.curryflows/board"
PAUSE="$tmpd/.curryflows/pause"

# the whole run uses a temp registry: mutating board.py calls self-register
# the fixture board here instead of ~/.cache/curryflows/boards.jsonl.
export CURRYFLOWS_REGISTRY="$tmpd/registry.jsonl"

# --- 1. both scripts must byte-compile ---------------------------------------
"$PY" -m py_compile "$BOARD_PY" "$TUI_PY" || _die "py_compile board.py + board-tui.py"
_ok "py_compile board.py + board-tui.py"

# --- 2. fixture board, populated ONLY via the board.py CLI -------------------
printf 'contract fixture body\n' > "$tmpd/contract.md"
printf 'evidence fixture body\n' > "$tmpd/evidence.md"

"$PY" "$BOARD_PY" upsert-thread --board "$BOARD" --id t-alpha \
  --state running --branch curryflows/t-alpha --worktree "$tmpd/wt-alpha" \
  --tmux-session cfx-t-alpha --budget-tokens 4000000 --budget-spent 1200000 \
  --contract "$tmpd/contract.md" >/dev/null \
  || _die "upsert-thread t-alpha"
_ok "upsert-thread t-alpha (running)"

"$PY" "$BOARD_PY" post-decision --board "$BOARD" --id d-1 \
  --barrier merge-main --thread t-beta --summary "merge t-beta to main" \
  --recommendation "ship it" --evidence "$tmpd/evidence.md" \
  --options "ship|hold" >/dev/null \
  || _die "post-decision d-1"
_ok "post-decision d-1 (merge-main, options ship|hold)"

# blocked-human AFTER d-1 exists: the fail-closed guard requires the open
# decision to already be on the board.
"$PY" "$BOARD_PY" upsert-thread --board "$BOARD" --id t-beta \
  --state blocked-human >/dev/null \
  || _die "upsert-thread t-beta (blocked-human, after d-1)"
_ok "upsert-thread t-beta (blocked-human, after d-1)"

"$PY" "$BOARD_PY" upsert-backlog --board "$BOARD" --id b-1 \
  --summary "fixture backlog item" --status sealed-ready --dedup-key k1 \
  >/dev/null || _die "upsert-backlog b-1"
_ok "upsert-backlog b-1 (sealed-ready)"

printf '{"tick":1,"summary":"fixture"}\n' > "$tmpd/tick.json"
"$PY" "$BOARD_PY" record-tick --board "$BOARD" --file "$tmpd/tick.json" \
  >/dev/null || _die "record-tick tick 1"
_ok "record-tick tick 1"

# --- 2b. global registry: mutating calls self-register the board (deduped) ----
[ -f "$CURRYFLOWS_REGISTRY" ] || _die "registry file exists after fixture writes"
_ok "registry file exists after fixture writes"
n="$(grep -cF -- "\"board\": \"$BOARD\"" "$CURRYFLOWS_REGISTRY" || true)"
if [ "$n" -eq 1 ]; then _ok "registry contains the fixture board exactly once"; else
  printf -- '--- registry was ---\n%s\n--------------------\n' "$(cat "$CURRYFLOWS_REGISTRY")"
  _die "registry contains the fixture board exactly once (got $n)"
fi
out="$("$PY" "$BOARD_PY" boards)" || _die "board.py boards exits 0"
_ok "board.py boards exits 0"
assert_in "boards lists the fixture board" "$BOARD" "$out"
assert_in "boards marks it exists: true"   '"exists": true' "$out"

# --- 3. summary: exact glyph+digit pairs -------------------------------------
out="$("$PY" "$BOARD_PY" summary --board "$BOARD")" \
  || _die "summary exits 0"
_ok "summary exits 0"
assert_in "summary counts running"       "▶1" "$out"
assert_in "summary counts blocked-human" "⏸1" "$out"
assert_in "summary counts open decision" "⚑1" "$out"
assert_in "summary counts sealed-ready"  "◆1" "$out"

# --- 4. summary: PAUSED suffix tracks the pause file --------------------------
touch "$PAUSE"
out="$("$PY" "$BOARD_PY" summary --board "$BOARD")" || _die "summary (paused) exits 0"
assert_in "summary shows | PAUSED while pause file exists" " | PAUSED" "$out"
rm "$PAUSE"
out="$("$PY" "$BOARD_PY" summary --board "$BOARD")" || _die "summary (resumed) exits 0"
assert_not_in "summary drops PAUSED after pause file removed" "PAUSED" "$out"

# --- 5. --render threads -------------------------------------------------------
out="$("$PY" "$TUI_PY" --board "$BOARD" --render threads)" \
  || _die "--render threads exits 0"
_ok "--render threads exits 0"
assert_in "threads frame lists t-alpha"        "t-alpha" "$out"
assert_in "threads frame shows running state"  "running" "$out"
assert_in "threads frame shows budget percent" "30%" "$out"
assert_in "threads frame lists t-beta"         "t-beta" "$out"
assert_in "threads frame shows blocked-human"  "blocked-human" "$out"

# --- 6. --render decisions ------------------------------------------------------
out="$("$PY" "$TUI_PY" --board "$BOARD" --render decisions)" \
  || _die "--render decisions exits 0"
_ok "--render decisions exits 0"
assert_in "decisions frame lists d-1"        "d-1" "$out"
assert_in "decisions frame shows merge-main" "merge-main" "$out"
assert_in "decisions frame shows option 1"   "ship" "$out"
assert_in "decisions frame shows option 2"   "hold" "$out"

# --- 7. --render backlog / --render ticks ------------------------------------------
out="$("$PY" "$TUI_PY" --board "$BOARD" --render backlog)" \
  || _die "--render backlog exits 0"
assert_in "backlog frame lists b-1"         "b-1" "$out"
assert_in "backlog frame shows sealed-ready" "sealed-ready" "$out"
out="$("$PY" "$TUI_PY" --board "$BOARD" --render ticks)" \
  || _die "--render ticks exits 0"
assert_in "ticks frame shows the fixture tick" "fixture" "$out"

# --- 7b. no-arg auto-discovery: cwd walk-up finds .curryflows/board -----------
out="$(cd "$tmpd" && "$PY" "$TUI_PY" --render threads)" \
  || _die "no-arg --render threads (cwd inside project) exits 0"
_ok "no-arg --render threads (cwd inside project) exits 0"
assert_in "auto-discovered board renders t-alpha" "t-alpha" "$out"

# --- 7c. --render boards: picker table, and exit 2 + hint on empty registry ---
out="$("$PY" "$TUI_PY" --render boards)" || _die "--render boards exits 0"
_ok "--render boards exits 0"
assert_in "boards table lists the fixture project" "$(basename "$tmpd")" "$out"
assert_in "boards table lists the fixture board path" "$BOARD" "$out"

err="$(CURRYFLOWS_REGISTRY="$tmpd/empty-registry.jsonl" \
  "$PY" "$TUI_PY" --render boards 2>&1 >/dev/null)"
rc=$?
if [ "$rc" -eq 2 ]; then _ok "--render boards exits 2 on empty registry"; else
  _die "--render boards exits 2 on empty registry (got rc=$rc)"
fi
assert_in "empty-registry hint says boards self-register" "self-register" "$err"

# --- 8. resolution path: explicitly the COORDINATOR's, via board.py -----------
# The TUI has ZERO write paths (CANON [R] revised): the human replies in the
# main session and the coordinator lands the resolution with this exact call.
"$PY" "$BOARD_PY" resolve-decision --board "$BOARD" --id d-1 \
  --resolution "ship" >/dev/null || _die "resolve-decision d-1 (coordinator path)"
_ok "resolve-decision d-1 (ship; coordinator path)"
out="$("$PY" "$BOARD_PY" summary --board "$BOARD")" || _die "summary after resolve exits 0"
assert_in "summary shows 0 open decisions after resolve" "⚑0" "$out"
out="$("$PY" "$TUI_PY" --board "$BOARD" --render decisions)" \
  || _die "--render decisions after resolve exits 0"
assert_not_in "open-filtered decisions frame no longer lists d-1" "d-1" "$out"

# --- 8b. tripwire: the TUI source must not regrow the resolution write path ----
n="$(grep -c -- 'resolve-decision' "$TUI_PY" || true)"
if [ "$n" -eq 0 ]; then _ok "board-tui.py never mentions resolve-decision (zero write paths)"; else
  grep -n -- 'resolve-decision' "$TUI_PY"
  _die "board-tui.py never mentions resolve-decision (found $n occurrence(s))"
fi

# --- 9. corruption surfacing (hand-append deliberately bypasses board.py) ------
echo 'garbage {not json' >> "$BOARD/threads.jsonl"
err="$("$PY" "$BOARD_PY" summary --board "$BOARD" 2>&1 >/dev/null)"
rc=$?
if [ "$rc" -eq 1 ]; then _ok "summary exits 1 on corrupted threads.jsonl"; else
  _die "summary exits 1 on corrupted threads.jsonl (got rc=$rc)"
fi
assert_in "corruption error names file and line" "threads.jsonl:3" "$err"

printf '\n%d checks passed\n' "$PASS"
printf 'NOTE: the interactive curses layer (including the registry picker) is NOT covered here (it needs a real TTY); only the headless --render surfaces, summary, boards, and the board.py write path (the coordinator'"'"'s) are verified.\n'
exit 0
