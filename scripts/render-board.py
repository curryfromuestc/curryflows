#!/usr/bin/env python3
"""curryflows: render the consolidated board (jsonl) into a human HTML dashboard.

The coordinator's context is disposable (each tick starts fresh, CANON [Q]), so
the board files are the source of truth. This script deterministically renders
them into one self-contained HTML file a human can open in a browser and watch:

  <board>/threads.jsonl    -> threads table (state, budget, codex session)
  <board>/decisions.jsonl  -> open human-decision queue
  <board>/ticks.jsonl      -> recent tick summaries (durable history)
  <board>/backlog.jsonl    -> task supply queue (watermark + rejection memory)

Output: <board>/dashboard.html (or --out). READ-ONLY w.r.t. the jsonl inputs.
`render_html()` is importable so serve-board.py can re-render live per request.
Exit 0 = rendered; 64 = usage error. Malformed lines are skipped, not fatal.
"""
import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def read_jsonl(path):
    rows = []
    if not os.path.isfile(path):
        return rows
    with open(path, "r", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue  # skip malformed line, never fatal
    return rows


def short(s, n=8):
    s = "" if s is None else str(s)
    return s[:n] if len(s) > n else s


def esc(s):
    return html.escape("" if s is None else str(s))


def budget_cell(t):
    spent, total = t.get("budget_spent"), t.get("budget_tokens")
    if total is None and spent is None:
        return "-"
    s = "?" if spent is None else f"{int(spent):,}"
    tt = "?" if total is None else f"{int(total):,}"
    return f"{s}/{tt}"


# --------------------------------------------------------------------------- #
# theme (light, academic palette)
# --------------------------------------------------------------------------- #
CSS = """
:root{--bg:#ffffff;--panel:#f4f5f7;--ink:#374e55;--muted:#74808a;--line:#bfc6c9;
--accent:#2c5f8d;--warn:#df8f44;--bad:#a4161a;--good:#3f7d63;--head:#374e55;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;}
.wrap{max-width:1100px;margin:0 auto;padding:24px 20px 60px;}
h1{font-size:20px;margin:0 0 4px;color:var(--head);letter-spacing:.3px}
.meta{color:var(--muted);font-size:13px;margin-bottom:20px}
h2{font-size:15px;margin:28px 0 10px;color:var(--head);
border-bottom:1px solid var(--line);padding-bottom:6px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px}
td.mono,code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.empty{color:var(--muted);font-style:italic;padding:8px 0}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;
font-weight:600;border:1px solid var(--line);white-space:nowrap}
.b-ready{color:var(--muted)}
.b-running{color:#fff;background:var(--accent);border-color:var(--accent)}
.b-idle{color:#fff;background:var(--warn);border-color:var(--warn)}
.b-reviewed{color:var(--ink);border-color:var(--line)}
.b-committed{color:var(--ink);border-color:var(--line)}
.b-verified{color:#fff;background:var(--good);border-color:var(--good)}
.b-session-reaped{color:var(--muted)}
.b-blocked-human{color:#fff;background:var(--bad);border-color:var(--bad)}
.b-merged{color:#fff;background:var(--good);border-color:var(--good)}
.b-rolled-back{color:var(--muted);text-decoration:line-through}
.b-bar{color:#fff;background:var(--warn);border-color:var(--warn)}
.b-candidate{color:var(--muted)}
.b-scoping{color:var(--ink);border-color:var(--warn)}
.b-sealed-ready{color:#fff;background:var(--accent);border-color:var(--accent)}
.b-launched{color:var(--good);border-color:var(--good)}
.b-rejected{color:var(--muted);text-decoration:line-through}
.tick{background:var(--panel);border:1px solid var(--line);border-radius:6px;
padding:12px 14px;margin:10px 0}
.tick h3{margin:0 0 8px;font-size:13px;color:var(--accent)}
.tick pre{margin:0;white-space:pre-wrap;word-break:break-word;font-size:12.5px;
font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--ink)}
.fail{color:var(--bad);font-weight:600}
.reap{color:var(--muted);font-size:12px;margin-top:6px}
.ev{color:var(--muted);font-size:12px}
"""


def _badge(state):
    cls = "b-" + str(state or "").replace("/", "-")
    return f'<span class="badge {cls}">{esc(state or "-")}</span>'


def _bar_badge(barrier):
    return f'<span class="badge b-bar">{esc(barrier or "-")}</span>'


def render_threads(threads):
    out = ["<h2>线程 (threads)</h2>"]
    if not threads:
        return out + ['<div class="empty">无在途线程</div>']
    order = {"blocked-human": 0, "running": 1, "idle": 2, "reviewed": 3,
             "committed": 4, "verified": 5, "session-reaped": 6, "ready": 7,
             "merged": 8, "rolled-back": 9}
    threads = sorted(threads, key=lambda t: (order.get(t.get("state"), 9),
                                             str(t.get("thread_id"))))
    out.append("<table><thead><tr>"
               "<th>thread</th><th>state</th><th>branch</th>"
               "<th>budget spent/total</th><th>codex</th>"
               "<th>last verdict</th><th>updated</th></tr></thead><tbody>")
    for t in threads:
        out.append(
            "<tr>"
            f"<td class='mono'>{esc(t.get('thread_id'))}</td>"
            f"<td>{_badge(t.get('state'))}</td>"
            f"<td class='mono'>{esc(t.get('branch'))}</td>"
            f"<td class='mono'>{esc(budget_cell(t))}</td>"
            f"<td class='mono'>{esc(short(t.get('codex_session')))}</td>"
            f"<td>{esc(t.get('last_verdict') or '-')}</td>"
            f"<td class='mono'>{esc(t.get('updated') or '-')}</td>"
            "</tr>")
    out.append("</tbody></table>")
    return out


def render_decisions(decisions):
    out = ["<h2>人类决策队列 (decisions)</h2>"]
    open_items = [d for d in decisions if d.get("status") == "open"]
    done_items = [d for d in decisions
                  if d.get("status") in ("resolved", "rejected")]
    if not open_items:
        out.append('<div class="empty">无待决策项</div>')
    else:
        out.append("<table><thead><tr>"
                   "<th>id</th><th>barrier</th><th>thread</th>"
                   "<th>summary</th><th>recommendation</th><th>evidence</th>"
                   "</tr></thead><tbody>")
        for d in open_items:
            ev = d.get("evidence")
            ev_html = f"<code>{esc(ev)}</code>" if ev else "-"
            out.append(
                "<tr>"
                f"<td class='mono'>{esc(d.get('id'))}</td>"
                f"<td>{_bar_badge(d.get('barrier'))}</td>"
                f"<td class='mono'>{esc(d.get('thread'))}</td>"
                f"<td>{esc(d.get('summary'))}</td>"
                f"<td>{esc(d.get('recommendation'))}</td>"
                f"<td class='ev'>{ev_html}</td>"
                "</tr>")
        out.append("</tbody></table>")
    # resolved / rejected history -- a decision that was made must stay visible,
    # not vanish from the board once it leaves the open queue.
    if done_items:
        out.append("<h3>已裁决 (resolved / rejected)</h3>")
        out.append("<table><thead><tr>"
                   "<th>id</th><th>barrier</th><th>thread</th>"
                   "<th>status</th><th>summary</th><th>resolution</th>"
                   "</tr></thead><tbody>")
        for d in done_items:
            out.append(
                "<tr>"
                f"<td class='mono'>{esc(d.get('id'))}</td>"
                f"<td>{_bar_badge(d.get('barrier'))}</td>"
                f"<td class='mono'>{esc(d.get('thread'))}</td>"
                f"<td>{esc(d.get('status'))}</td>"
                f"<td>{esc(d.get('summary'))}</td>"
                f"<td>{esc(d.get('resolution'))}</td>"
                "</tr>")
        out.append("</tbody></table>")
    return out


def render_backlog(backlog):
    out = ["<h2>任务补给队列 (backlog)</h2>"]
    if not backlog:
        return out + ['<div class="empty">补给队列为空</div>']
    # launchable first; rejected stay visible at the bottom (rejection memory)
    order = {"sealed-ready": 0, "scoping": 1, "candidate": 2,
             "launched": 3, "rejected": 4}
    backlog = sorted(backlog, key=lambda b: (order.get(b.get("status"), 5),
                                             str(b.get("backlog_id"))))
    out.append("<table><thead><tr>"
               "<th>id</th><th>status</th><th>summary</th>"
               "<th>dedup key</th><th>rationale / reject reason</th>"
               "<th>thread</th><th>updated</th></tr></thead><tbody>")
    for b in backlog:
        why = (b.get("reject_reason") if b.get("status") == "rejected"
               else b.get("rationale"))
        out.append(
            "<tr>"
            f"<td class='mono'>{esc(b.get('backlog_id'))}</td>"
            f"<td>{_badge(b.get('status'))}</td>"
            f"<td>{esc(b.get('summary'))}</td>"
            f"<td class='mono'>{esc(b.get('dedup_key') or '-')}</td>"
            f"<td>{esc(why or '-')}</td>"
            f"<td class='mono'>{esc(b.get('thread') or '-')}</td>"
            f"<td class='mono'>{esc(b.get('updated') or '-')}</td>"
            "</tr>")
    out.append("</tbody></table>")
    return out


def render_ticks(ticks, n):
    out = [f"<h2>最近 {n} 个 tick</h2>"]
    if not ticks:
        return out + ['<div class="empty">无 tick 历史</div>']
    for t in ticks[-n:][::-1]:
        out.append('<div class="tick">')
        out.append(f"<h3>tick {esc(t.get('tick', '?'))} · {esc(t.get('ts', '-'))}</h3>")
        summ = t.get("summary")
        if summ:
            out.append(f"<pre>{esc(str(summ).strip())}</pre>")
        op = t.get("operator") or {}
        reaped = op.get("reaped") or []
        if reaped:
            refs = ", ".join(esc(r.get("ref")) for r in reaped if isinstance(r, dict))
            out.append(f'<div class="reap">回收: {refs}</div>')
        fails = op.get("failures") or []
        if fails:
            out.append('<div class="reap fail">operator 失败: '
                       f'{esc(json.dumps(fails, ensure_ascii=False))}</div>')
        out.append("</div>")
    return out


def render_html(board, recent_ticks=5, refresh=10):
    """Build the full self-contained HTML string from the board dir."""
    threads = read_jsonl(os.path.join(board, "threads.jsonl"))
    decisions = read_jsonl(os.path.join(board, "decisions.jsonl"))
    ticks = read_jsonl(os.path.join(board, "ticks.jsonl"))
    backlog = read_jsonl(os.path.join(board, "backlog.jsonl"))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    open_count = sum(1 for d in decisions if d.get("status") == "open")
    running = sum(1 for t in threads if t.get("state") == "running")
    sealed = sum(1 for b in backlog if b.get("status") == "sealed-ready")

    refresh_tag = (f'<meta http-equiv="refresh" content="{int(refresh)}">'
                   if refresh and refresh > 0 else "")
    body = []
    body += render_threads(threads)
    body += render_decisions(decisions)
    body += render_backlog(backlog)
    body += render_ticks(ticks, recent_ticks)

    return (
        "<!doctype html><html lang='zh'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"{refresh_tag}<title>curryflows 看板</title>"
        f"<style>{CSS}</style></head><body><div class='wrap'>"
        "<h1>curryflows 综合看板</h1>"
        f"<div class='meta'>渲染于 {now} · 在跑线程 {running} · 待决策 {open_count}"
        f" · 补给 sealed-ready {sealed}</div>"
        + "".join(body) +
        "</div></body></html>\n"
    )


def main():
    ap = argparse.ArgumentParser(description="render curryflows board -> dashboard.html")
    ap.add_argument("--board", required=True,
                    help="board dir containing threads/decisions/ticks .jsonl")
    ap.add_argument("--out", default=None,
                    help="output path (default: <board>/dashboard.html)")
    ap.add_argument("--recent-ticks", type=int, default=5,
                    help="how many recent ticks to include (default 5)")
    ap.add_argument("--refresh", type=int, default=10,
                    help="browser auto-refresh seconds (0 = off; default 10)")
    args = ap.parse_args()

    if not os.path.isdir(args.board):
        sys.stderr.write(f"error: board dir not found: {args.board}\n")
        return 64
    out_path = args.out or os.path.join(args.board, "dashboard.html")
    with open(out_path, "w") as f:
        f.write(render_html(args.board, args.recent_ticks, args.refresh))
    sys.stderr.write(f"rendered: {out_path}\n")
    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
