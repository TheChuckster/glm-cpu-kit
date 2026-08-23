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
#   3. does it TERMINATE      - K3 once answered correctly, then repeated its
#                               message-closing tag until max_tokens. The strict
#                               parser rejected that tail and OpenCode appeared
#                               to spin forever. Reject leaked structural tags
#                               and a trivial reply consuming the full budget.
#   4. does reasoning stay    - --reasoning-budget 0 does NOT suppress thinking,
#      out of `content`         it dumps the raw chain-of-thought into the
#                               user-visible answer. Checked on the STREAMING
#                               path, because that is what harnesses use and ik's
#                               own help text claims it degrades there
#   5. do tool calls work     - ik #2242: without it ik falls back to the
#                               autoparser, which forces string="true" on every
#                               argument, breaking prompt caching
#   6. are they RELIABLE      - one passing call proves nothing. Kimi K3 passed
#                               check 5 and then emitted a call 3 times in 5,
#                               where DeepSeek-V4 and GLM were 5/5. Runs it
#                               VALIDATE_TOOL_REPEATS times (default 5).
#   7. do they STREAM         - harnesses stream. A model can return tool_calls
#                               non-streaming and emit zero deltas when streamed,
#                               which shows up as the harness printing nothing.
#   8. does the REPLAY work   - the turn after a tool call sends the assistant
#                               message back complete (tool_calls AND
#                               reasoning_content) plus the tool result. That
#                               shape is unavoidable for a reasoning model.
#   9. does it DEGENERATE     - long prompt WITH tool declarations, because that
#                               is the shape that triggered it: K3 answered chat
#                               correctly and produced 8000 tokens of one
#                               paragraph on an agent prompt. An earlier version
#                               of this check used plain filler, passed, and
#                               missed it.
#  10. how fast is it
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

# Refuse to start on an occupied port rather than silently sharing it. See the
# trap below for why a stale listener is not merely cosmetic.
if ss -ltn "sport = :${VALIDATE_PORT:-8081}" 2>/dev/null | grep -q LISTEN; then
    say "FATAL something is already listening on port ${VALIDATE_PORT:-8081}"
    say "      (a previous run's server, most likely - kill it, or set VALIDATE_PORT)"
    ss -ltnp "sport = :${VALIDATE_PORT:-8081}" 2>/dev/null | tail -n +2
    exit 1
fi
[ -x "$IK" ]    || { say "FATAL engine missing: $IK (set IK_LLAMA)"; exit 1; }

# nproc counts SMT siblings, and using it here is not merely suboptimal. On a
# 64-core part it asks for 128 threads, where DeepSeek-V4 measures 0.43 tok/s
# against 31.5 at 64 - so a 300-token check takes twelve minutes instead of ten
# seconds, and validation looks hung rather than slow. Decode saturates around
# 32 threads on every model here anyway; prefill wants physical cores and no
# more.
VCORES=$(lscpu -p=Core,Socket 2>/dev/null | grep -v '^#' | sort -u | wc -l)
[ "${VCORES:-0}" -gt 0 ] 2>/dev/null || VCORES=$(nproc)

say "=== $(basename "$MODEL") $* ==="
say "threads: $VCORES physical cores (nproc reports $(nproc))"
"$IK" --model "$MODEL" --alias "$LABEL" --host 127.0.0.1 --port "$PORT" \
    --ctx-size "${VALIDATE_CTX:-65536}" --parallel 1 \
    --threads "${THREADS:-$VCORES}" --threads-batch "${THREADS:-$VCORES}" \
    --batch-size 2048 --ubatch-size 2048 -fa on \
    --cache-type-k q8_0 --cache-type-v q8_0 --jinja \
    --repeat-penalty 1.1 --repeat-last-n 256 --metrics \
    --api-key-file "$HOME/.glm-api-key" "$@" \
    > "$OUT/server.log" 2>&1 &
SRV=$!
# Kill by PID, not by pattern: a pkill -f on the port or the binary path also
# matches other validation runs and, worse, anything else holding that string.
# EXIT alone does NOT fire on SIGTERM/SIGINT, so an interrupted run used to
# leave its llama-server bound to $PORT forever. That is worse than untidy:
# llama-server binds with SO_REUSEPORT, so the next run's server shares the port
# rather than failing, and requests are split between the live server and a
# zombie that never answers - which presents as validation hanging with no
# request ever reaching the server you just started.
trap 'kill "$SRV" 2>/dev/null; wait "$SRV" 2>/dev/null' EXIT INT TERM HUP

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
predicted = None
for ln in open(sys.argv[1]):
    ln = ln.strip()
    if not ln.startswith("data: "): continue
    p = ln[6:]
    if p == "[DONE]": break
    try: d = json.loads(p)
    except Exception: continue
    timings = d.get("timings") or {}
    if isinstance(timings.get("predicted_n"), int):
        predicted = timings["predicted_n"]
    for ch in d.get("choices", []):
        delta = ch.get("delta", {}) or {}
        if delta.get("content"):           content.append(delta["content"])
        if delta.get("reasoning_content"): reasoning.append(delta["reasoning_content"])
c, r = "".join(content), "".join(reasoning)
print("  content  :", repr(c[:250]))
print("  reasoning:", repr(r[:150]) if r else "(none)")
print("  COHERENCE:", "PASS" if "paris" in c.lower() else "FAIL (garbled quant? ik #2214)")
termination_fail = "<|" in c or (predicted is not None and predicted >= 300)
detail = "structural tag leaked" if "<|" in c else "used all 300 tokens"
print("  TERMINATION:", "FAIL (" + detail + ")" if termination_fail else
      "PASS (%s generated tokens)" % (predicted if predicted is not None else "unknown"))
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

# --- tool call RELIABILITY, not existence -----------------------------------
# One passing tool call proves nothing. Kimi K3 passed the single check above
# and then emitted a call only 3 times in 5, while DeepSeek-V4 and GLM were 5/5
# - which is the difference between a usable agent and one that stalls silently.
REPEATS="${VALIDATE_TOOL_REPEATS:-5}"
say "--- tool call reliability ($REPEATS runs) ---"
PASSES=0
for i in $(seq 1 "$REPEATS"); do
  curl -sf --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -d '{"model":"'"$LABEL"'","max_tokens":1200,"stream":false,"tool_choice":"auto","messages":[{"role":"user","content":"What files are in /tmp? Use the list_files tool."}],"tools":[{"type":"function","function":{"name":"list_files","description":"List files in a directory","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
    > "$OUT/tool_$i.json" 2>/dev/null || true
  if python3 -c "
import json,sys
try: d=json.load(open('$OUT/tool_$i.json'))
except Exception: sys.exit(1)
sys.exit(0 if (d.get('choices',[{}])[0].get('message',{}).get('tool_calls')) else 1)
" 2>/dev/null; then PASSES=$((PASSES+1)); fi
done
printf "  TOOL RELIABILITY: %d/%d %s\n" "$PASSES" "$REPEATS" \
  "$([ "$PASSES" = "$REPEATS" ] && echo PASS || echo 'FAIL - unreliable for agent use')" \
  | tee -a "$OUT/report.txt"

# --- streaming tool calls ----------------------------------------------------
# Harnesses stream. A model can return tool_calls non-streaming and emit zero
# tool-call deltas when streamed, which presents as the harness printing nothing.
say "--- streaming tool call ---"
curl -sN --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$LABEL"'","max_tokens":1200,"stream":true,"tool_choice":"auto","messages":[{"role":"user","content":"What files are in /tmp? Use the list_files tool."}],"tools":[{"type":"function","function":{"name":"list_files","description":"List files in a directory","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}}]}' \
  > "$OUT/toolstream.txt" 2>/dev/null || true
python3 - "$OUT/toolstream.txt" <<'PY' | tee -a "$OUT/report.txt"
import json, sys
n = 0; finish = None
for line in open(sys.argv[1], errors="replace"):
    line = line.strip()
    if not line.startswith("data: "): continue
    payload = line[6:]
    if payload == "[DONE]": break
    try: d = json.loads(payload)
    except Exception: continue
    ch = d.get("choices", [{}])[0]
    if (ch.get("delta") or {}).get("tool_calls"): n += 1
    if ch.get("finish_reason"): finish = ch["finish_reason"]
print("  STREAM TOOLCALL:", "PASS (%d deltas)" % n if n else
      "FAIL (0 deltas, finish=%s) - harness will show nothing" % finish)
PY

# --- multi-turn replay -------------------------------------------------------
# The turn AFTER a tool call is what breaks agents: the assistant message has to
# go back complete, tool_calls and reasoning_content included, followed by the
# tool result. Reasoning models cannot avoid that shape.
say "--- multi-turn tool replay ---"
curl -sf --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$LABEL"'","max_tokens":600,"messages":[{"role":"user","content":"What is the weather in Oslo? Use the tool."},{"role":"assistant","content":"","reasoning_content":"Need to call get_weather for Oslo.","tool_calls":[{"id":"call_1","type":"function","function":{"name":"get_weather","arguments":"{\"city\":\"Oslo\"}"}}]},{"role":"tool","tool_call_id":"call_1","name":"get_weather","content":"{\"temp_c\": 3, \"conditions\": \"light rain\"}"}],"tools":[{"type":"function","function":{"name":"get_weather","description":"Get current weather","parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}}]}' \
  > "$OUT/multiturn.json" 2>/dev/null || true
python3 - "$OUT/multiturn.json" <<'PY' | tee -a "$OUT/report.txt"
import json, sys
try: d = json.load(open(sys.argv[1]))
except Exception as e:
    print("  MULTITURN: FAIL (no/invalid response - replay rejected?)"); raise SystemExit
c = (d.get("choices", [{}])[0].get("message", {}).get("content") or "")
ok = "3" in c and ("rain" in c.lower())
print("  MULTITURN:", "PASS" if ok else "FAIL (did not use the tool result)", repr(c[:100]))
PY

# --- degeneration on a long prompt -------------------------------------------
# A low-bit quant can be fine on short prompts and collapse into repetition on a
# long one - which is what an agent sends. Kimi K3 answered chat correctly and
# produced 8000 tokens of the same paragraph on a ~7000-token agent prompt.
say "--- long-prompt degeneration ---"
python3 - "$OUT/longprompt.json" "$LABEL" <<'PY'
import json, sys
# Tools are part of the prompt on purpose. Kimi K3 degenerated only on
# agent-shaped input - long AND carrying tool declarations - and an earlier
# version of this check used plain filler, passed, and missed it entirely while
# the tool checks below caught it.
filler = ("The following is reference material the assistant may consult. " * 60 + "\n") * 12
msg = filler + "\nIgnoring the material above, answer in one short sentence: what is 2+2?"
tools = [{"type": "function", "function": {
    "name": "list_files", "description": "List files in a directory",
    "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                   "required": ["path"]}}}]
json.dump({"model": sys.argv[2], "max_tokens": 1200, "stream": False,
           "tools": tools, "tool_choice": "auto",
           "messages": [{"role": "user", "content": msg}]}, open(sys.argv[1], "w"))
PY
curl -sf --max-time 1800 "http://127.0.0.1:$PORT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d @"$OUT/longprompt.json" > "$OUT/longout.json" 2>/dev/null || true
python3 - "$OUT/longout.json" <<'PY' | tee -a "$OUT/report.txt"
import json, sys, collections
try: d = json.load(open(sys.argv[1]))
except Exception:
    print("  DEGENERATION: FAIL (no response)"); raise SystemExit
m = d.get("choices", [{}])[0].get("message", {})
content = m.get("content") or ""
text = content + (m.get("reasoning_content") or "")

# Two independent detectors, because degeneration does not have one shape.
#
# The repetition one alone is not enough and this was learned the hard way: on a
# configuration known to be broken it passed twice, because that model's failure
# was a counting sequence and unrelated prose rather than a verbatim loop.
#
# The decisive question is simply whether the model ANSWERED. The prompt asks
# what 2+2 is after a wall of filler; a model holding the thread says 4 in a
# sentence. Thousands of characters that never say 4 is a failure whatever its
# shape.
wins = collections.Counter(text[i:i+60] for i in range(0, max(0, len(text) - 60), 20))
worst = wins.most_common(1)[0][1] if wins else 0
answered = "4" in content
verdict = []
if worst >= 5:      verdict.append("a 60-char window repeats %dx" % worst)
if not answered:    verdict.append("never answered (no '4' in content)")
if len(text) > 4000 and not answered:
                    verdict.append("%d chars for a one-sentence question" % len(text))
print("  DEGENERATION:", "FAIL (%s)" % "; ".join(verdict) if verdict
      else "PASS (answered, %d chars)" % len(text))
PY

say "=== done: $OUT/report.txt ==="
