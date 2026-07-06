#!/usr/bin/env python3
"""curryflows: the sole writer of the board JSONL files.

The coordinator's context is expendable (auto-compact may rewrite it into a
lossy summary at any point; anything that must survive lives here), so the
durable board under <board>/ is the source of truth. Four files are written
exclusively through this CLI:

  <board>/threads.jsonl    -- one record per in-flight thread (state machine)
  <board>/decisions.jsonl  -- the human-decision queue (barriers)
  <board>/ticks.jsonl      -- append-only durable tick history (record-tick)
  <board>/backlog.jsonl    -- task supply queue (CANON [M]; keeps rejection memory)

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
  session-reaped codex tmux session reaped early (rare; the normal flow keeps the
                 session alive until merged so merge conflicts can be steered
                 back to the live worker, see CANON [B])
  merged         merged to main (terminal)
  rolled-back    discarded (terminal)

Decision barriers (CANON [E]): seal-contract, merge-main, outward-irreversible,
model-divergence.

Backlog statuses (CANON [M]): candidate -> scoping -> sealed-ready -> launched,
or rejected. Rejected items are never deleted (rejection memory), and dedup_key
is unique across all items, so a previously rejected task cannot silently
reappear under a fresh id.

Subcommands: upsert-thread, post-decision, resolve-decision, record-tick,
upsert-backlog, list-threads, list-decisions, list-backlog, list-ticks,
validate-contract, panel-args. Usage errors exit 64.
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
BACKLOG_STATUSES = (
    "candidate", "scoping", "sealed-ready", "launched", "rejected",
)
CONTRACT_REQUIRED = (
    "outcome", "verification", "constraints", "boundaries",
    "iteration", "budget", "blocked_stop", "preconditions",
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


def backlog_path(board):
    return os.path.join(board, "backlog.jsonl")


def fail(msg, code=1):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


# --------------------------------------------------------------------------- #
# upsert-thread
# --------------------------------------------------------------------------- #
def cmd_upsert_thread(args):
    if args.state is not None and args.state not in STATES:
        fail(f"illegal state '{args.state}'; allowed: {', '.join(STATES)}")

    # CANON [N] fail-closed guard: blocked-human means "a human decision blocks
    # this thread", so that decision must EXIST on the decision surface first.
    # Incident: design-scale sat "waiting on the user" for 3 days while
    # decisions.jsonl had no corresponding item -- the user had nothing to act
    # on. Post the decision (board.py post-decision) BEFORE blocking the thread.
    if args.state == "blocked-human":
        open_for_thread = [
            r for r in read_jsonl_strict(decisions_path(args.board))
            if r.get("status") == "open" and r.get("thread") == args.id
        ]
        if not open_for_thread:
            fail(
                f"state=blocked-human requires an OPEN decision with "
                f"thread='{args.id}' on the board; post it first "
                "(board.py post-decision), otherwise the wait is invisible "
                "to the human"
            )

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
    """Append a decision item, or -- with --reopen -- reopen an existing id IN
    PLACE. A plain post with an id that already exists is refused fail-closed:
    appending a second row under the same id is exactly the corruption found in
    tick-81 (duplicate rows; resolve-decision then only updated the first, so
    the dashboard kept showing a stale open copy). Reopen keeps one row per id,
    preserves prior outcomes in prior_resolutions, and collapses any legacy
    duplicate rows of that id."""
    if args.barrier not in BARRIERS:
        fail(f"illegal barrier '{args.barrier}'; allowed: {', '.join(BARRIERS)}")
    if not args.recommendation or not args.recommendation.strip():
        fail("recommendation must be non-empty")
    if not args.evidence or not args.evidence.strip():
        fail("evidence must be non-empty")

    rows = read_jsonl_strict(decisions_path(args.board))
    dupes = [r for r in rows if r.get("id") == args.id]

    if dupes and not args.reopen:
        fail(
            f"decision id '{args.id}' already exists "
            f"(status={dupes[0].get('status')}); use --reopen to reopen it "
            "in place, or pick a new id"
        )
    if args.reopen and not dupes:
        fail(f"--reopen: no existing decision with id '{args.id}'")

    fresh = {
        "barrier": args.barrier,
        "thread": args.thread,
        "summary": args.summary,
        "recommendation": args.recommendation,
        "evidence": args.evidence,
        "divergence": args.divergence,
        "options": args.options.split("|") if args.options else None,
        "status": "open",
        "resolution": None,
    }

    if args.reopen:
        rec = dupes[0]
        history = rec.get("prior_resolutions") or []
        for d in dupes:
            if d.get("resolution") is not None or d.get("status") != "open":
                history.append({
                    "status": d.get("status"),
                    "resolution": d.get("resolution"),
                    "resolved": d.get("updated"),
                })
        rec.update(fresh)
        rec["prior_resolutions"] = history
        rec["reopened"] = now_iso()
        # collapse legacy duplicate rows: keep only the first occurrence
        rows = [r for r in rows if r.get("id") != args.id or r is rec]
    else:
        rec = {"id": args.id, **fresh, "created": now_iso()}
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
    # update EVERY row bearing the id, not just the first: legacy boards may
    # carry duplicate rows from the pre---reopen era (tick-81 incident), and a
    # resolution that only lands on the first copy leaves a stale open copy
    # visible on the dashboard.
    matched = [rec for rec in rows if rec.get("id") == args.id]
    if not matched:
        fail(f"no decision with id '{args.id}'")

    for rec in matched:
        rec["resolution"] = args.resolution
        rec["status"] = args.status
        rec["updated"] = now_iso()
    write_jsonl_atomic(decisions_path(args.board), rows)
    print(json.dumps(matched[0], ensure_ascii=False))
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
        # decision aging (computed at read time, never persisted): the surface
        # is pull-only, so the coordinator must resurface old open decisions in
        # its tick summary -- age_hours is the input to that nagging rule.
        now = datetime.now(timezone.utc)
        for rec in rows:
            ref = rec.get("reopened") or rec.get("created")
            if ref:
                try:
                    then = datetime.fromisoformat(ref)
                    rec = dict(rec)
                    rec["age_hours"] = round((now - then).total_seconds() / 3600, 1)
                except ValueError:
                    pass
            print(json.dumps(rec, ensure_ascii=False))
        return 0
    for rec in rows:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# upsert-backlog / list-backlog (task supply queue, CANON [M])
# --------------------------------------------------------------------------- #
def cmd_upsert_backlog(args):
    """Create/merge a backlog item. The backlog is the durable task-supply queue
    behind the per-tick replenish step (CANON [M]): candidates go in, sealed-ready
    items are launchable, rejected items STAY as rejection memory. Two fail-closed
    guards give the memory teeth:

      * a NEW item whose dedup_key collides with any existing item is refused --
        re-proposing a previously seen (possibly rejected) task must reuse that
        item's backlog_id, so its history stays attached and visible;
      * status=rejected requires a reject_reason (given now or already on record)
        -- a rejection that cannot say why cannot stop future re-proposals.
    """
    if args.status is not None and args.status not in BACKLOG_STATUSES:
        fail(f"illegal status '{args.status}'; allowed: {', '.join(BACKLOG_STATUSES)}")

    provided = {}
    for key in ("summary", "status", "dedup_key", "rationale",
                "contract", "thread", "reject_reason"):
        val = getattr(args, key)
        if val is not None:
            provided[key] = val

    rows = read_jsonl_strict(backlog_path(args.board))
    found = None
    for rec in rows:
        if rec.get("backlog_id") == args.id:
            found = rec
            break

    if found is None:
        if not provided.get("summary", "").strip():
            fail("a new backlog item requires a non-empty --summary")
        key = provided.get("dedup_key")
        if key:
            for rec in rows:
                if rec.get("dedup_key") == key:
                    fail(
                        f"dedup_key '{key}' already used by backlog item "
                        f"'{rec.get('backlog_id')}' (status={rec.get('status')}); "
                        "reuse that item's id instead of re-proposing it"
                    )
        rec = {"backlog_id": args.id, "status": "candidate"}
        rec.update(provided)
        rows.append(rec)
    else:
        found.update(provided)
        rec = found

    if rec.get("status") == "rejected" and not (rec.get("reject_reason") or "").strip():
        fail("status=rejected requires --reject-reason (rejection memory must say why)")

    rec["updated"] = now_iso()
    write_jsonl_atomic(backlog_path(args.board), rows)
    print(json.dumps(rec, ensure_ascii=False))
    return 0


def cmd_list_backlog(args):
    if args.status is not None and args.status not in BACKLOG_STATUSES:
        fail(f"illegal status '{args.status}'; allowed: {', '.join(BACKLOG_STATUSES)}")
    rows = read_jsonl_strict(backlog_path(args.board))
    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    for rec in rows:
        print(json.dumps(rec, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- #
# list-ticks (bounded read of the append-only tick history)
# --------------------------------------------------------------------------- #
def cmd_list_ticks(args):
    """Emit the last N tick records. ticks.jsonl is append-only and grows for a
    campaign's whole life, so the per-tick rehydrate must never slurp the whole
    file -- this is the bounded reader. Only the returned window is parsed and
    validated (strictly, with real line numbers); earlier lines are not read as
    JSON, which also keeps old corruption from blocking current ticks."""
    if args.last is not None and args.last < 1:
        fail("--last must be >= 1", 64)
    path = ticks_path(args.board)
    if not os.path.isfile(path):
        return 0
    with open(path, "r", errors="strict") as f:
        numbered = [(n, ln) for n, ln in enumerate(f, start=1) if ln.strip()]
    if args.last is not None:
        numbered = numbered[-args.last:]
    for lineno, line in numbered:
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
    sys.stderr.write(
        f"contract OK: all {len(CONTRACT_REQUIRED)} required fields present\n"
    )
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
    # invisible-wait tripwire: a summary that claims to be waiting on a human
    # while the decision surface is empty is the exact failure that left
    # design-scale blocked for 3 days with nothing for the user to act on.
    # Warning only (not fail): the text match is heuristic.
    if re.search(r"待用户|待人类|等人类|等待用户|等用户", summ):
        open_count = sum(
            1 for r in read_jsonl_strict(decisions_path(args.board))
            if r.get("status") == "open"
        )
        if open_count == 0:
            sys.stderr.write(
                "WARNING: tick summary says waiting-on-human but the board has "
                "0 open decisions -- post the decision in the SAME tick "
                "(board.py post-decision) or the wait is invisible\n"
            )
    rows = read_jsonl_strict(ticks_path(args.board))
    rows.append(rec)
    write_jsonl_atomic(ticks_path(args.board), rows)
    print(json.dumps(rec, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# panel-args (single source of truth for review-panel.js dispatch)
# --------------------------------------------------------------------------- #
def cmd_panel_args(args):
    """Emit ready-to-use args JSON for workflows/review-panel.js straight from
    threads.jsonl. The coordinator must NOT hand-build the threads[] array:
    incident wf_3a62dfb1-a14 hand-built it as bare id strings and burned a full
    5-agent panel (206K tokens) reviewing literal 'undefined'. This subcommand
    is the single source of truth -- unknown ids and records missing the fields
    the panel needs are refused fail-closed."""
    ids = [s.strip() for s in args.threads.split(",") if s.strip()]
    if not ids:
        fail("--threads requires at least one thread id", 64)

    rows = read_jsonl_strict(threads_path(args.board))
    by_id = {r.get("thread_id"): r for r in rows}
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        fail(f"unknown thread id(s): {', '.join(unknown)}")

    board_abs = os.path.abspath(args.board)
    skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_dir = os.path.abspath(args.project) if args.project else \
        os.path.dirname(os.path.dirname(board_abs))

    threads, incomplete = [], []
    for tid in ids:
        r = by_id[tid]
        t = {
            "thread_id": tid,
            "worktree": r.get("worktree"),
            "branch": r.get("branch"),
            "codex_session": r.get("codex_session"),
            "contract": r.get("contract"),
            "worker_model": r.get("worker_model") or "codex",
            "state": r.get("state"),
        }
        lacking = [k for k in ("worktree", "branch", "contract")
                   if not (t.get(k) or "").strip()]
        if lacking:
            incomplete.append(f"{tid}: missing {', '.join(lacking)}")
        threads.append(t)
    if incomplete:
        fail("board record(s) incomplete for panel dispatch: "
             + "; ".join(incomplete))

    print(json.dumps(
        {"board": board_abs, "skillDir": skill_dir,
         "projectDir": project_dir, "threads": threads},
        ensure_ascii=False,
    ))
    return 0


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
    p.add_argument("--reopen", action="store_true",
                   help="reopen an existing decision id in place (keeps one "
                        "row per id; prior outcome moves to prior_resolutions)")
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

    p = sub.add_parser("upsert-backlog",
                       help="create/merge a backlog item (task supply queue)")
    add_board(p)
    p.add_argument("--id", required=True, dest="id")
    p.add_argument("--summary")
    p.add_argument("--status",
                   help="candidate|scoping|sealed-ready|launched|rejected")
    p.add_argument("--dedup-key", dest="dedup_key",
                   help="stable key of the underlying task; unique across items")
    p.add_argument("--rationale")
    p.add_argument("--contract", help="path to the drafted/sealed contract")
    p.add_argument("--thread", help="thread_id once launched")
    p.add_argument("--reject-reason", dest="reject_reason")
    p.set_defaults(func=cmd_upsert_backlog)

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

    p = sub.add_parser("list-backlog", help="dump backlog.jsonl")
    add_board(p)
    p.add_argument("--status", help="filter by status")
    p.set_defaults(func=cmd_list_backlog)

    p = sub.add_parser("list-ticks",
                       help="dump the last N tick records (bounded read; "
                            "never slurps the whole append-only history)")
    add_board(p)
    p.add_argument("--last", type=int,
                   help="only the last N records (also the validation window)")
    p.set_defaults(func=cmd_list_ticks)

    p = sub.add_parser("validate-contract",
                       help="fail-closed seal-contract precondition")
    p.add_argument("--file", required=True, help="sealed contract file path")
    p.set_defaults(func=cmd_validate_contract)

    p = sub.add_parser("panel-args",
                       help="emit review-panel.js args JSON from threads.jsonl "
                            "(single source of truth; never hand-build threads[])")
    add_board(p)
    p.add_argument("--threads", required=True,
                   help="comma-separated thread ids to review")
    p.add_argument("--project",
                   help="project dir override (default: board's grandparent)")
    p.set_defaults(func=cmd_panel_args)

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
