#!/usr/bin/env python3
"""Verbatim logging proxy for OpenAI-compatible traffic.

Sits between a client (opencode) and an upstream (litellm), forwards requests
byte-for-byte, and writes every request/response pair to disk. Built to capture
the exact request that triggers a rare model failure, so it can be replayed
deterministically instead of guessed at.

    ./capture-proxy.py                       # :4100 -> 127.0.0.1:4000
    CAPTURE_DIR=/tmp/cap ./capture-proxy.py  # choose output dir

Point a client at it:

    OPENCODE_BASE_URL=http://127.0.0.1:4100/v1 ./ds4-opencode.sh run "..."

Each exchange writes <dir>/<n>-request.json and <n>-response.txt. Streaming
responses are relayed chunk-by-chunk as they arrive (so the client still streams)
while being teed to the log.
"""
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.request

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:4000")
PORT = int(os.environ.get("PORT", "4100"))
CAPDIR = os.environ.get("CAPTURE_DIR", "/tmp/capture")
os.makedirs(CAPDIR, exist_ok=True)
_lock = threading.Lock()
_n = [0]


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _proxy(self, method):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        with _lock:
            _n[0] += 1
            idx = _n[0]

        if body:
            with open(f"{CAPDIR}/{idx:04d}-request.json", "wb") as f:
                f.write(body)

        hdrs = {k: v for k, v in self.headers.items()
                if k.lower() not in ("host", "content-length", "accept-encoding")}
        req = urllib.request.Request(UPSTREAM + self.path, data=body or None,
                                     headers=hdrs, method=method)
        try:
            up = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            up = e
        except Exception as e:
            self.send_response(502)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return

        self.send_response(up.status)
        for k, v in up.headers.items():
            if k.lower() in ("transfer-encoding", "content-length", "connection"):
                continue
            self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        # Relay as it arrives so the client keeps streaming, tee to disk.
        out = open(f"{CAPDIR}/{idx:04d}-response.txt", "wb")
        try:
            while True:
                chunk = up.read(1024)
                if not chunk:
                    break
                out.write(chunk)
                out.flush()
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass
        finally:
            out.close()
            print(f"[{time.strftime('%H:%M:%S')}] {idx:04d} {method} {self.path} "
                  f"req={len(body)}B", flush=True)

    def do_POST(self):
        self._proxy("POST")

    def do_GET(self):
        self._proxy("GET")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


if __name__ == "__main__":
    print(f"capture proxy :{PORT} -> {UPSTREAM}, writing to {CAPDIR}", flush=True)
    Server(("127.0.0.1", PORT), Handler).serve_forever()
