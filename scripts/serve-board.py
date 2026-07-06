#!/usr/bin/env python3
"""curryflows: serve the consolidated board as a live HTML dashboard.

Started alongside the coordinator (`start` op). It re-renders the board from the
jsonl files on EVERY request, so the browser always shows current state; the page
also carries a meta-refresh so an open tab updates on its own. The coordinator
still writes board/*.jsonl each tick -- this just presents them live.

Bind defaults to 127.0.0.1 (localhost only); on an SSH host, port-forward to view.
Reuses render_html() from render-board.py. READ-ONLY w.r.t. the board.

Usage:
  serve-board.py --board <dir> [--port 8787] [--host 127.0.0.1] \
                 [--recent-ticks 5] [--refresh 10]

Exit codes: 0 normal stop; 64 usage; 70 port bind failed.
"""
import argparse
import importlib.util
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _load_renderer():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "render-board.py")
    spec = importlib.util.spec_from_file_location("cfx_render_board", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RB = _load_renderer()


def make_handler(board, recent_ticks, refresh):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8", errors="replace")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path not in ("/", "/index.html", "/dashboard.html"):
                self._send(404, "not found", "text/plain; charset=utf-8")
                return
            try:
                html = RB.render_html(board, recent_ticks, refresh)
                self._send(200, html)
            except Exception as e:  # never let a bad board kill the server
                self._send(500, f"<pre>render error: {e}</pre>")

        def log_message(self, *a):  # quiet
            return

    return Handler


def main():
    ap = argparse.ArgumentParser(description="serve curryflows board live")
    ap.add_argument("--board", required=True)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787, help="0 = auto-pick")
    ap.add_argument("--recent-ticks", type=int, default=5)
    ap.add_argument("--refresh", type=int, default=10)
    args = ap.parse_args()

    if not os.path.isdir(args.board):
        sys.stderr.write(f"error: board dir not found: {args.board}\n")
        return 64

    try:
        httpd = ThreadingHTTPServer(
            (args.host, args.port),
            make_handler(os.path.abspath(args.board), args.recent_ticks, args.refresh),
        )
    except OSError as e:
        # Idempotent start: if the port is already held by a live board server,
        # that IS the desired end state -- print its URL and exit 0 instead of
        # failing. (Coordinator ops retry `serve-board.py` each tick; exit 70
        # here used to read as "dashboard won't start" when it was just already
        # running.) Anything else on the port stays a hard error.
        probe = f"http://{args.host or '127.0.0.1'}:{args.port}/"
        try:
            import urllib.request
            # empty ProxyHandler: env proxies (http_proxy=...) would otherwise
            # route a 127.0.0.1 probe through the proxy and always fail
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(probe, timeout=3) as resp:
                body_head = resp.read(4096).decode("utf-8", errors="replace")
                if resp.status == 200 and "curryflows" in body_head:
                    sys.stderr.write(f"already serving at {probe} (reusing)\n")
                    print(probe)
                    return 0
        except Exception:
            pass
        sys.stderr.write(f"error: cannot bind {args.host}:{args.port}: {e}\n")
        return 70

    host, port = httpd.server_address
    url = f"http://{args.host}:{port}/"
    sys.stderr.write(f"curryflows board serving at {url} (board={args.board})\n")
    print(url)
    sys.stderr.flush()
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
