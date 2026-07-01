#!/usr/bin/env python3
"""curryflows: the sole writer of the board JSONL files.

The coordinator's own context is unreliable (it parks, compacts, spans wake-ups),
so the durable board under <board>/ is the source of truth. Three files are written
exclusively through this CLI:

  <board>/threads.jsonl    -- one record per in-flight thread (state machine)
  <board>/decisions.jsonl  -- the human-decision queue (barriers)
  <board>/ticks.jsonl      -- append-only durable tick history (record-tick)

NEVER hand-edit threads.jsonl / decisions.jsonl / ticks.jsonl. A hand-edit easily corrupts a
line, and render-board.py silently skips malformed lines -- so a corrupted line
silently drops a thread's state. board.py is the single writer: every write is
atomic (same-dir temp file + os.replace) and every illegal enum / missing
required field is rejected fail-closed (the file is never partially written).

Unlike the renderer, board.py READS strictly: a malformed JSONL line is NOT
skipped, it raises with the offending line number, because the whole point is to
surface corruption rather than hide it.

Authoritative thread state machine (CANON [A]):

  ready -> running -> idle -> reviewed -> committed -> verified
        -> session-reaped -> merged | rolled-back

  plus blocked-human (escalation; reachable from any state).

  ready          contract sealed, not yet started
  running        codex /goal worker is executing
  idle           worker hit budget / blocked-stop / self-declared done, awaiting review
  reviewed       reviewer finished, last_verdict recorded
  committed      work committed to its own branch (durability; not merge, not push)
  verified       independent re-run on the committed branch worktree passed
  session-reaped codex tmux session reaped (process freed); worktree+branch kept for human merge
  merged         merged to main (terminal)
  rolled-back    discarded (terminal)

Decision barriers (CANON [E]): seal-contract, merge-main, outward-irreversible,
model-divergence.

Subcommands: upsert-thread, post-decision, resolve-decision, record-tick,
list-threads, list-decisions, validate-contract. Usage errors exit 64.
"""
import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# canonical enumerations (CANON [A] / [E])
# --------------------------------------------------------------------------- #
STATES = (
    "ready", "running", "idle", "reviewed", "committed", "verified",
    "session-reaped", "merged", "rolled-back", "blocked-human",
)
BARRIERS = (
    "seal-contract", "merge-main", "outward-irreversible", "model-divergence",
)
CONTRACT_REQUIRED = (
    "outcome", "verification", "constraints", "boundaries",
    "iteration", "budget", "blocked_stop",
)


class _UsageParser(argparse.ArgumentParser):
    """Exit 64 on usage error (matches discover-threads.py), so a malformed
    invocation never collides with the data-validation failure exits."""

    def error(self, message):
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        sys.exit(64)


# --------------------------------------------------------------------------- #
# strict JSONL I/O
# --------------------------------------------------------------------------- #
def read_jsonl_strict(path):
    """Read a JSONL file into a list of dicts. A malformed line is fatal and
    raises ValueError naming the 1-based line number -- corruption is surfaced,
    never silently skipped. A missing file yields []."""
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, "r", errors="strict") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception as exc:
                raise ValueError(
                    f"corrupted JSONL at {path}:{lineno}: {exc}"
                ) from exc
            if not isinstance(rec, dict):
                raise ValueError(
                    f"corrupted JSONL at {path}:{lineno}: not a JSON object"
                )
            rows.append(rec)
    return rows


def write_jsonl_atomic(path, rows):
    """Atomically (re)write a JSONL file: write all rows to a same-dir temp file,
    fsync, then os.replace. A failure mid-write never leaves a partial file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".board-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            for rec in rows:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def threads_path(board):
    return os.path.join(board, "threads.jsonl")


def decisions_path(board):
    return os.path.join(board, "decisions.jsonl")


def ticks_path(board):
    return os.path.join(board, "ticks.jsonl")


def fail(msg, code=1):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


# --------------------------------------------------------------------------- #
# upsert-thread
# --------------------------------------------------------------------------- #
def cmd_upsert_thread(args):
    if args.state is not None and args.state not in STATES:
        fail(f"illegal state '{args.state}'; allowed: {', '.join(STATES)}")

    # map of provided CLI fields -> record keys (only set when explicitly given)
    provided = {}
    field_map = [
        ("state", "state"),
        ("branch", "branch"),
        ("worktree", "worktree"),
        ("tmux_session", "tmux_session"),
        ("codex_session", "codex_session"),
        ("budget_tokens", "budget_tokens"),
        ("budget_spent", "budget_spent"),
        ("contract", "contract"),
        ("last_verdict", "last_verdict"),
        ("attempt", "attempt"),
    ]
    for arg_name, rec_key in field_map:
        val = getattr(args, arg_name)
        if val is not None:
            provided[rec_key] = val

    rows = read_jsonl_strict(threads_path(args.board))
    found = None
    for rec in rows:
        if rec.get("thread_id") == args.id:
            found = rec
            break

    if found is None:
        rec = {"thread_id": args.id}
        rec.update(provided)
        rec["updated"] = now_iso()
        rows.append(rec)
    else:
        found.update(provided)
        found["updated"] = now_iso()
        rec = found

    write_jsonl_atomic(threads_path(args.board), rows)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# post-decision
# --------------------------------------------------------------------------- #
def cmd_post_decision(args):
    if args.barrier not in BARRIERS:
        fail(f"illegal barrier '{args.barrier}'; allowed: {', '.join(BARRIERS)}")
    if not args.recommendation or not args.recommendation.strip():
        fail("recommendation must be non-empty")
    if not args.evidence or not args.evidence.strip():
        fail("evidence must be non-empty")

    rec = {
        "id": args.id,
        "barrier": args.barrier,
        "thread": args.thread,
        "summary": args.summary,
        "recommendation": args.recommendation,
        "evidence": args.evidence,
        "divergence": args.divergence,
        "options": args.options.split("|") if args.options else None,
        "status": "open",
        "resolution": None,
        "created": now_iso(),
    }

    rows = read_jsonl_strict(decisions_path(args.board))
    rows.append(rec)
    write_jsonl_atomic(decisions_path(args.board), rows)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# resolve-decision
# --------------------------------------------------------------------------- #
def cmd_resolve_decision(args):
    if args.status not in ("resolved", "rejected"):
        fail(f"illegal status '{args.status}'; allowed: resolved, rejected")

    rows = read_jsonl_strict(decisions_path(args.board))
    found = None
    for rec in rows:
        if rec.get("id") == args.id:
            found = rec
            break
    if found is None:
        fail(f"no decision with id '{args.id}'")

    found["resolution"] = args.resolution
    found["status"] = args.status
    found["updated"] = now_iso()
    write_jsonl_atomic(decisions_path(args.board), rows)
    print(json.dumps(found, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# list-threads / list-decisions
# --------------------------------------------------------------------------- #
def cmd_list_threads(args):
    rows = read_jsonl_strict(threads_path(args.board))
    if args.open:
        terminal = {"merged", "rolled-back"}
        rows = [r for r in rows if r.get("state") not in terminal]
    for rec in rows:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_list_decisions(args):
    rows = read_jsonl_strict(decisions_path(args.board))
    if args.open:
        rows = [r for r in rows if r.get("status") == "open"]
    for rec in rows:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# validate-contract (CANON [D], fail-closed seal precondition)
# --------------------------------------------------------------------------- #
_KEY_RE = re.compile(r"^[`\"']?([A-Za-z_][A-Za-z0-9_\-]*)[`\"']?\s*:\s*(.*)$")


def parse_contract(text):
    """Parse a sealed contract file into {normalized_key: has_nonempty_value}.

    Supports plain 'key: value' lines and the task-contracts/task.md style where
    the contract lives in a ```yaml fenced block. When fenced blocks are present
    they are authoritative (the surrounding markdown is documentation); otherwise
    the whole file is scanned. A key with no inline value but with more-indented
    child lines counts as present (YAML block scalar / mapping)."""
    fences = re.findall(r"```ya?ml[^\n]*\n(.*?)```", text, re.DOTALL)
    region = "\n".join(fences) if fences else text
    lines = region.splitlines()

    fields = {}
    for i, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        s = stripped
        if s.startswith("- "):
            s = s[2:].strip()
        m = _KEY_RE.match(s)
        if not m:
            continue
        key = m.group(1).strip().lower().replace("-", "_")
        val = m.group(2).strip().strip("\"'")
        present = bool(val)
        if not present:
            base_indent = len(raw) - len(raw.lstrip())
            for nxt in lines[i + 1:]:
                if not nxt.strip():
                    continue
                nxt_indent = len(nxt) - len(nxt.lstrip())
                present = nxt_indent > base_indent
                break
        if key not in fields or (present and not fields[key]):
            fields[key] = present
    return fields


def cmd_validate_contract(args):
    if not os.path.isfile(args.file):
        sys.stderr.write(f"error: contract file not found: {args.file}\n")
        return 1
    with open(args.file, "r", errors="strict") as f:
        text = f.read()
    fields = parse_contract(text)
    missing = [k for k in CONTRACT_REQUIRED if not fields.get(k)]
    if missing:
        sys.stderr.write(
            "contract INVALID: missing/empty required field(s): "
            + ", ".join(missing) + "\n"
        )
        return 1
    sys.stderr.write("contract OK: all 7 required fields present\n")
    print(args.file)
    return 0


# --------------------------------------------------------------------------- #
# record-tick (append-only durable tick history)
# --------------------------------------------------------------------------- #
def cmd_record_tick(args):
    """Append one tick record to ticks.jsonl. The coordinator prepares the tick
    JSON as a data file (allowed under CANON [J]); board.py is still the sole
    writer -- it validates required fields and appends atomically. This is the
    ONLY sanctioned way to write ticks.jsonl (never hand-append / `>` it)."""
    try:
        with open(args.file, "r", errors="strict") as f:
            rec = json.load(f)
    except FileNotFoundError:
        return fail(f"tick file not found: {args.file}", 64)
    except Exception as exc:
        return fail(f"cannot parse tick JSON {args.file}: {exc}", 64)
    if not isinstance(rec, dict):
        return fail("tick JSON must be a JSON object", 64)
    if not isinstance(rec.get("tick"), int) or isinstance(rec.get("tick"), bool):
        return fail("tick record requires integer field 'tick'")
    summ = rec.get("summary")
    if not isinstance(summ, str) or not summ.strip():
        return fail("tick record requires non-empty string field 'summary'")
    rec.setdefault("reviews", [])
    rec.setdefault("decisions_made", [])
    rec.setdefault("operator", {})
    if not rec.get("ts"):
        rec["ts"] = now_iso()
    rows = read_jsonl_strict(ticks_path(args.board))
    rows.append(rec)
    write_jsonl_atomic(ticks_path(args.board), rows)
    print(json.dumps(rec, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# argparse wiring
# --------------------------------------------------------------------------- #
def build_parser():
    ap = _UsageParser(description="curryflows board JSONL writer (sole writer)")
    sub = ap.add_subparsers(dest="command", required=True)

    def add_board(p):
        p.add_argument("--board", required=True, help="board directory")

    p = sub.add_parser("upsert-thread", help="create/merge a thread record")
    add_board(p)
    p.add_argument("--id", required=True, dest="id")
    p.add_argument("--state")
    p.add_argument("--branch")
    p.add_argument("--worktree")
    p.add_argument("--tmux-session", dest="tmux_session")
    p.add_argument("--codex-session", dest="codex_session")
    p.add_argument("--budget-tokens", dest="budget_tokens", type=int)
    p.add_argument("--budget-spent", dest="budget_spent", type=int)
    p.add_argument("--contract")
    p.add_argument("--last-verdict", dest="last_verdict")
    p.add_argument("--attempt", type=int)
    p.set_defaults(func=cmd_upsert_thread)

    p = sub.add_parser("post-decision", help="append a human-decision item")
    add_board(p)
    p.add_argument("--id", required=True, dest="id")
    p.add_argument("--barrier", required=True)
    p.add_argument("--thread", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--recommendation", required=True)
    p.add_argument("--evidence", required=True)
    p.add_argument("--divergence")
    p.add_argument("--options", help='pipe-separated, e.g. "a|b|c"')
    p.set_defaults(func=cmd_post_decision)

    p = sub.add_parser("resolve-decision", help="resolve/reject a decision")
    add_board(p)
    p.add_argument("--id", required=True, dest="id")
    p.add_argument("--resolution", required=True)
    p.add_argument("--status", default="resolved",
                   choices=("resolved", "rejected"))
    p.set_defaults(func=cmd_resolve_decision)

    p = sub.add_parser("record-tick",
                       help="append a tick record to ticks.jsonl (durable history)")
    add_board(p)
    p.add_argument("--file", required=True,
                   help="JSON file with the tick record: "
                        "{tick:int, summary:str, reviews?, decisions_made?, operator?, ts?}")
    p.set_defaults(func=cmd_record_tick)

    p = sub.add_parser("list-threads", help="dump threads.jsonl")
    add_board(p)
    p.add_argument("--open", action="store_true",
                   help="exclude terminal states (merged, rolled-back)")
    p.set_defaults(func=cmd_list_threads)

    p = sub.add_parser("list-decisions", help="dump decisions.jsonl")
    add_board(p)
    p.add_argument("--open", action="store_true",
                   help="only status=open decisions")
    p.set_defaults(func=cmd_list_decisions)

    p = sub.add_parser("validate-contract",
                       help="fail-closed seal-contract precondition")
    p.add_argument("--file", required=True, help="sealed contract file path")
    p.set_defaults(func=cmd_validate_contract)

    return ap


def main():
    ap = build_parser()
    args = ap.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        # corruption surfaced by read_jsonl_strict: fail loud (with line number),
        # non-zero, but without a noisy traceback.
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
