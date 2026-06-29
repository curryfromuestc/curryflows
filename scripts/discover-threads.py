#!/usr/bin/env python3
"""curryflows: unified, READ-ONLY discovery of live in-flight resources.

The coordinator must never be blind to a running codex /goal or an orphaned
worktree. curryflows exists because a ~1.9B-token codex /goal ran for 3.7 days
undetected by tmux-only monitoring.

Two resource classes are unioned and reconciled against the per-project board:

  A. codex sessions (all READ-ONLY):
     1. ~/.codex/sessions rollout transcripts -- EVERY codex session, any launch
        surface (CLI, VSCode/app-server). Only the FIRST line (session_meta) and
        the file stat are read, never the multi-hundred-MB body.
     2. tmux panes whose foreground command looks like a live Codex TUI.
     3. codex / app-server processes (ps).
  B. git worktrees of a project (`git worktree list`), filtered to curryflows/*
     branches, marked orphan when not tracked by the board.

Output: JSONL on stdout (one record per resource) + a human summary on stderr.
Exit 0 = clean; 2 = an active codex session OR a curryflows worktree is not on a
supplied board (the in-flight-but-untracked condition curryflows must never miss);
64 = usage error.
"""
import argparse
import glob
import json
import os
import subprocess
import sys
import time


# --------------------------------------------------------------------------- #
# A. codex session discovery
# --------------------------------------------------------------------------- #
def read_session_meta(path):
    """Read ONLY the first line (session_meta) of a rollout transcript."""
    try:
        with open(path, "r", errors="replace") as f:
            first = f.readline()
        rec = json.loads(first)
        payload = rec.get("payload", rec)
        return {
            "id": payload.get("id"),
            "originator": payload.get("originator"),
            "source": payload.get("source"),
            "cwd": payload.get("cwd"),
            "cli_version": payload.get("cli_version"),
        }
    except Exception:
        return {"id": None, "originator": None, "source": None,
                "cwd": None, "cli_version": None}


def scan_rollouts(sessions_dir, lookback_h, active_min, runaway_mb):
    now = time.time()
    out = []
    pattern = os.path.join(sessions_dir, "**", "rollout-*.jsonl")
    for path in glob.glob(pattern, recursive=True):
        try:
            st = os.stat(path)
        except OSError:
            continue
        if (now - st.st_mtime) / 3600.0 > lookback_h:
            continue
        meta = read_session_meta(path)
        size_mb = st.st_size / (1024 * 1024)
        idle_min = (now - st.st_mtime) / 60.0
        active = idle_min <= active_min
        out.append({
            "kind": "codex-rollout",
            "session_id": meta["id"],
            "source": meta["source"],
            "originator": meta["originator"],
            "cwd": meta["cwd"],
            "cli_version": meta["cli_version"],
            "rollout": path,
            "size_mb": round(size_mb, 1),
            "idle_min": round(idle_min, 1),
            "active": active,
            "runaway_suspect": active and size_mb >= runaway_mb,
        })
    out.sort(key=lambda r: (not r["active"], -r["size_mb"]))
    return out


def tmux_codex_panes():
    fmt = "#{session_name}:#{window_index}.#{pane_index}|#{pane_current_command}|#{pane_current_path}"
    try:
        res = subprocess.run(["tmux", "list-panes", "-a", "-F", fmt],
                             capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return []
    except Exception:
        return []
    panes = []
    for line in res.stdout.splitlines():
        try:
            target, cmd, cwd = line.split("|", 2)
        except ValueError:
            continue
        if "codex" in cmd.lower():
            panes.append({"kind": "tmux-pane", "pane": target, "cmd": cmd, "cwd": cwd})
    return panes


def codex_procs():
    try:
        res = subprocess.run(["ps", "-eo", "pid,etimes,args"],
                             capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    procs = []
    for line in res.stdout.splitlines()[1:]:
        low = line.lower()
        if "codex" not in low and "app-server" not in low:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        pid, etimes, args = parts
        if "discover-threads" in args:  # do not report ourselves
            continue
        try:
            etimes_i = int(etimes)
        except ValueError:
            etimes_i = -1
        procs.append({
            "kind": "process",
            "pid": int(pid),
            "runtime_h": round(etimes_i / 3600.0, 1),
            "args": args[:160],
        })
    return procs


# --------------------------------------------------------------------------- #
# B. worktree discovery
# --------------------------------------------------------------------------- #
def discover_worktrees(project, board_provided, board_branches):
    """List a project's git worktrees, filter to curryflows/* branches, mark
    orphan when a curryflows worktree is not tracked by the board."""
    if not project:
        return []
    try:
        res = subprocess.run(["git", "-C", project, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return []
    except Exception:
        return []
    trees, cur = [], {}
    for line in res.stdout.splitlines():
        if line.startswith("worktree "):
            if cur:
                trees.append(cur)
            cur = {"path": line[len("worktree "):]}
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
        elif line == "" and cur:
            trees.append(cur)
            cur = {}
    if cur:
        trees.append(cur)

    recs = []
    for w in trees:
        branch = w.get("branch", "")
        short = branch.replace("refs/heads/", "")
        is_cf = short.startswith("curryflows/")
        if not is_cf:
            continue
        rec = {"kind": "worktree", "path": w.get("path"), "branch": short}
        rec["orphan"] = (short not in board_branches) if board_provided else None
        recs.append(rec)
    return recs


# --------------------------------------------------------------------------- #
# board
# --------------------------------------------------------------------------- #
def load_board(board_path):
    """Return (provided, codex_session_ids, branches). `provided` is True when a
    board path was given and exists -- even if it registers zero threads. An
    empty board still means every in-flight resource is untracked, which is the
    most dangerous case, so it must be distinguished from "no board supplied"."""
    codex_ids, branches = set(), set()
    if not board_path or not os.path.exists(board_path):
        return False, codex_ids, branches
    with open(board_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("codex_session"):
                codex_ids.add(rec["codex_session"])
            if rec.get("branch"):
                branches.add(rec["branch"])
    return True, codex_ids, branches


class _UsageParser(argparse.ArgumentParser):
    """Exit 64 on usage error so it never collides with exit 2 (the
    in-flight-but-untracked signal callers key on)."""
    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)


def main():
    ap = _UsageParser(description="curryflows read-only resource discovery")
    ap.add_argument("--sessions-dir",
                    default=os.environ.get("CODEX_SESSIONS_DIR",
                                           os.path.expanduser("~/.codex/sessions")))
    ap.add_argument("--project", default=None,
                    help="project repo path; lists its curryflows/* worktrees")
    ap.add_argument("--board", default=None,
                    help="board/threads.jsonl; marks untracked in-flight resources")
    ap.add_argument("--lookback-hours", type=float, default=96.0)
    ap.add_argument("--active-min", type=float, default=10.0,
                    help="rollout written within N minutes counts as active")
    ap.add_argument("--runaway-mb", type=float, default=50.0,
                    help="active rollout >= N MB is flagged a runaway suspect")
    ap.add_argument("--summary-only", action="store_true")
    args = ap.parse_args()

    board_provided, board_ids, board_branches = load_board(args.board)

    rollouts = scan_rollouts(args.sessions_dir, args.lookback_hours,
                             args.active_min, args.runaway_mb)
    panes = tmux_codex_panes()
    procs = codex_procs()
    worktrees = discover_worktrees(args.project, board_provided, board_branches)

    for r in rollouts:
        if r["session_id"] is not None and board_provided:
            r["registered"] = r["session_id"] in board_ids
        else:
            r["registered"] = None

    if not args.summary_only:
        for r in rollouts + panes + procs + worktrees:
            print(json.dumps(r, ensure_ascii=False))

    active = [r for r in rollouts if r["active"]]
    runaway = [r for r in rollouts if r["runaway_suspect"]]
    unregistered = [r for r in active if r.get("registered") is False]
    orphans = [w for w in worktrees if w.get("orphan") is True]

    def w(s):
        sys.stderr.write(s + "\n")

    w("== curryflows resource discovery ==")
    w(f"rollouts(<{args.lookback_hours}h): {len(rollouts)}  "
      f"active(<{args.active_min}m): {len(active)}  "
      f"runaway-suspect(>={args.runaway_mb}MB & active): {len(runaway)}")
    w(f"tmux codex panes: {len(panes)}  codex/app-server procs: {len(procs)}  "
      f"curryflows worktrees: {len(worktrees)}  orphan: {len(orphans)}")
    for r in active:
        flag = "  <<< RUNAWAY-SUSPECT" if r["runaway_suspect"] else ""
        if r.get("registered") is True:
            reg = " [REGISTERED]"
        elif r.get("registered") is False:
            reg = " [UNREGISTERED]"
        else:
            reg = ""
        w(f"  ACTIVE codex {r['session_id']} src={r['source']} {r['size_mb']}MB "
          f"idle={r['idle_min']}m cwd={r['cwd']}{reg}{flag}")
    for wt in worktrees:
        tag = " [ORPHAN]" if wt.get("orphan") is True else ""
        w(f"  WORKTREE {wt['branch']} -> {wt['path']}{tag}")

    # Exit 2 when an in-flight resource is untracked against a supplied board:
    # the precise condition curryflows exists to never miss. An empty board still
    # counts as supplied, so "nothing registered yet something running" trips it.
    if board_provided and (unregistered or orphans):
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
