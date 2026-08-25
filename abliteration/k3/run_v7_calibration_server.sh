#!/usr/bin/env bash
# Run the fixed v7 calibration server and always restore accepted production.
set -euo pipefail

SERVER=/home/chuck/ik_llama.cpp-abliteration/build-abliteration/bin/llama-server
MODEL=/models/Kimi-K3-Q5attn-Abliterated-V2/Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf
COMPLETE=/models/Kimi-K3-Q5attn-Abliterated-V2/.complete
VECTOR=/models/.abliteration/k3/run/k3-refusal-direction.gguf
FAILURES=/models/.abliteration/k3/v7-calibration-de9ea79/calibration.failures.jsonl
STABILITY=/models/.abliteration/k3/v7-calibration-de9ea79/calibration.stability.jsonl
CONTROL_HELPER=/models/.abliteration/k3/eval-tools-v7-de9ea79/set_v7_control.py
UNIT=kimi-k3-q5attn-abl-v7-cal.service
PRODUCTION=glm-server.service

production_stopped=0
candidate_started=0

check_sha256() {
    local expected=$1
    local path=$2
    local observed
    observed=$(sha256sum "$path" | awk '{print $1}')
    if [[ "$observed" != "$expected" ]]; then
        echo "SHA-256 mismatch: $path" >&2
        exit 1
    fi
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
}

wait_candidate_ready() {
    local attempt
    local journal_match
    local main_pid
    for ((attempt = 0; attempt < 360; attempt++)); do
        if ! systemctl is-active --quiet "$UNIT"; then
            echo "candidate unit exited during load" >&2
            return 1
        fi
        main_pid=$(systemctl show "$UNIT" --property=MainPID --value)
        if [[ "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
            journal_match=$(journalctl \
                --unit="$UNIT" \
                "_PID=$main_pid" \
                --no-pager \
                --output=cat \
                --grep='HTTP server listening' \
                --lines=1 \
                2>/dev/null || true)
            if [[ "$journal_match" == *'HTTP server listening'* ]]; then
                if ! curl -fsS --max-time 2 \
                    http://127.0.0.1:8081/health >/dev/null; then
                    echo "candidate announced listening but health failed" >&2
                    return 1
                fi
                echo "READY unit=$UNIT pid=$main_pid alias=kimi-k3-q5attn-abl-v7-cal"
                return 0
            fi
        fi
        sleep 1
    done
    echo "candidate did not announce HTTP readiness" >&2
    return 1
}

# Invoked by the signal/exit trap below.
# shellcheck disable=SC2329
restore_production() {
    local status=$?
    trap - EXIT INT TERM HUP
    set +e
    if [[ "$candidate_started" == 1 ]]; then
        sudo -n systemctl stop "$UNIT"
        wait_closed 8081 || status=1
    fi
    if [[ "$production_stopped" == 1 ]]; then
        sudo -n systemctl start "$PRODUCTION" || status=1
        for _attempt in $(seq 1 360); do
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

if (( $# > 0 )); then
    if [[ "$1" == --check-production-only && $# == 1 ]]; then
        production_ready
        /usr/local/bin/glm-model status
        exit 0
    fi
    echo "usage: $0 [--check-production-only]" >&2
    exit 2
fi
trap restore_production EXIT INT TERM HUP

check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$SERVER"
check_sha256 108e23b77c8a22da1f27524993d3788e7826a3bbbb4fb8b61f64332836e88b6f "$COMPLETE"
check_sha256 7ce9aee3339ee267fa3de8017bba933168467a3d5a59f6d4c7da080b0b0588ad "$VECTOR"
check_sha256 204dd0a5e95314f83a2869420c6c6bd74d55d97aca66a85bcbcb8d3a73e369a8 "$FAILURES"
check_sha256 55f56229fa3730ff17bd2abe91f5b848fe33ab5f6f8ef6d4aa4c997dcff28b79 "$STABILITY"
check_sha256 51d7869ca576102d769f3821f4490ab369d847d2c5e2c64a9784b2d65244eae8 "$CONTROL_HELPER"
[[ -s "$MODEL" ]]
[[ "$(<"$COMPLETE")" == 845361056864 ]]
[[ "$(systemctl is-active "$PRODUCTION")" == active ]]
/usr/local/bin/glm-model status

sudo -n systemctl stop "$PRODUCTION"
production_stopped=1
wait_closed 8080

sudo -n systemd-run \
    --unit="$UNIT" \
    --collect \
    --property=Type=exec \
    --property=User=chuck \
    --property=Group=chuck \
    --property=LimitMEMLOCK=infinity \
    --property=TimeoutStopSec=60 \
    --property=WorkingDirectory=/home/chuck/ik_llama.cpp-abliteration \
    -- "$SERVER" \
    --model "$MODEL" \
    --alias kimi-k3-q5attn-abl-v7-cal \
    --host 127.0.0.1 --port 8081 \
    --numa distribute \
    --ctx-size 131072 \
    --defrag-thold 0.1 \
    --parallel 1 \
    --threads 64 --threads-batch 64 \
    --batch-size 2048 --ubatch-size 2048 \
    -fa on \
    --cache-type-k q8_0 --cache-type-v q8_0 \
    --mlock \
    --jinja \
    --repeat-penalty 1.1 --repeat-last-n 256 \
    --metrics \
    --api-key-file /home/chuck/.glm-api-key \
    --reasoning-format deepseek \
    --cache-type-v f16 \
    --repeat-penalty 1.0 \
    --temp 1.0 --top-p 0.95 \
    --chat-template-kwargs '{"thinking_effort": "low"}' \
    --reasoning-budget 1024 \
    --spec-type ngram-mod:n_max=16,n_min=2 \
    --cache-ram 0
candidate_started=1
wait_candidate_ready

while systemctl is-active --quiet "$UNIT"; do
    sleep 5
done
echo "candidate unit exited unexpectedly" >&2
exit 1
