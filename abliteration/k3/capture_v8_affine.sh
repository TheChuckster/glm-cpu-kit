#!/usr/bin/env bash
# Capture V2 layer-61 prompt activations and always restore accepted production.
set -euo pipefail
umask 077

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
CVECTOR=/home/chuck/ik_llama.cpp-v5/build-abliteration/bin/llama-cvector-generator
MODEL=/models/Kimi-K3-Q5attn-Abliterated-V2/Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf
COMPLETE=/models/Kimi-K3-Q5attn-Abliterated-V2/.complete
PROMPTS=/models/.abliteration/k3/v5-prompts
CAPTURE_DIR=/models/.abliteration/k3/v8-capture-caca44c
PRODUCTION=glm-server.service
PROTOCOL=$SCRIPT_DIR/V8_PROTOCOL.md
THREADS=64

CVECTOR_SHA256=47e921423d579806ce455aeedd366d8c471cb73eb5826540d1116471ba7a04b5
COMPLETE_SHA256=108e23b77c8a22da1f27524993d3788e7826a3bbbb4fb8b61f64332836e88b6f
HARMFUL_SHA256=98c044a2dd14047259d7c7e36d4464d125efabf54b3638904a41a73263b01cf8
HARMLESS_SHA256=6583c21a1668832301ab62c33e5dffa32d49511a049a2b30acd0f59ca20f3dfc
PROMPT_MANIFEST_SHA256=f4ea340c455c103d8cfef990e552ddae5474ef3f9e8eca62c3ab09d213b93af0
PROTOCOL_SHA256=2dfa7613d9381f842a13b69d41dee489cbd6b50ff6ee0acd89e944ac70a727e4
SERVER_SHA256=a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6

production_stopped=0
capture_running=0
capture_pid=0

die() {
    echo "capture_v8_affine: $*" >&2
    exit 1
}

check_sha256() {
    local expected=$1
    local path=$2
    local observed
    observed=$(sha256sum "$path" | awk '{print $1}')
    [[ "$observed" == "$expected" ]] || die "SHA-256 mismatch: $path"
}

wait_closed() {
    local port=$1
    local attempt
    for ((attempt = 0; attempt < 60; attempt++)); do
        if ! curl -fsS --max-time 1 "http://127.0.0.1:${port}/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "port ${port} did not close" >&2
    return 1
}

production_ready() {
    local health
    local main_pid
    local snapshot
    [[ "$(systemctl is-active "$PRODUCTION")" == active ]] || return 1
    health=$(curl -fsS --max-time 2 http://127.0.0.1:8080/health) || return 1
    [[ "$health" == *'"status":"ok"'* ]] || return 1
    snapshot=$(/usr/local/bin/glm-model status) || return 1
    [[ "$snapshot" == *'selected variant : kimi-k3-q5attn-abl  (kimi-k3)'* ]]
    [[ "$snapshot" == *'model directory  : /models/Kimi-K3-Q5attn-Abliterated'* ]]
    [[ "$snapshot" == *'service          : active'* ]]
    [[ "$snapshot" == *'health           : {"status":"ok"'* ]]
    [[ "$snapshot" == *'serving alias    : kimi-k3'* ]]
    main_pid=$(systemctl show "$PRODUCTION" --property=MainPID --value)
    [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || return 1
    [[ "$(sha256sum "/proc/$main_pid/exe" | awk '{print $1}')" == "$SERVER_SHA256" ]]
}

check_no_competing_workload() {
    local main_pid
    local pid
    main_pid=$(systemctl show "$PRODUCTION" --property=MainPID --value)
    [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || die "production MainPID is invalid"
    mapfile -t workload_pids < <(
        pgrep -f '/llama-(server|perplexity|cvector-generator|quantize)([[:space:]]|$)' || true
    )
    [[ "${#workload_pids[@]}" == 1 ]] \
        || die "expected only production model workload; found ${#workload_pids[@]}"
    pid=${workload_pids[0]}
    [[ "$pid" == "$main_pid" ]] || die "unexpected model workload PID $pid"
}

preflight() {
    production_ready || die "accepted production is not exactly healthy"
    check_no_competing_workload
    [[ -x "$CVECTOR" ]] || die "missing capture executable"
    [[ -s "$MODEL" ]] || die "missing V2 model"
    check_sha256 "$CVECTOR_SHA256" "$CVECTOR"
    check_sha256 "$COMPLETE_SHA256" "$COMPLETE"
    [[ "$(<"$COMPLETE")" == 845361056864 ]] || die "V2 byte count changed"
    check_sha256 "$HARMFUL_SHA256" "$PROMPTS/train.harmful.txt"
    check_sha256 "$HARMLESS_SHA256" "$PROMPTS/train.harmless.txt"
    check_sha256 "$PROMPT_MANIFEST_SHA256" "$PROMPTS/manifest.json"
    check_sha256 "$PROTOCOL_SHA256" "$PROTOCOL"
    [[ "$(wc -l < "$PROMPTS/train.harmful.txt")" == 359 ]] \
        || die "harmful prompt count changed"
    [[ "$(wc -l < "$PROMPTS/train.harmless.txt")" == 359 ]] \
        || die "harmless prompt count changed"
    [[ ! -e "$CAPTURE_DIR" ]] || die "capture path already exists: $CAPTURE_DIR"
}

# Invoked by the signal/exit trap below.
# shellcheck disable=SC2329
restore_production() {
    local status=$?
    trap - EXIT INT TERM HUP
    set +e
    if [[ "$capture_running" == 1 ]] && kill -0 "$capture_pid" 2>/dev/null; then
        kill -TERM "$capture_pid"
        wait "$capture_pid"
    fi
    if [[ "$production_stopped" == 1 ]]; then
        sudo -n systemctl start "$PRODUCTION" || status=1
        for ((attempt = 0; attempt < 600; attempt++)); do
            if production_ready >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        production_ready || status=1
        /usr/local/bin/glm-model status || status=1
    fi
    exit "$status"
}

case "${1:-}" in
    --check-production-only)
        [[ $# == 1 ]] || die "--check-production-only takes no argument"
        production_ready
        /usr/local/bin/glm-model status
        exit 0
        ;;
    --preflight-only)
        [[ $# == 1 ]] || die "--preflight-only takes no argument"
        preflight
        echo "V8 capture preflight passed; production unchanged"
        exit 0
        ;;
    "")
        [[ $# == 0 ]] || die "unexpected argument"
        ;;
    *)
        die "usage: $0 [--check-production-only|--preflight-only]"
        ;;
esac

preflight
trap restore_production EXIT INT TERM HUP
install -d -m 700 "$CAPTURE_DIR"

mapfile -t runtime_paths < <(
    ldd "$CVECTOR" |
        awk '{ for (i = 1; i <= NF; ++i) if ($i ~ /^\//) { print $i; break } }' |
        sort -u
)
[[ "${#runtime_paths[@]}" -gt 0 ]] || die "could not resolve capture runtime"
sha256sum \
    "$CVECTOR" "${runtime_paths[@]}" "$COMPLETE" \
    "$PROMPTS/train.harmful.txt" "$PROMPTS/train.harmless.txt" \
    "$PROMPTS/manifest.json" "$PROTOCOL" "$0" \
    > "$CAPTURE_DIR/engine-and-inputs.sha256"
{
    echo "method=k3-v8-v2-layer61-final-templated-prompt-position"
    echo "threads=$THREADS"
    echo "model=$MODEL"
    echo "positive=$PROMPTS/train.harmful.txt"
    echo "negative=$PROMPTS/train.harmless.txt"
    echo "output=$CAPTURE_DIR/v2-mean.gguf"
    echo "activations_output=$CAPTURE_DIR/v2-activations.gguf"
    echo "activations_layers=61"
    uname -a
} > "$CAPTURE_DIR/capture.env"

sudo -n systemctl stop "$PRODUCTION"
production_stopped=1
wait_closed 8080

"$CVECTOR" --model "$MODEL" \
    --method mean-last --apply-chat-template --jinja \
    --reasoning-format deepseek \
    --chat-template-kwargs '{"thinking_effort":"low"}' \
    --ctx-size 2048 --batch-size 2048 --ubatch-size 2048 \
    --threads "$THREADS" --threads-batch "$THREADS" -fa on \
    --positive-file "$PROMPTS/train.harmful.txt" \
    --negative-file "$PROMPTS/train.harmless.txt" \
    --output "$CAPTURE_DIR/v2-mean.gguf" \
    --activations-output "$CAPTURE_DIR/v2-activations.gguf" \
    --activations-layers 61 \
    > "$CAPTURE_DIR/capture.log" 2>&1 &
capture_pid=$!
capture_running=1

while kill -0 "$capture_pid" 2>/dev/null; do
    evaluated=$(grep -c '^Evaluating prompt\[' "$CAPTURE_DIR/capture.log" 2>/dev/null || true)
    rss=$(ps -o rss= -p "$capture_pid" | awk '{print $1}')
    echo "V8 capture progress pairs=${evaluated}/359 rss_kib=${rss:-unknown}"
    sleep 30
done
set +e
wait "$capture_pid"
capture_status=$?
set -e
capture_running=0
[[ "$capture_status" == 0 ]] || die "capture executable failed with status $capture_status"

[[ -s "$CAPTURE_DIR/v2-mean.gguf" ]] || die "missing V2 mean direction"
[[ -s "$CAPTURE_DIR/v2-activations.gguf" ]] || die "missing V2 activations"
sha256sum "$CAPTURE_DIR"/* > "$CAPTURE_DIR/all-artifacts.sha256"
chmod 600 "$CAPTURE_DIR"/*
echo "V8 V2 activation capture complete; restoring accepted production"
