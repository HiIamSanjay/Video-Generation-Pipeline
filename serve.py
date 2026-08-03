"""Phase 5 -- serve finished ads over HTTP for the in-store display.

    .\\py serve.py                       # 127.0.0.1:8787, nothing exposed
    .\\py serve.py --port 9000

Routes
    /                    tiny status page
    /manifest.json       current ad metadata (no-store)
    /latest.mp4          current ad, stable filename (no-store)
    /ads/<name>.mp4      dated file, immutable, long cache

Deliberately narrow: the document root is out/ and only .mp4 plus
manifest.json are served, because this is what a Cloudflare tunnel will point
at. ComfyUI itself stays on 127.0.0.1 and is never exposed -- it has no
authentication, so publishing it would hand over job submission and file
access to anyone with the URL.

Implements HTTP Range (206) because SimpleHTTPRequestHandler does not, and
video players issue range requests to seek and to start playback early.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import sys
from datetime import datetime, timezone
from email.utils import formatdate
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = (HERE / "out").resolve()

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
CHUNK = 1 << 16


class AdHandler(BaseHTTPRequestHandler):
    server_version = "video-gen/1.0"
    protocol_version = "HTTP/1.1"          # required for keep-alive and ranges

    # ---------- helpers ----------

    def _resolve(self, name: str) -> Path | None:
        """Map a bare filename inside out/ to a real path, or None."""
        if not SAFE_NAME.match(name):
            return None                     # blocks .. and separators outright
        p = (OUT / name).resolve()
        try:
            p.relative_to(OUT)              # defence in depth against traversal
        except ValueError:
            return None
        if not p.is_file():
            return None
        if p.suffix.lower() != ".mp4" and p.name != "manifest.json":
            return None
        return p

    def _common(self, ctype: str, cache: str) -> None:
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _fail(self, code: HTTPStatus, msg: str = "") -> None:
        body = json.dumps({"error": msg or code.phrase}).encode()
        self.send_response(code)
        self._common("application/json", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_file(self, path: Path, cache: str, head: bool) -> None:
        size = path.stat().st_size
        ctype = "video/mp4" if path.suffix.lower() == ".mp4" else "application/json"
        rng = self.headers.get("Range")

        start, end = 0, size - 1
        partial = False
        if rng:
            m = RANGE_RE.match(rng.strip())
            if not m:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            s, e = m.group(1), m.group(2)
            if s == "":                       # suffix range: last N bytes
                length = int(e or 0)
                if length == 0:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = max(0, size - length)
            else:
                start = int(s)
                if e != "":
                    end = min(int(e), size - 1)
            if start > end or start >= size:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            partial = True

        length = end - start + 1
        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self._common(ctype, cache)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Last-Modified", formatdate(path.stat().st_mtime, usegmt=True))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head:
            return
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                buf = f.read(min(CHUNK, remaining))
                if not buf:
                    break
                try:
                    self.wfile.write(buf)
                except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                    return                  # player closed the connection, normal
                remaining -= len(buf)

    def _status_page(self, head: bool) -> None:
        mf = OUT / "manifest.json"
        cur = {}
        if mf.exists():
            try:
                cur = json.loads(mf.read_text(encoding="utf-8")).get("current", {})
            except json.JSONDecodeError:
                pass
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if cur:
            rows = "".join(
                f"<tr><th>{k}</th><td>{cur.get(k, '')}</td></tr>"
                for k in ("item", "department", "percent", "line2", "duration",
                          "width", "height", "generated_at")
            )
            # #t=2 seeks the poster frame past the opening push-in, which is the
            # least representative moment of the clip. Needs Range support (we
            # have it) or the browser cannot seek before playing.
            body = (f"<h1>Current ad</h1><table>{rows}</table>"
                    f'<p><video src="/latest.mp4#t=2" controls preload="metadata" '
                    f'width="640"></video></p>')
        else:
            body = "<h1>No ad published yet</h1><p>Run make_ad.py first.</p>"
        html = (
            "<!doctype html><meta charset=utf-8><title>video_gen</title>"
            "<style>body{font:15px system-ui;margin:2rem;max-width:56rem}"
            "table{border-collapse:collapse}th,td{padding:.3rem .8rem;"
            "border-bottom:1px solid #ddd;text-align:left}"
            "th{color:#666;font-weight:500}video{max-width:100%}</style>"
            f"{body}<hr><p><a href=/manifest.json>manifest.json</a> &middot; "
            f"<a href=/latest.mp4>latest.mp4</a> &middot; {now}</p>"
        ).encode()
        self.send_response(HTTPStatus.OK)
        self._common("text/html; charset=utf-8", "no-store")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        if not head:
            self.wfile.write(html)

    # ---------- routing ----------

    def _route(self, head: bool = False) -> None:
        path = self.path.split("?", 1)[0].split("#", 1)[0]

        if path in ("/", "/index.html"):
            return self._status_page(head)
        if path == "/healthz":
            return self._fail(HTTPStatus.OK, "ok")

        if path == "/manifest.json":
            p = self._resolve("manifest.json")
            return self._send_file(p, "no-store", head) if p else \
                self._fail(HTTPStatus.NOT_FOUND, "no manifest yet")

        if path == "/latest.mp4":
            p = self._resolve("latest.mp4")
            # no-store: the filename is stable, so a cached copy would pin the
            # display to whichever ad it first saw.
            return self._send_file(p, "no-store", head) if p else \
                self._fail(HTTPStatus.NOT_FOUND, "no ad published yet")

        if path.startswith("/ads/"):
            p = self._resolve(path[len("/ads/"):])
            # Dated filenames never change, so they can cache hard.
            return self._send_file(p, "public, max-age=31536000, immutable", head) if p \
                else self._fail(HTTPStatus.NOT_FOUND)

        self._fail(HTTPStatus.NOT_FOUND)

    def do_GET(self) -> None:
        self._route(head=False)

    def do_HEAD(self) -> None:
        self._route(head=True)

    def log_message(self, fmt: str, *a) -> None:
        sys.stderr.write("  %s %s\n" % (self.address_string(), fmt % a))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1",
                    help="0.0.0.0 exposes to your LAN; the tunnel does not need it")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()

    if not OUT.exists():
        OUT.mkdir(parents=True)

    srv = ThreadingHTTPServer((args.host, args.port), AdHandler)
    srv.daemon_threads = True
    host = args.host if args.host != "0.0.0.0" else socket.gethostname()
    print(f"serving {OUT}")
    print(f"  http://{host}:{args.port}/")
    print(f"  http://{host}:{args.port}/manifest.json")
    print(f"  http://{host}:{args.port}/latest.mp4")
    print("ctrl-c to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
