#!/usr/bin/env python3
# Minimal log->Loki shipper. Tails journald units and/or files and pushes lines
# to Loki's HTTP push API. Usage:
#   loki-pusher.py <loki_push_url> journal=glm-server file=/tmp/litellm.log ...
import subprocess, time, json, urllib.request, threading, sys, os

LOKI = sys.argv[1]
buffers = {}
lock = threading.Lock()

def add(labels, line):
    if not line:
        return
    key = json.dumps(labels, sort_keys=True)
    ts = str(time.time_ns())
    with lock:
        buffers.setdefault(key, (labels, []))[1].append([ts, line])

def flusher():
    while True:
        time.sleep(1)
        streams = []
        with lock:
            for key in list(buffers):
                labels, vals = buffers[key]
                if vals:
                    streams.append({"stream": labels, "values": vals[:800]})
                    buffers[key] = (labels, vals[800:])
        if streams:
            try:
                req = urllib.request.Request(LOKI,
                    data=json.dumps({"streams": streams}).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=8)
            except Exception as e:
                print("loki push err:", e, file=sys.stderr)

def tail_journal(unit):
    labels = {"job": unit, "host": os.uname().nodename}
    p = subprocess.Popen(["journalctl", "-fu", unit, "-o", "cat", "-n", "0"],
                         stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in p.stdout:
        add(labels, line.rstrip("\n"))

def tail_file(path):
    labels = {"job": os.path.basename(path).split(".")[0], "host": os.uname().nodename}
    p = subprocess.Popen(["tail", "-Fn", "0", path], stdout=subprocess.PIPE, text=True, bufsize=1)
    for line in p.stdout:
        add(labels, line.rstrip("\n"))

threading.Thread(target=flusher, daemon=True).start()
for src in sys.argv[2:]:
    kind, _, val = src.partition("=")
    fn = tail_journal if kind == "journal" else tail_file
    threading.Thread(target=fn, args=(val,), daemon=True).start()
while True:
    time.sleep(3600)
