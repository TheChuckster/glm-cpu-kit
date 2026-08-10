#!/usr/bin/env python3
"""Verbatim logging proxy for OpenAI-compatible traffic.

Sits between a client (opencode) and an upstream (litellm), forwards requests
byte-for-byte, and writes every request/response pair to disk. Built to capture
the exact request that triggers a rare model failure, so it can be replayed
deterministically instead of guessed at.

    ./capture-proxy.py                       # :4100 -> 127.0.0.1:4000
    CAPTURE_DIR=/tmp/cap ./capture-proxy.py  # choose output dir

Point a client at it by changing the provider baseURL - NOT by setting
OPENCODE_BASE_URL, which the launchers use only for their preflight curl and
never forward to opencode (whose endpoint comes from opencode.json):

    cp -r ~/.glm-opencode-config /tmp/cap-config
    python3 - <<'PY'
    import json
    p = "/tmp/cap-config/opencode/opencode.json"
    d = json.load(open(p))
    d["provider"]["local"]["options"]["baseURL"] = "http://127.0.0.1:4100/v1"
    json.dump(d, open(p, "w"), indent=1)
    PY
    GLM_OPENCODE_XDG=/tmp/cap-config ./ds4-opencode.sh run "..."

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


def _highest_existing():
    """Continue numbering past whatever is already in CAPTURE_DIR.

    The counter used to start at 1 every run, so restarting the proxy against the
    same directory overwrote earlier captures in place while leaving later ones
    untouched - and replay-sequence.py, which addresses captures purely by index,
    would then splice two unrelated sessions into one "ordered" sequence.
    """
    n = 0
    for f in os.listdir(CAPDIR):
        if f.endswith("-request.json") or f.endswith("-response.txt"):
            try:
                n = max(n, int(f.split("-")[0]))
            except ValueError:
                pass
    return n


_n = [_highest_existing()]


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
            up = urllib.request.urlopen(req, timeout=21600)  # match litellm's
        except urllib.error.HTTPError as e:
            up = e
        except Exception as e:
            # Frame the body and close: with protocol_version HTTP/1.1 and no
            # Content-Length the connection stays open and the client blocks on a
            # body it can never know has ended, so a stopped upstream presented as
            # a permanently hung agent instead of an instant error.
            msg = f"capture-proxy: upstream error: {e}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(msg)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(msg)
            self.close_connection = True
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
        err = None
        try:
            while True:
                # read1, not read: http.client's chunked read(amt) blocks until it
                # has exactly amt bytes, accumulating several SSE frames before
                # releasing any. That batched a stream this tool exists to relay
                # faithfully - measured as nothing for 4.8 s then the whole body
                # at once, against 12 evenly spaced events direct.
                chunk = up.read1(65536)
                if not chunk:
                    break
                out.write(chunk)
                out.flush()
                self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception as e:
            # Do NOT swallow this. A capture that ends early is indistinguishable
            # from a complete one to replay-sequence.py, and the old bare
            # `except: pass` also skipped the terminating chunk, so the client
            # hung while the log reported success.
            err = e
            self.close_connection = True
        finally:
            out.close()
            if err is not None:
                with open(f"{CAPDIR}/{idx:04d}-TRUNCATED.txt", "w") as f:
                    f.write(f"{type(err).__name__}: {err}\n")
                print(f"[{time.strftime('%H:%M:%S')}] {idx:04d} {method} {self.path} "
                      f"req={len(body)}B  *** TRUNCATED: {type(err).__name__} ***",
                      flush=True)
            else:
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
