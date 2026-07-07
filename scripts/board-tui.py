#!/usr/bin/env python3
"""curryflows: terminal board TUI -- the on-demand human surface (CANON [R] T1).

A python3-stdlib curses viewer over the durable board (threads.jsonl /
decisions.jsonl / backlog.jsonl / ticks.jsonl) plus a decision-input surface.
Launched in a zellij floating pane or tmux popup; the always-visible T0 tier is
`board.py summary` under `watch -n 15`.

CANON [R] write-path discipline: this TUI is a READ-ONLY renderer of the board
whose only write paths into the system are
  (1) resolve-decision, shelled out to board.py (the sole board writer) -- the
      TUI never writes the jsonl files itself;
  (2) creating/removing the pause flag file <cf_dir>/pause (a plain flag file);
  (3) the pre-existing human right of Esc emergency stop is NOT implemented
      here -- humans attach to the worker pane (Enter) and press Esc there.
The TUI NEVER performs lifecycle operations (launch/steer/commit/merge/reap);
those belong to the coordinator, the single lifecycle writer. Closing the TUI
must not affect progress.

Refresh model (explicit design decision): passive refresh only stats mtimes --
on every getch timeout (1000 ms) the four jsonl files plus the pause file are
stat()ed and ONLY files whose mtime changed are re-read. Active discovery runs
ONLY on the R key (discover-threads.py), never on the passive path: the
coordinator tick already runs authoritative discovery, R is a queue-jump.

Corruption handling: strict read-back (boardlib.read_jsonl_strict) is reused,
never duplicated. In curses mode a ValueError keeps the last good data and
shows a persistent bold banner with the file:line error until a re-read
succeeds -- corruption is surfaced, never hidden. In --render mode corruption
goes to stderr with exit 1.

CLI:
  board-tui.py --board <dir> [--render threads|decisions|backlog|ticks]

--render prints one plain-text frame of that view to stdout (no curses, no
color) and exits 0 -- the testable surface, also usable by agents. A missing
board dir renders headers with zero rows. Without --render, a non-TTY stdout
fails fast with exit 64 and a hint to use --render.

ticks.jsonl is append-only and read boundedly (last 50 lines, tail-then-parse,
same approach as board.py list-ticks; the file is never slurped).

CJK: summaries/decisions are Chinese; all truncation/padding goes through a
display-width helper (unicodedata.east_asian_width, W/F count 2) so columns
stay aligned in curses and stable in --render output.
"""
import argparse
import curses
import json
import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import time
import unicodedata
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
import board as boardlib  # noqa: E402  (sibling module; sole strict-IO owner)

BOARD_PY = os.path.join(SCRIPT_DIR, "board.py")
DISCOVER_PY = os.path.join(SCRIPT_DIR, "discover-threads.py")

VIEWS = ("threads", "decisions", "backlog", "ticks")
TICKS_TAIL = 50
RENDER_WIDTH = 100          # fixed frame width for --render (stable columns)
STATUS_TTL_S = 8            # transient status line lifetime
DETAIL_MAX_ROWS = 12


class _UsageParser(argparse.ArgumentParser):
    """Exit 64 on usage error (matches board.py / discover-threads.py)."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)


# --------------------------------------------------------------------------- #
# display-width helpers (CJK-aware truncation/padding)
# --------------------------------------------------------------------------- #
def _char_width(ch):
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def disp_width(s):
    return sum(_char_width(ch) for ch in s)


def clip(s, width, ellipsis=True):
    """Truncate s to at most `width` display columns."""
    if width <= 0:
        return ""
    if disp_width(s) <= width:
        return s
    budget = width - (1 if ellipsis else 0)
    out, used = [], 0
    for ch in s:
        w = _char_width(ch)
        if used + w > budget:
            break
        out.append(ch)
        used += w
    return "".join(out) + ("…" if ellipsis else "")


def pad(s, width):
    """Clip then right-pad s to exactly `width` display columns."""
    s = clip(s, width)
    return s + " " * (width - disp_width(s))


def humanize_age(iso_ts, now=None):
    """ISO timestamp -> compact age like 45s / 5m / 3h / 2d."""
    if not iso_ts:
        return ""
    try:
        then = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "?"
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    secs = max(0, int((now - then).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _abbrev_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return str(n)


def fmt_budget(rec):
    """spent/total with percent; blank when either field is missing."""
    spent = rec.get("budget_spent")
    total = rec.get("budget_tokens")
    if not isinstance(spent, int) or not isinstance(total, int) or total <= 0:
        return ""
    pct = int(round(spent * 100.0 / total))
    return f"{_abbrev_tokens(spent)}/{_abbrev_tokens(total)} {pct}%"


def wrap_kv(label, value, width):
    """`label: value` wrapped to width; continuation lines indented."""
    text = f"{label}: {value if value not in (None, '') else '-'}"
    lines = textwrap.wrap(text, width=max(20, width),
                          subsequent_indent="  ") or [text]
    return lines


# --------------------------------------------------------------------------- #
# board state (strict read-back via boardlib; mtime-driven passive refresh)
# --------------------------------------------------------------------------- #
def read_ticks_tail(path, last=TICKS_TAIL):
    """Bounded strict read of the last N tick records (tail-then-parse, same
    approach as board.py cmd_list_ticks; never slurps the append-only file)."""
    if not os.path.isfile(path):
        return []
    with open(path, "r", errors="strict") as f:
        numbered = [(n, ln) for n, ln in enumerate(f, start=1) if ln.strip()]
    numbered = numbered[-last:]
    out = []
    for lineno, line in numbered:
        try:
            rec = json.loads(line)
        except Exception as exc:
            raise ValueError(
                f"corrupted JSONL at {path}:{lineno}: {exc}") from exc
        if not isinstance(rec, dict):
            raise ValueError(
                f"corrupted JSONL at {path}:{lineno}: not a JSON object")
        out.append(rec)
    return out


class BoardState:
    """Loaded board data + the paths derived from --board (see references:
    cf_dir = dirname(board), pause = cf_dir/pause, contracts = cf_dir/contracts,
    project_dir = dirname(cf_dir))."""

    def __init__(self, board):
        self.board = os.path.abspath(board)
        self.cf_dir = os.path.dirname(self.board)
        self.project_dir = os.path.dirname(self.cf_dir)
        self.pause_path = os.path.join(self.cf_dir, "pause")
        self.contracts_dir = os.path.join(self.cf_dir, "contracts")
        self.threads = []
        self.decisions = []
        self.backlog = []
        self.ticks = []
        self.paused = False
        self.corruption = {}     # file key -> error text (persistent banner)
        self._mtimes = {}

    def _path(self, name):
        return {
            "threads": boardlib.threads_path(self.board),
            "decisions": boardlib.decisions_path(self.board),
            "backlog": boardlib.backlog_path(self.board),
            "ticks": boardlib.ticks_path(self.board),
        }[name]

    def _read(self, name):
        if name == "ticks":
            return read_ticks_tail(self._path(name))
        return boardlib.read_jsonl_strict(self._path(name))

    def load_all_strict(self):
        """Load everything; ValueError propagates (--render mode)."""
        for name in VIEWS:
            setattr(self, name, self._read(name))
        for name in VIEWS:
            self._remember_mtime(name)
        self.paused = os.path.exists(self.pause_path)

    def _remember_mtime(self, name):
        try:
            self._mtimes[name] = os.stat(self._path(name)).st_mtime_ns
        except OSError:
            self._mtimes[name] = None

    def reload_file(self, name):
        """Re-read one file, keeping last good data on corruption (curses)."""
        self._remember_mtime(name)
        try:
            setattr(self, name, self._read(name))
            self.corruption.pop(name, None)
        except ValueError as exc:
            self.corruption[name] = str(exc)

    def reload_all(self):
        for name in VIEWS:
            self.reload_file(name)
        self.paused = os.path.exists(self.pause_path)

    def poll(self):
        """Passive refresh: stat the four jsonl files + pause file; re-read
        ONLY files whose mtime changed. Runs on every getch timeout."""
        changed = False
        for name in VIEWS:
            try:
                mtime = os.stat(self._path(name)).st_mtime_ns
            except OSError:
                mtime = None
            if self._mtimes.get(name, "unset") != mtime:
                self.reload_file(name)
                changed = True
        paused = os.path.exists(self.pause_path)
        if paused != self.paused:
            self.paused = paused
            changed = True
        return changed

    def counts_line(self):
        line = boardlib.summary_line(self.threads, self.decisions, self.backlog)
        if self.paused:
            line += " | PAUSED"
        return line


# --------------------------------------------------------------------------- #
# view model: rows, table columns, detail boxes (shared curses / --render)
# --------------------------------------------------------------------------- #
def visible_rows(state, view, open_only=True):
    if view == "threads":
        return state.threads
    if view == "decisions":
        if open_only:
            return [r for r in state.decisions if r.get("status") == "open"]
        return state.decisions
    if view == "backlog":
        return state.backlog
    # ticks: last 50 already; newest first for display
    return list(reversed(state.ticks))


TABLE_COLS = {
    "threads": (("THREAD", 20), ("STATE", 14), ("ATT", 3),
                ("BUDGET", 18), ("VERDICT", 12), ("AGE", 5)),
    "decisions": (("ID", 16), ("BARRIER", 20), ("THREAD", 16),
                  ("AGE", 5), ("SUMMARY", None)),
    "backlog": (("ID", 14), ("STATUS", 12), ("DEDUP_KEY", 20),
                ("SUMMARY", None)),
    "ticks": (("TICK", 6), ("TS", 20), ("SUMMARY", None)),
}


def table_cells(view, rec):
    if view == "threads":
        return (rec.get("thread_id") or "",
                rec.get("state") or "",
                "" if rec.get("attempt") is None else str(rec.get("attempt")),
                fmt_budget(rec),
                rec.get("last_verdict") or "",
                humanize_age(rec.get("updated")))
    if view == "decisions":
        return (rec.get("id") or "",
                rec.get("barrier") or "",
                rec.get("thread") or "",
                humanize_age(rec.get("reopened") or rec.get("created")),
                rec.get("summary") or "")
    if view == "backlog":
        return (rec.get("backlog_id") or "",
                rec.get("status") or "",
                rec.get("dedup_key") or "",
                rec.get("summary") or "")
    return ("" if rec.get("tick") is None else str(rec.get("tick")),
            (rec.get("ts") or "")[:19],
            rec.get("summary") or "")


def column_widths(view, width):
    cols = TABLE_COLS[view]
    fixed = sum(w for _, w in cols if w) + (len(cols) - 1)
    flex = max(10, width - fixed)
    return [w if w else flex for _, w in cols]


def table_header_line(view, width):
    widths = column_widths(view, width)
    return " ".join(pad(t, w) for (t, _), w in zip(TABLE_COLS[view], widths))


def table_row_line(view, rec, width):
    widths = column_widths(view, width)
    return " ".join(pad(str(c), w)
                    for c, w in zip(table_cells(view, rec), widths))


def detail_lines(view, rec, width):
    """Detail box body for the selected row (shared curses / --render)."""
    if rec is None:
        return []
    lines = []
    if view == "threads":
        for key in ("branch", "worktree", "tmux_session",
                    "codex_session", "contract"):
            lines += wrap_kv(key, rec.get(key), width)
    elif view == "decisions":
        lines += wrap_kv("recommendation", rec.get("recommendation"), width)
        options = rec.get("options") or []
        if options:
            lines.append("options:")
            for i, opt in enumerate(options, start=1):
                lines.append(clip(f"  {i}) {opt}", width))
        else:
            lines.append("options: -")
        lines += wrap_kv("evidence", rec.get("evidence"), width)
        lines += wrap_kv("divergence", rec.get("divergence"), width)
        status = rec.get("status") or "-"
        resolution = rec.get("resolution")
        lines += wrap_kv("status", status if resolution is None
                         else f"{status} / {resolution}", width)
    elif view == "backlog":
        for key in ("rationale", "contract", "thread", "reject_reason"):
            lines += wrap_kv(key, rec.get(key), width)
    return lines


def row_key(view, rec):
    return rec.get({"threads": "thread_id", "decisions": "id",
                    "backlog": "backlog_id", "ticks": "tick"}[view], "")


def render_frame(state, view, open_only=True, width=RENDER_WIDTH):
    """One plain-text frame of a view (the --render surface)."""
    lines = [f"curryflows board: {state.board}", state.counts_line()]
    label = view
    if view == "decisions":
        label += " (open only)" if open_only else " (all)"
    lines += [f"view: {label}", ""]
    lines.append(table_header_line(view, width))
    rows = visible_rows(state, view, open_only)
    if not rows:
        lines.append("(no rows)")
    for rec in rows:
        lines.append(table_row_line(view, rec, width))
    if rows:
        detail = detail_lines(view, rows[0], width)
        if detail:
            lines += ["", f"-- detail: {row_key(view, rows[0])} --"]
            lines += detail
    return lines


# --------------------------------------------------------------------------- #
# curses UI
# --------------------------------------------------------------------------- #
HELP_LINES = [
    "global",
    "  1/2/3/4     switch view (Threads/Decisions/Backlog/Ticks)",
    "  j/k, arrows move selection",
    "  g/G         first/last row",
    "  r           force re-read of all board files",
    "  R           run resource discovery (discover-threads.py; queue-jump,",
    "              the coordinator tick already runs it authoritatively)",
    "  P           toggle the pause file",
    "  ?           this help",
    "  q           quit (never affects progress: the TUI holds no lifecycle)",
    "",
    "[1] threads",
    "  Enter       attach to the thread's tmux session (Esc stop lives there)",
    "  p           peek: last 200 pane lines (tmux capture-pane)",
    "  d           branch diff vs main-base (delta if installed, else pager)",
    "  u           uncommitted diff (git diff HEAD, same pipeline)",
    "  c           view the contract file",
    "",
    "[2] decisions (write path: resolve-decision via board.py ONLY)",
    "  o           toggle open-only / all",
    "  Enter       resolve: a bare number 1..N picks that option, else free",
    "              text; ESC cancels",
    "  x           reject: same input flow, non-empty text required",
    "  v           view the evidence file",
    "",
    "[3] backlog (read-only)",
    "  Enter/v     full record in the pager",
    "",
    "[4] ticks (read-only, last 50, newest first)",
    "  Enter       full record in the pager",
    "",
    "pager: j/k scroll, PgUp/PgDn page, q close",
]


def safe_addstr(win, y, x, s, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x < 0 or x >= w - 1:
        return
    try:
        win.addstr(y, x, clip(s, w - x - 1, ellipsis=False), attr)
    except curses.error:
        pass


class TUI:
    def __init__(self, stdscr, state):
        self.stdscr = stdscr
        self.state = state
        self.view = 0                       # index into VIEWS
        self.sel = {v: 0 for v in VIEWS}
        self.offset = {v: 0 for v in VIEWS}
        self.open_only = True
        self.status = ""
        self.status_ts = 0.0
        self.red = 0

    # -- plumbing ---------------------------------------------------------- #
    def set_status(self, msg):
        self.status = msg
        self.status_ts = time.time()

    def resume_screen(self):
        """Re-enter curses after endwin() (attach / external pager)."""
        self.stdscr.keypad(True)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        self.stdscr.clear()
        self.stdscr.refresh()
        self.stdscr.timeout(1000)

    def current_rows(self):
        return visible_rows(self.state, VIEWS[self.view], self.open_only)

    def current_rec(self):
        rows = self.current_rows()
        if not rows:
            return None
        idx = min(self.sel[VIEWS[self.view]], len(rows) - 1)
        return rows[idx]

    # -- main loop ---------------------------------------------------------- #
    def run(self):
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        if curses.has_colors():
            try:
                curses.use_default_colors()
                curses.init_pair(1, curses.COLOR_RED, -1)
                self.red = curses.color_pair(1)
            except curses.error:
                self.red = 0
        self.stdscr.timeout(1000)
        self.state.reload_all()
        while True:
            self.draw()
            ch = self.stdscr.getch()
            if ch == -1:                    # 1000 ms timeout: passive refresh
                self.state.poll()
                continue
            if ch == curses.KEY_RESIZE:
                continue
            if not self.handle_key(ch):
                return

    def handle_key(self, ch):
        view = VIEWS[self.view]
        rows = self.current_rows()
        if ch in (ord("q"), ord("Q")):
            return False
        if ch in (ord("1"), ord("2"), ord("3"), ord("4")):
            self.view = ch - ord("1")
        elif ch in (ord("j"), curses.KEY_DOWN):
            self.sel[view] = min(self.sel[view] + 1, max(0, len(rows) - 1))
        elif ch in (ord("k"), curses.KEY_UP):
            self.sel[view] = max(self.sel[view] - 1, 0)
        elif ch == ord("g"):
            self.sel[view] = 0
        elif ch == ord("G"):
            self.sel[view] = max(0, len(rows) - 1)
        elif ch == ord("r"):
            self.state.reload_all()
            self.set_status("board files re-read")
        elif ch == ord("R"):
            self.run_discovery()
        elif ch == ord("P"):
            self.toggle_pause()
        elif ch == ord("?"):
            self.pager("help", HELP_LINES)
        elif ch in (curses.KEY_ENTER, 10, 13):
            self.on_enter()
        elif view == "threads" and ch == ord("p"):
            self.peek()
        elif view == "threads" and ch == ord("d"):
            self.show_diff(branch_diff=True)
        elif view == "threads" and ch == ord("u"):
            self.show_diff(branch_diff=False)
        elif view == "threads" and ch == ord("c"):
            rec = self.current_rec()
            if rec is not None:
                self.view_file(rec.get("contract"), "contract")
        elif view == "decisions" and ch == ord("o"):
            self.open_only = not self.open_only
            self.set_status("decisions: open only" if self.open_only
                            else "decisions: all")
        elif view == "decisions" and ch == ord("x"):
            self.resolve_dialog(reject=True)
        elif view == "decisions" and ch == ord("v"):
            rec = self.current_rec()
            if rec is not None:
                self.view_file(rec.get("evidence"), "evidence")
        elif view == "backlog" and ch == ord("v"):
            self.show_full_record()
        return True

    def on_enter(self):
        view = VIEWS[self.view]
        if view == "threads":
            self.attach()
        elif view == "decisions":
            self.resolve_dialog(reject=False)
        else:                               # backlog / ticks
            self.show_full_record()

    # -- drawing ------------------------------------------------------------ #
    def draw(self):
        stdscr = self.stdscr
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        view = VIEWS[self.view]
        rows = self.current_rows()
        self.sel[view] = min(self.sel[view], max(0, len(rows) - 1))

        label = view
        if view == "decisions":
            label += " (open only, o=all)" if self.open_only else " (all)"
        safe_addstr(stdscr, 0, 0,
                    f"board: {self.state.board}", curses.A_DIM)
        safe_addstr(stdscr, 1, 0, self.state.counts_line()
                    + f"   [{self.view + 1}/4 {label}]  ?=help",
                    curses.A_BOLD if self.state.paused else 0)

        y = 2
        if self.state.corruption:
            banner = "CORRUPTION: " + " ; ".join(
                self.state.corruption[k] for k in sorted(self.state.corruption))
            safe_addstr(stdscr, y, 0, banner, curses.A_BOLD | self.red)
            y += 1

        safe_addstr(stdscr, y, 0, table_header_line(view, w - 1),
                    curses.A_UNDERLINE)
        y += 1

        rec = self.current_rec()
        detail = detail_lines(view, rec, w - 3)[:DETAIL_MAX_ROWS]
        detail_h = (len(detail) + 1) if detail else 0
        list_h = max(1, h - y - detail_h - 1)

        off = self.offset[view]
        if self.sel[view] < off:
            off = self.sel[view]
        if self.sel[view] >= off + list_h:
            off = self.sel[view] - list_h + 1
        self.offset[view] = max(0, off)

        if not rows:
            safe_addstr(stdscr, y, 0, "(no rows)", curses.A_DIM)
        for i, r in enumerate(rows[off:off + list_h]):
            attr = curses.A_REVERSE if off + i == self.sel[view] else 0
            safe_addstr(stdscr, y + i, 0,
                        pad(table_row_line(view, r, w - 1), w - 1), attr)

        if detail:
            dy = h - detail_h - 1
            safe_addstr(stdscr, dy, 0,
                        pad(f"-- detail: {row_key(view, rec)} --", w - 1),
                        curses.A_BOLD)
            for i, line in enumerate(detail):
                safe_addstr(stdscr, dy + 1 + i, 2, line)

        if self.status and time.time() - self.status_ts < STATUS_TTL_S:
            safe_addstr(stdscr, h - 1, 0, self.status, curses.A_BOLD)
        stdscr.refresh()

    # -- pager overlay (internal, scrollable) -------------------------------- #
    def pager(self, title, lines):
        self.stdscr.timeout(-1)
        off = 0
        try:
            while True:
                self.stdscr.erase()
                h, w = self.stdscr.getmaxyx()
                page = max(1, h - 2)
                off = max(0, min(off, max(0, len(lines) - page)))
                safe_addstr(self.stdscr, 0, 0,
                            pad(f"── {title} ── ({off + 1}-"
                                f"{min(off + page, len(lines))}/{len(lines)})",
                                w - 1), curses.A_REVERSE)
                for i, line in enumerate(lines[off:off + page]):
                    safe_addstr(self.stdscr, 1 + i, 0, line)
                safe_addstr(self.stdscr, h - 1, 0,
                            "j/k scroll  PgUp/PgDn page  q close",
                            curses.A_DIM)
                self.stdscr.refresh()
                ch = self.stdscr.getch()
                if ch == ord("q"):
                    return
                if ch == ord("j"):
                    off += 1
                elif ch == ord("k"):
                    off -= 1
                elif ch == curses.KEY_NPAGE:
                    off += page
                elif ch == curses.KEY_PPAGE:
                    off -= page
                elif ch == curses.KEY_RESIZE:
                    continue
        finally:
            self.stdscr.timeout(1000)

    # -- single-line input dialog (hand-rolled; backspace + ESC cancel) ------ #
    def prompt_input(self, prompt):
        """Returns the entered text, or None on ESC."""
        self.stdscr.timeout(-1)
        try:
            curses.curs_set(1)
        except curses.error:
            pass
        buf = []
        result = None
        try:
            while True:
                h, w = self.stdscr.getmaxyx()
                line = prompt + "".join(buf)
                shown = clip(line, w - 2, ellipsis=False)
                safe_addstr(self.stdscr, h - 1, 0, pad(shown, w - 1),
                            curses.A_BOLD)
                try:
                    self.stdscr.move(h - 1, min(disp_width(shown), w - 2))
                except curses.error:
                    pass
                self.stdscr.refresh()
                try:
                    ch = self.stdscr.get_wch()
                except curses.error:
                    continue
                if isinstance(ch, str):
                    if ch == "\x1b":                       # ESC: cancel
                        return None
                    if ch in ("\n", "\r"):
                        result = "".join(buf)
                        return result
                    if ch in ("\x7f", "\x08"):
                        buf = buf[:-1]
                    elif ch.isprintable():
                        buf.append(ch)
                else:
                    if ch in (curses.KEY_BACKSPACE, curses.KEY_DC):
                        buf = buf[:-1]
                    elif ch == curses.KEY_ENTER:
                        result = "".join(buf)
                        return result
        finally:
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.stdscr.timeout(1000)

    # -- threads actions ------------------------------------------------------ #
    def attach(self):
        rec = self.current_rec()
        if rec is None:
            return
        session = (rec.get("tmux_session") or "").strip()
        if not session:
            self.set_status("attach: thread has no tmux_session")
            return
        curses.endwin()
        try:
            status = os.system("tmux attach -t " + shlex.quote(session))
        finally:
            self.resume_screen()
        code = os.waitstatus_to_exitcode(status) \
            if hasattr(os, "waitstatus_to_exitcode") else status >> 8
        if code != 0:
            self.set_status(f"tmux attach -t {session} failed (exit {code})")

    def peek(self):
        rec = self.current_rec()
        if rec is None:
            return
        session = (rec.get("tmux_session") or "").strip()
        if not session:
            self.set_status("peek: thread has no tmux_session")
            return
        try:
            proc = subprocess.run(
                ["tmux", "capture-pane", "-p", "-t", session, "-S", "-200"],
                capture_output=True, text=True, timeout=15)
        except Exception as exc:
            self.set_status(f"peek failed: {exc}")
            return
        if proc.returncode != 0:
            self.set_status("peek failed: "
                            + (proc.stderr.strip() or f"exit {proc.returncode}"))
            return
        self.pager(f"peek: {session} (last 200 lines)",
                   proc.stdout.splitlines() or ["(empty pane)"])

    def _mainbase(self, worktree):
        for base in ("main", "master"):
            try:
                proc = subprocess.run(
                    ["git", "-C", worktree, "rev-parse", "--verify",
                     "--quiet", base],
                    capture_output=True, text=True, timeout=15)
            except Exception:
                return None
            if proc.returncode == 0:
                return base
        return None

    def show_diff(self, branch_diff):
        rec = self.current_rec()
        if rec is None:
            return
        worktree = (rec.get("worktree") or "").strip()
        if not worktree or not os.path.isdir(worktree):
            self.set_status("diff: thread has no usable worktree")
            return
        if branch_diff:
            base = self._mainbase(worktree)
            if base is None:
                self.set_status("diff: no main/master ref in worktree")
                return
            git_cmd = f"git -C {shlex.quote(worktree)} diff {base}...HEAD"
        else:
            git_cmd = f"git -C {shlex.quote(worktree)} diff HEAD"
        if shutil.which("delta"):
            pager_cmd = "delta"
        else:
            pager_cmd = os.environ.get("PAGER") or "less -R"
        curses.endwin()
        try:
            os.system(git_cmd + " | " + pager_cmd)
        except Exception as exc:
            self.set_status(f"diff failed: {exc}")
        finally:
            self.resume_screen()

    def view_file(self, path, label):
        if not path or not str(path).strip():
            self.set_status(f"{label}: no path on this record")
            return
        path = str(path).strip()
        if not os.path.isabs(path):
            path = os.path.join(self.state.project_dir, path)
        if not os.path.isfile(path):
            self.set_status(f"{label}: file not found: {path}")
            return
        try:
            with open(path, "r", errors="replace") as f:
                text = f.read()
        except Exception as exc:
            self.set_status(f"{label}: cannot read: {exc}")
            return
        self.pager(f"{label}: {path}", text.splitlines() or ["(empty file)"])

    # -- decisions actions ----------------------------------------------------- #
    def resolve_dialog(self, reject):
        rec = self.current_rec()
        if rec is None:
            return
        if rec.get("status") != "open":
            self.set_status("only OPEN decisions can be resolved/rejected")
            return
        options = rec.get("options") or []
        verb = "reject" if reject else "resolve"
        hint = f" (1..{len(options)} picks an option, or text)" if options \
            else " (text)"
        text = self.prompt_input(f"{verb} {rec.get('id')}{hint}: ")
        if text is None:
            self.set_status(f"{verb}: cancelled")
            return
        text = text.strip()
        if options and text.isdigit() and 1 <= int(text) <= len(options):
            text = str(options[int(text) - 1])
        if not text:
            self.set_status(f"{verb}: empty text refused")
            return
        # CANON [R]: the ONLY board write path -- shell out to board.py.
        cmd = ["python3", BOARD_PY, "resolve-decision",
               "--board", self.state.board,
               "--id", str(rec.get("id")), "--resolution", text]
        if reject:
            cmd += ["--status", "rejected"]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=30)
        except Exception as exc:
            self.set_status(f"{verb} failed: {exc}")
            return
        result = (proc.stdout.strip() or proc.stderr.strip()).splitlines()
        tail = result[-1] if result else ""
        if proc.returncode == 0:
            self.state.reload_file("decisions")
            self.set_status(f"{verb} ok: {clip(tail, 120)}")
        else:
            self.set_status(
                f"{verb} failed (exit {proc.returncode}): {clip(tail, 120)}")

    # -- backlog / ticks actions ------------------------------------------------ #
    def show_full_record(self):
        rec = self.current_rec()
        if rec is None:
            return
        view = VIEWS[self.view]
        body = json.dumps(rec, indent=2, ensure_ascii=False).splitlines()
        self.pager(f"{view} record: {row_key(view, rec)}", body)

    # -- global actions ----------------------------------------------------------- #
    def toggle_pause(self):
        try:
            if os.path.exists(self.state.pause_path):
                os.remove(self.state.pause_path)
                self.set_status("pause file removed (coordinator resumes)")
            else:
                with open(self.state.pause_path, "w"):
                    pass
                self.set_status("pause file created (coordinator pauses)")
        except OSError as exc:
            self.set_status(f"pause toggle failed: {exc}")
        self.state.paused = os.path.exists(self.state.pause_path)

    def run_discovery(self):
        """Active discovery, ONLY on the R key (queue-jump; the coordinator
        tick runs the authoritative pass). Never on the passive path."""
        cmd = ["python3", DISCOVER_PY,
               "--board", os.path.join(self.state.board, "threads.jsonl")]
        self.set_status("discover: running ...")
        self.draw()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=120)
        except Exception as exc:
            self.set_status(f"discover failed: {exc}")
            return
        flagged = []
        for line in proc.stdout.splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            if (rec.get("registered") is False or rec.get("runaway_suspect")
                    or rec.get("orphan")):
                flagged.append(json.dumps(rec, ensure_ascii=False))
        body = proc.stderr.splitlines() or ["(no discovery output)"]
        if flagged:
            body += ["", "-- flagged records (unregistered / runaway / orphan) --"]
            body += flagged
        untracked = (proc.stderr.count("[UNREGISTERED]")
                     + proc.stderr.count("[ORPHAN]"))
        if proc.returncode == 0:
            self.set_status("discover: clean (exit 0)")
        elif proc.returncode == 2:
            self.set_status(f"discover: {untracked} untracked (exit 2)")
        else:
            self.set_status(f"discover: error (exit {proc.returncode})")
        self.pager("resource discovery", body)


# --------------------------------------------------------------------------- #
# entry point
# --------------------------------------------------------------------------- #
def main():
    ap = _UsageParser(
        description="curryflows terminal board TUI (CANON [R]: read-only "
                    "renderer + decision input; never a lifecycle writer)")
    ap.add_argument("--board", required=True, help="board directory")
    ap.add_argument("--render", choices=VIEWS,
                    help="headless: print one plain-text frame of this view "
                         "to stdout and exit (no curses, no color)")
    args = ap.parse_args()

    state = BoardState(args.board)
    if args.render:
        try:
            state.load_all_strict()
        except ValueError as exc:
            sys.stderr.write(f"error: {exc}\n")
            return 1
        for line in render_frame(state, args.render):
            print(line)
        return 0

    if not sys.stdout.isatty():
        sys.stderr.write(
            "board-tui: stdout is not a TTY; use --render "
            "threads|decisions|backlog|ticks for a headless frame\n")
        return 64

    curses.wrapper(lambda stdscr: TUI(stdscr, state).run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
