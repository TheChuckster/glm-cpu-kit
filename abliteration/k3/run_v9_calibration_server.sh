#!/usr/bin/env bash
# Run one fixed V9 calibration PID and always restore accepted V1 production.
set -euo pipefail
umask 077

SERVER=/home/chuck/ik_llama-v9-35db6bb3/build/bin/llama-server
SERVER_REPO=/home/chuck/ik_llama-v9-35db6bb3
PRODUCTION_SERVER=/home/chuck/ik_llama.cpp-abliteration/build-abliteration/bin/llama-server
MODEL_DIR=/models/Kimi-K3-Q5attn-Abliterated-V2
MODEL=/models/Kimi-K3-Q5attn-Abliterated-V2/Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf
COMPLETE=/models/Kimi-K3-Q5attn-Abliterated-V2/.complete
V2_VERIFY_DIR=/models/.abliteration/k3/v9-v2-reverify-e34450a
V2_VERIFY_JSON=$V2_VERIFY_DIR/model-verification.json
V2_VERIFY_TEXT=$V2_VERIFY_DIR/model-verification.txt
V2_INVENTORY=$V2_VERIFY_DIR/v2-shards.stat
ARTIFACT_DIR=/models/.abliteration/k3/v9-affine-35db6bb3-d5c0a018
ARTIFACT_MANIFEST=$ARTIFACT_DIR/manifest.json
FAILURES=/models/.abliteration/k3/v7-calibration-de9ea79/calibration.failures.jsonl
STABILITY=/models/.abliteration/k3/v7-calibration-de9ea79/calibration.stability.jsonl
TOOLS=/models/.abliteration/k3/eval-tools-v9-e34450a
STATE_HELPER=$TOOLS/verify_v9_calibration_state.py
EVALUATOR=$TOOLS/evaluate_api.py
REVIEW_HELPER=$TOOLS/prepare_manual_review.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
REQUEST_PREFIX=$TOOLS/v9-calibration-request-prefix.json
STABILITY_REQUEST_PREFIX=$TOOLS/v9-calibration-stability-request-prefix.json
GATE_HELPER=$TOOLS/gate_v9_calibration.py
RUN_ROOT=/models/.abliteration/k3/v9-calibration-run-e34450a
PRODUCTION=glm-server.service

production_stopped=0
candidate_started=0
inventory_tmp=

die() {
    echo "run_v9_calibration_server: $*" >&2
    exit 1
}

check_sha256() {
    local expected=$1
    local path=$2
    local observed
    [[ -f "$path" ]] || die "missing file: $path"
    observed=$(sha256sum "$path" | awk '{print $1}')
    [[ "$observed" == "$expected" ]] || die "SHA-256 mismatch: $path"
}

port_closed() {
    local port=$1
    ! ss -H -ltn "sport = :${port}" | grep -q .
}

wait_closed() {
    local port=$1
    local attempt
    for ((attempt = 0; attempt < 60; attempt++)); do
        if port_closed "$port"; then
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
    local observed
    local snapshot
    [[ "$(systemctl is-active "$PRODUCTION")" == active ]] || return 1
    main_pid=$(systemctl show "$PRODUCTION" --property=MainPID --value) || return 1
    [[ "$main_pid" =~ ^[1-9][0-9]*$ ]] || return 1
    [[ "$(readlink -f "/proc/${main_pid}/exe")" == "$PRODUCTION_SERVER" ]] || return 1
    observed=$(sha256sum "/proc/${main_pid}/exe" | awk '{print $1}') || return 1
    [[ "$observed" == a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 ]] \
        || return 1
    health=$(curl -fsS --max-time 2 http://127.0.0.1:8080/health) || return 1
    [[ "$health" == *'"status":"ok"'* ]] || return 1
    snapshot=$(/usr/local/bin/glm-model status) || return 1
    [[ "$snapshot" == *'selected variant : kimi-k3-q5attn-abl  (kimi-k3)'* ]]
    [[ "$snapshot" == *'model directory  : /models/Kimi-K3-Q5attn-Abliterated'* ]]
    [[ "$snapshot" == *'service          : active'* ]]
    [[ "$snapshot" == *'health           : {"status":"ok"'* ]]
    [[ "$snapshot" == *'serving alias    : kimi-k3'* ]]
}

verify_v2_inventory() {
    local -a paths
    mapfile -d '' paths < <(
        find "$MODEL_DIR" -maxdepth 1 -type f \
            \( -name 'Kimi-K3-Q5attn-Abliterated-V2-*.gguf' -o -name '.complete' \) \
            -print0 | sort -z
    )
    [[ "${#paths[@]}" -eq 20 ]] || die "expected 19 V2 shards plus .complete"
    inventory_tmp=$(mktemp /tmp/k3-v9-v2-inventory.XXXXXX)
    stat -c '%n\t%s\t%Y\t%Z\t%D\t%i\t%a' "${paths[@]}" > "$inventory_tmp"
    cmp "$V2_INVENTORY" "$inventory_tmp" >/dev/null || die "V2 shard inventory changed"
}

wait_candidate_ready() {
    local attempt
    local journal_match
    local main_pid
    for ((attempt = 0; attempt < 900; attempt++)); do
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
                [[ "$(readlink -f "/proc/${main_pid}/exe")" == "$SERVER" ]] || return 1
                check_sha256 \
                    5a93d3a75c2ec1cec936233827bc81adb3dc31d838c0e761d6e4d9543f503f26 \
                    "/proc/${main_pid}/exe"
                "$STATE_HELPER" "$COEFFICIENT" \
                    --output "$RUN_DIR/startup-state.json"
                echo "READY unit=$UNIT pid=$main_pid alias=$ALIAS coefficient=$COEFFICIENT"
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
    local attempt
    trap - EXIT INT TERM HUP
    set +e
    if [[ "$candidate_started" == 1 ]]; then
        sudo -n systemctl stop "$UNIT"
        wait_closed 8081 || status=1
    fi
    if [[ "$production_stopped" == 1 ]]; then
        sudo -n systemctl start "$PRODUCTION" || status=1
        for ((attempt = 0; attempt < 900; attempt++)); do
            if production_ready >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
        production_ready || status=1
        /usr/local/bin/glm-model status || status=1
    fi
    if [[ -n "$inventory_tmp" && -f "$inventory_tmp" ]]; then
        rm -f -- "$inventory_tmp"
    fi
    exit "$status"
}

if [[ $# -eq 1 && "$1" == --check-production-only ]]; then
    check_sha256 \
        a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 \
        "$PRODUCTION_SERVER"
    production_ready
    port_closed 8081
    /usr/local/bin/glm-model status
    exit 0
fi
preflight_only=0
if [[ $# -eq 2 && "$1" == --preflight-only ]]; then
    preflight_only=1
    COEFFICIENT=$2
elif [[ $# -eq 1 ]]; then
    COEFFICIENT=$1
else
    die "usage: $0 [--preflight-only] {alpha0|alpha-m0p5} | --check-production-only"
fi
case "$COEFFICIENT" in
    alpha0)
        ARTIFACT=$ARTIFACT_DIR/affine-alpha0.gguf
        ARTIFACT_SHA=9f8c1184a91c0492d10d95af5fea22624171b5c4b23641bd32ee2667dc6cf611
        ALIAS=kimi-k3-q5attn-abl-v9-alpha0-cal
        UNIT=kimi-k3-q5attn-abl-v9-alpha0-cal.service
        ;;
    alpha-m0p5)
        ARTIFACT=$ARTIFACT_DIR/affine-alpha-m0p5.gguf
        ARTIFACT_SHA=581e359359d0c1b7b642b015a7bd4355078314d0e890d8879522b64df262bfe8
        ALIAS=kimi-k3-q5attn-abl-v9-alpha-m0p5-cal
        UNIT=kimi-k3-q5attn-abl-v9-alpha-m0p5-cal.service
        ;;
    *) die "coefficient must be alpha0 or alpha-m0p5" ;;
esac
RUN_DIR=$RUN_ROOT/$COEFFICIENT

trap restore_production EXIT INT TERM HUP

# Every check in this block completes before production is stopped.
check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$PRODUCTION_SERVER"
check_sha256 5a93d3a75c2ec1cec936233827bc81adb3dc31d838c0e761d6e4d9543f503f26 "$SERVER"
check_sha256 986dec76a01691be0c7e7b94add7b07983d0789dd3740b725093040acffed537 "$SERVER_REPO/build/examples/mtmd/libmtmd.so"
check_sha256 32208991ddfa789adc89ed65b85a514c15740c6afd239e32e4b1c2ef1d86791d "$SERVER_REPO/build/src/libllama.so"
check_sha256 05c2b42c95c3eef68ff60a6df1657be5d2bb8f27582d42ac69ea8f0c2756f314 "$SERVER_REPO/build/ggml/src/libggml.so"
check_sha256 1fd75fe70354a416d75aef22bcae68c47bd25d20e2d0568c30b1a9838cf62f11 /lib/x86_64-linux-gnu/libstdc++.so.6
check_sha256 e9c4b28d340e415b8137480ec442662f981e1399386c5931dae0e886e3639e91 /lib/x86_64-linux-gnu/libm.so.6
check_sha256 d93224d2b0dab4247598be683adca02f5cf00586f99c187579cd7e92058fb7cb /lib/x86_64-linux-gnu/libgcc_s.so.1
check_sha256 8db37cf3f2169f59a0f07ef1fea308c35656668c64c8ff294e1860f4121eb161 /lib/x86_64-linux-gnu/libc.so.6
check_sha256 cd4df4f3c7b83673d61189bf2eaebd33ca4f2853ab9772b8a25e025ef99b1e81 /lib64/ld-linux-x86-64.so.2
check_sha256 135f3c8f006d2fe5e68e51281c7974cb991a03de3bfb3593d68d174dfcf854d1 /lib/x86_64-linux-gnu/libgomp.so.1
check_sha256 108e23b77c8a22da1f27524993d3788e7826a3bbbb4fb8b61f64332836e88b6f "$COMPLETE"
check_sha256 23fce7007554d8e25f1b90d170c5298069eb5839de41a06ed9541bf2da4d0a4d "$V2_VERIFY_JSON"
check_sha256 dccb89fd94eb56625c8a6726ff348a93af7b7e7b79ecfd00f536ccdb2a43df0f "$V2_VERIFY_TEXT"
check_sha256 ebb0a7791c857476ae81dbdfc82baf414a60ca6f10b14c4a8b6e1ec63918ddf0 "$V2_INVENTORY"
check_sha256 "$ARTIFACT_SHA" "$ARTIFACT"
check_sha256 173f9b766313af79966ccd9e7e70749e48ee01bc93df06e94d0d036ba344fcdb "$ARTIFACT_MANIFEST"
check_sha256 204dd0a5e95314f83a2869420c6c6bd74d55d97aca66a85bcbcb8d3a73e369a8 "$FAILURES"
check_sha256 55f56229fa3730ff17bd2abe91f5b848fe33ab5f6f8ef6d4aa4c997dcff28b79 "$STABILITY"
check_sha256 5cf826e5fb28e277c8a5c11b6dce682a17898972d970892738c1d7ccf528bb69 "$EVALUATOR"
check_sha256 6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54 "$REVIEW_HELPER"
check_sha256 8d6213a2c9a6979744713dd514a91457bbb6461940fa009565f8500d0b51738c "$STATE_HELPER"
check_sha256 6b1b52ad0e9efe9cf5fb9ddc7910bb4eb1e7adfe02159af74879ead4f485a4bc "$PROVENANCE_HELPER"
check_sha256 3efe905fb3720aa0dd585aa71cfe97f2c1ef325b9214be233437df7c86792ae2 "$REQUEST_PREFIX"
check_sha256 e724ca715dca19590826aebaed02e7b43e44a6a2a516c733a9cffef6a94e1bae "$STABILITY_REQUEST_PREFIX"
check_sha256 5e84597e19d453c504270002d087f69a66e7cf1604acb87e9a1e3ea51ba6131b "$GATE_HELPER"
[[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == 35db6bb3e4de67c1703ffbb3b98e1690296c8d03 ]] \
    || die "candidate engine checkout changed"
[[ -z "$(git -C "$SERVER_REPO" status --porcelain)" ]] || die "candidate engine checkout is dirty"
[[ "$(<"$COMPLETE")" == 845361056864 ]] || die "V2 byte count changed"
[[ -s "$MODEL" ]] || die "missing V2 shard 1"
[[ -r /home/chuck/.glm-api-key ]] || die "API key file is unreadable"
verify_v2_inventory
production_ready || die "accepted production is not exact and healthy"
port_closed 8081 || die "calibration port 8081 is already open"
! systemctl is-active --quiet "$UNIT" || die "candidate unit is already active"
[[ ! -e "$RUN_DIR" ]] || die "refusing to reuse calibration run directory: $RUN_DIR"
if [[ "$COEFFICIENT" == alpha-m0p5 ]]; then
    "$GATE_HELPER" require-rejected alpha0 \
        --selection "$RUN_ROOT/alpha0/selection.json"
fi
if [[ "$preflight_only" == 1 ]]; then
    echo "PASS V9 preflight coefficient=$COEFFICIENT; production remains active"
    exit 0
fi
mkdir -p "$RUN_ROOT"
chmod 700 "$RUN_ROOT"
mkdir "$RUN_DIR"
chmod 700 "$RUN_DIR"

production_stopped=1
sudo -n systemctl stop "$PRODUCTION"
wait_closed 8080

candidate_started=1
sudo -n systemd-run \
    --unit="$UNIT" \
    --collect \
    --property=Type=exec \
    --property=User=chuck \
    --property=Group=chuck \
    --property=LimitMEMLOCK=infinity \
    --property=TimeoutStopSec=60 \
    --property=WorkingDirectory="$SERVER_REPO" \
    -- "$SERVER" \
    --model "$MODEL" \
    --alias "$ALIAS" \
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
    --cache-ram 0 \
    --control-vector-affine-subspace "$ARTIFACT"
wait_candidate_ready

while systemctl is-active --quiet "$UNIT"; do
    sleep 5
done
echo "candidate unit exited unexpectedly" >&2
exit 1
