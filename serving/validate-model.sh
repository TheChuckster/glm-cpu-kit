#!/bin/bash
# Does a newly downloaded quant actually work? Answers that on a spare port, so
# the live server on :8080 is never disturbed.
#
#   ./validate-model.sh <label> <first-shard-or-single.gguf> [extra flags...]
#
# Checks, ordered by what would make the model useless, each earned from a real
# failure during the DeepSeek-V4 bring-up (runbook §6b):
#
#   1. does it load           - the antirez DS4 quant segfaults during warmup,
#                               reproducibly, and a quant that loads in the
#                               publisher's own engine may not load in ik
#   2. is output coherent     - ik #2214/#2218: DS4 loaded fine and emitted
#                               "dekametersapl dekametersapl". Perplexity looked
#                               normal; it was a broken quantisation kernel
#   3. does reasoning stay    - --reasoning-budget 0 does NOT suppress thinking,
#      out of `content`         it dumps the raw chain-of-thought into the
#                               user-visible answer. Checked on the STREAMING
#                               path, because that is what harnesses use and ik's
#                               own help text claims it degrades there
#   4. do tool calls work     - ik #2242: without it ik falls back to the
#                               autoparser, which forces string="true" on every
#                               argument, breaking prompt caching
#   5. how fast is it
#
# Runs as your normal user. It deliberately does NOT pass --mlock: the memlock
# ulimit is often below the model size, and mmap plus a large page cache is fine
# for a throwaway validation run.
set -uo pipefail
LABEL="${1:?usage: validate-model.sh <label> <model.gguf> [flags...]}"
MODEL="${2:?}"
shift 2

IK="${IK_LLAMA:-$HOME/ik_llama.cpp/build/bin/llama-server}"
PORT="${VALIDATE_PORT:-8081}"
OUT="${VALIDATE_OUT:-/models/.validate}/$LABEL"
KEY=$(cat "$HOME/.glm-api-key" 2>/dev/null)
mkdir -p "$OUT"

say() { echo "$(date +%H:%M:%S) [$LABEL] $*" | tee -a "$OUT/report.txt"; }
[ -f "$MODEL" ] || { say "FATAL model missing: $MODEL"; exit 1; }
[ -x "$IK" ]    || { say "FATAL engine missing: $IK (set IK_LLAMA)"; exit 1; }

say "=== $(basename "$MODEL") $* ==="
"$IK" --model "$MODEL" --alias "$LABEL" --host 127.0.0.1 --port "$PORT" \
    --ctx-size "${VALIDATE_CTX:-65536}" --parallel 1 \
    --threads "${THREADS:-$(nproc)}" --threads-batch "${THREADS:-$(nproc)}" \
    --batch-size 2048 --ubatch-size 2048 -fa on \
    --cache-type-k q8_0 --cache-type-v q8_0 --jinja \
    --repeat-penalty 1.1 --repeat-last-n 256 --metrics \
    --api-key-file "$HOME/.glm-api-key" "$@" \
    > "$OUT/server.log" 2>&1 &
SRV=$!
# Kill by PID, not by pattern: a pkill -f on the port or the binary path also
# matches other validation runs and, worse, anything else holding that string.
trap 'kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null' EXIT

say "waiting for health"
ready=0
for _ in $(seq 1 180); do
    curl -sf --max-time 5 "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && { ready=1; break; }
    if ! kill -0 "$SRV" 2>/dev/null; then
        wait "$SRV"; rc=$?
        say "FATAL server died during load (exit $rc; 139=SIGSEGV, 134=SIGABRT)"
        tail -20 "$OUT/server.log" | tee -a "$OUT/report.txt"
        exit 1
    fi
    sleep 10
done
[ "$ready" = 1 ] || { say "FATAL not ready after 30 min"; exit 1; }
say "loaded OK"

# --- coherence + reasoning separation, on the STREAMING path -----------------
curl -sN --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$LABEL"'","stream":true,"max_tokens":300,"messages":[{"role":"user","content":"Say hello and name the capital of France in one short sentence."}]}' \
  > "$OUT/stream.sse"
python3 - "$OUT/stream.sse" <<'PY' | tee -a "$OUT/report.txt"
import json, sys
content, reasoning = [], []
for ln in open(sys.argv[1]):
    ln = ln.strip()
    if not ln.startswith("data: "): continue
    p = ln[6:]
    if p == "[DONE]": break
    try: d = json.loads(p)
    except Exception: continue
    for ch in d.get("choices", []):
        delta = ch.get("delta", {}) or {}
        if delta.get("content"):           content.append(delta["content"])
        if delta.get("reasoning_content"): reasoning.append(delta["reasoning_content"])
c, r = "".join(content), "".join(reasoning)
print("  content  :", repr(c[:250]))
print("  reasoning:", repr(r[:150]) if r else "(none)")
print("  COHERENCE:", "PASS" if "paris" in c.lower() else "FAIL (garbled quant? ik #2214)")
# A leaked trace is recognisable: the model narrates a numbered plan first.
leak = c.lstrip().startswith("1.") or "The user asks" in c[:200]
print("  REASONING:", "LEAK into content" if leak else "PASS (separated)")
PY

# --- tool call ---------------------------------------------------------------
curl -sf --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$LABEL"'","max_tokens":400,"stream":false,"tool_choice":"auto","messages":[{"role":"user","content":"What files are in /tmp? Use the list_files tool."}],"tools":[{"type":"function","function":{"name":"list_files","description":"List files in a directory","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
  > "$OUT/toolcall.json"
python3 - "$OUT/toolcall.json" <<'PY' | tee -a "$OUT/report.txt"
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception as e:
    print("  TOOLCALL: FAIL (no/invalid response:", e, ")"); raise SystemExit
m = d.get("choices", [{}])[0].get("message", {})
tc = m.get("tool_calls") or []
if not tc:
    print("  TOOLCALL: FAIL (no tool_calls);", str(m.get("content"))[:150]); raise SystemExit
f = tc[0].get("function", {})
args = str(f.get("arguments", ""))
print("  tool     :", f.get("name"), args[:120])
print("  TOOLCALL:", "FAIL (autoparser fallback - ik #2242)" if 'string="true"' in args
      else ("PASS" if f.get("name") == "list_files" else "FAIL (wrong tool)"))
PY

say "=== done: $OUT/report.txt ==="
