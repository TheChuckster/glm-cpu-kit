#!/usr/bin/env python3
"""Replay a captured REQUEST SEQUENCE in order, reproducing KV-cache evolution.

Usage: replay_seq.py <first> <last> [repeats]

The hypothesis this tests: the degeneration is not a property of any single
request but of the server's cache state after a specific run of prefix-sharing
requests. Replaying one request in isolation cannot reproduce that.
"""
import json, sys, glob, os, urllib.request
from collections import Counter

FIRST = int(sys.argv[1]); LAST = int(sys.argv[2])
REPEATS = int(sys.argv[3]) if len(sys.argv) > 3 else 1
URL = "http://127.0.0.1:4000/v1/chat/completions"
CAP = "/tmp/capture"


def degen(t):
    w = t.split()
    if len(w) < 40:
        return False, 0
    g = Counter(tuple(w[i:i+3]) for i in range(len(w)-2))
    top = g.most_common(1)[0]
    return top[1] > 12, top[1]


PATCH = json.loads(os.environ.get("PATCH", "{}"))


def send(body):
    body = dict(body)
    body.pop("stream", None)
    body.update(PATCH)
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer sk-litellm-not-needed",
                 "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(req, timeout=3600))
    m = d["choices"][0]["message"]
    return (m.get("reasoning_content") or ""), (m.get("content") or "")


total_bad = 0
for rep in range(1, REPEATS+1):
    print(f"=== sequence pass {rep} ({FIRST}..{LAST}) ===", flush=True)
    for i in range(FIRST, LAST+1):
        p = f"{CAP}/{i:04d}-request.json"
        if not os.path.exists(p):
            continue
        try:
            body = json.load(open(p))
        except Exception:
            continue
        try:
            R, C = send(body)
        except Exception as e:
            print(f"  {i:04d}: FAILED {type(e).__name__} {e}", flush=True)
            continue
        dr, nr = degen(R); dc, nc = degen(C)
        bad = dr or dc
        total_bad += bad
        print(f"  {i:04d}: reasoning={len(R):6d}(x{nr}) content={len(C):6d}(x{nc})"
              f"{'   <== DEGENERATE' if bad else ''}", flush=True)
print(f"\ntotal degenerate responses: {total_bad}", flush=True)
