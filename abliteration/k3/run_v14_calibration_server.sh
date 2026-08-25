#!/usr/bin/env bash
# Run one fixed V14 TTF phase PID and always restore accepted V1 production.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

SERVER=/home/chuck/ik_llama-v13-98de9a7f/build-v13/bin/llama-server
SERVER_REPO=/home/chuck/ik_llama-v13-98de9a7f
SERVER_BUILD=$SERVER_REPO/build-v13
SERVER_TEST=$SERVER_BUILD/bin/test-reasoning-prefill
PRODUCTION_SERVER=/home/chuck/ik_llama.cpp-abliteration/build-abliteration/bin/llama-server
MODEL_DIR=/models/Kimi-K3-Q5attn-Abliterated-V2
MODEL=/models/Kimi-K3-Q5attn-Abliterated-V2/Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf
COMPLETE=/models/Kimi-K3-Q5attn-Abliterated-V2/.complete
V2_VERIFY_DIR=/models/.abliteration/k3/v9-v2-reverify-e34450a
V2_VERIFY_JSON=$V2_VERIFY_DIR/model-verification.json
V2_VERIFY_TEXT=$V2_VERIFY_DIR/model-verification.txt
V2_INVENTORY=$V2_VERIFY_DIR/v2-shards.stat
PARTITION=/models/.abliteration/k3/v10-calibration-partition-v1
PARTITION_MANIFEST=$PARTITION/manifest.json
FAILURES=$PARTITION/calibration.failures.jsonl
STABILITY=$PARTITION/calibration.stability.jsonl
REMAINDER=$PARTITION/calibration.remainder.jsonl
TOOLS=/models/.abliteration/k3/eval-tools-v14-v1
STATE_HELPER=$TOOLS/verify_v14_calibration_state.py
STATE_CORE=$TOOLS/verify_v10_calibration_state.py
EVALUATOR=$TOOLS/evaluate_reasoning_prefill_api_v14.py
BASE_EVALUATOR=$TOOLS/evaluate_api.py
REVIEW_HELPER=$TOOLS/prepare_manual_review.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
REQUEST_PREFIX=$TOOLS/v10-calibration-request-prefix.json
GATE_HELPER=$TOOLS/gate_v14_calibration.py
GATE_CORE=$TOOLS/gate_v10_calibration.py
PROTOCOL=$TOOLS/V14_PROTOCOL.md
PRIOR_RESULTS=$TOOLS/V13_RESULTS.md
REASONING_PREFILL=$TOOLS/v14-reasoning-prefill.txt
ENGINE_MANIFEST=$TOOLS/v14-engine-sources.sha256
PREFLIGHT_HELPER=$TOOLS/preflight_v14_reasoning_prefill.py
PREFLIGHT_ROOT=/models/.abliteration/k3/v14-response-free-preflight-v1
CONTROL_RECEIPT=$PREFLIGHT_ROOT/control.json
PREFLIGHT_RECEIPT=$PREFLIGHT_ROOT/preflight.json
RUN_ROOT=/models/.abliteration/k3/v14-calibration-run-v1
PRODUCTION=glm-server.service

production_stopped=0
candidate_started=0
inventory_tmp=

die() {
    echo "run_v14_calibration_server: $*" >&2
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
    inventory_tmp=$(mktemp /tmp/k3-v14-v2-inventory.XXXXXX)
    stat -c '%n\t%s\t%Y\t%Z\t%D\t%i\t%a' "${paths[@]}" > "$inventory_tmp"
    cmp "$V2_INVENTORY" "$inventory_tmp" >/dev/null || die "V2 shard inventory changed"
}

startup_has_diagnostic() {
    local main_pid=$1
    journalctl \
        --unit="$UNIT" \
        "_PID=$main_pid" \
        --no-pager \
        --output=cat \
        --grep='(^|[^[:alpha:]])(WARN|ERR|ERROR|warning|error)([^[:alpha:]]|$)' \
        --quiet
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
                    b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6 \
                    "/proc/${main_pid}/exe"
                if startup_has_diagnostic "$main_pid"; then
                    journalctl --unit="$UNIT" "_PID=$main_pid" --no-pager --output=cat >&2
                    die "$UNIT emitted a startup warning or error"
                fi
                "$STATE_HELPER" "$PROMPT" \
                    --prompt-file "$PROMPT_FILE" \
                    --output "$RUN_DIR/startup-state.json"
                echo "READY unit=$UNIT pid=$main_pid alias=$ALIAS prompt=$PROMPT phase=$PHASE dataset=$DATASET"
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
if [[ $# -eq 4 && "$1" == --preflight-only ]]; then
    preflight_only=1
    PROMPT=$2
    PHASE=$3
    [[ "$4" == --no-response ]] || die "preflight requires --no-response acknowledgement"
elif [[ $# -eq 2 ]]; then
    PROMPT=$1
    PHASE=$2
else
    die "usage: $0 [--preflight-only prompt14 PHASE --no-response] | prompt14 PHASE | --check-production-only"
fi

case "$PROMPT" in
    prompt14)
        ALIAS=kimi-k3-q5attn-abl-v14-ttf-cal
        PROMPT_FILE=$TOOLS/v10-system-prompt-02-semantic-contract.txt
        PROMPT_SHA=44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9
        ;;
    *) die "prompt must be prompt14" ;;
esac
case "$PHASE" in
    failures) DATASET=$FAILURES ;;
    stability) DATASET=$STABILITY ;;
    remainder) DATASET=$REMAINDER ;;
    *) die "phase must be failures, stability, or remainder" ;;
esac
# The immutable V10 gate core derives this compatibility tag for prompt14.
UNIT=kimi-k3-q5attn-abl-v10-${PROMPT}-${PHASE}-cal.service
RUN_DIR=$RUN_ROOT/$PROMPT/$PHASE

trap restore_production EXIT INT TERM HUP

# Every check in this block completes before production is stopped.
check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$PRODUCTION_SERVER"
check_sha256 b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6 "$SERVER"
check_sha256 1fdc7c3fe29d6a3fdba22d74e5101e317cdacfc72ad7a70bf088786c48b5f276 "$SERVER_BUILD/examples/mtmd/libmtmd.so"
check_sha256 9dc6d78d4232bc919c1493e00f6ec3c198608a419c5485f1887cc29c0df4fdbf "$SERVER_BUILD/src/libllama.so"
check_sha256 bfcb4e24f698a78de0f18ce03d1109f80042124959c1cca2b7affd470e9b3abc "$SERVER_BUILD/ggml/src/libggml.so"
check_sha256 cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f "$SERVER_TEST"
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
check_sha256 da323ac2826309ba37f07829f4fe6f2c78175dfff9f32227e842bbb5244e9bbf "$PARTITION_MANIFEST"
check_sha256 204dd0a5e95314f83a2869420c6c6bd74d55d97aca66a85bcbcb8d3a73e369a8 "$FAILURES"
check_sha256 55f56229fa3730ff17bd2abe91f5b848fe33ab5f6f8ef6d4aa4c997dcff28b79 "$STABILITY"
check_sha256 cbea682929ecc470d02a84d1ffc4166a3780d464204e93fa85206b3ea7c0f96a "$REMAINDER"
check_sha256 "$PROMPT_SHA" "$PROMPT_FILE"
check_sha256 ab50c9ecab58e47f6e69033f6df5229f25b5eae0cc583e960fa3fe1dc5938b57 "$REASONING_PREFILL"
check_sha256 cc9dc22531d094270d8e0b976e29e4fbc69f22a3af5e3d8a5322035e7609cec5 "$EVALUATOR"
check_sha256 1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a "$BASE_EVALUATOR"
check_sha256 6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54 "$REVIEW_HELPER"
check_sha256 697bcfdf1df15f6b0ac33a5ad9fcfec561efe3bbcc53ab63012b6b0370f7f61f "$STATE_HELPER"
check_sha256 291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae "$STATE_CORE"
check_sha256 63836bc34d7096a062e90cee855d320944fac71ab55769700f6dfa3fab8e4616 "$PROVENANCE_HELPER"
check_sha256 5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220 "$REQUEST_PREFIX"
check_sha256 cc9e0efb048ebc0a2b18e7bb64c191c3a28e926802ddaff0c1b2602363d3e7a5 "$GATE_HELPER"
check_sha256 5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59 "$GATE_CORE"
check_sha256 5e9ee7350131bd6832b47ceca503b4a2451e99a14702ebcc69d27bb65c12339f "$PROTOCOL"
check_sha256 2f29a61e980d9bf3d5d563590714dc4665effd5e0ba1b79941a276e5e81a5fc0 "$PRIOR_RESULTS"
check_sha256 0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13 "$ENGINE_MANIFEST"
check_sha256 258d32713bba3600b1e67e956db04862b0b9121711ebe2f53041aa59c238a1ee "$PREFLIGHT_HELPER"
check_sha256 d3337062d491082cd308e5e5bbc20d2013941a126d4e494cd8f56eca2b395772 "$CONTROL_RECEIPT"
check_sha256 c49ccecc2a10cf092f8ad3f4ef6bc0d81b0e578fabdf19a4589132964eb4d5f8 "$PREFLIGHT_RECEIPT"
[[ "$(stat -c %a "$PREFLIGHT_ROOT")" == 700 ]] || die "preflight root mode changed"
[[ "$(stat -c %a "$CONTROL_RECEIPT")" == 600 ]] || die "control receipt mode changed"
[[ "$(stat -c %a "$PREFLIGHT_RECEIPT")" == 600 ]] || die "preflight receipt mode changed"
[[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == 98de9a7f69ef3d387b676ad4a3ee14946ac88f94 ]] \
    || die "candidate engine checkout changed"
[[ -z "$(git -C "$SERVER_REPO" status --porcelain)" ]] || die "candidate engine checkout is dirty"
(cd "$SERVER_REPO" && sha256sum --check "$ENGINE_MANIFEST")
"$SERVER_TEST"
ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-reasoning-prefill$'
[[ "$(<"$COMPLETE")" == 845361056864 ]] || die "V2 byte count changed"
[[ -s "$MODEL" ]] || die "missing V2 shard 1"
[[ -r /home/chuck/.glm-api-key ]] || die "API key file is unreadable"
verify_v2_inventory
production_ready || die "accepted production is not exact and healthy"
port_closed 8081 || die "calibration port 8081 is already open"
! systemctl is-active --quiet "$UNIT" || die "candidate unit is already active"
[[ ! -e "$RUN_DIR" ]] || die "refusing to reuse calibration run directory: $RUN_DIR"

case "$PHASE" in
    stability)
        "$GATE_HELPER" require-passed-phase "$PROMPT" failures \
            --receipt "$RUN_ROOT/$PROMPT/failures/phase.gate.json"
        ;;
    remainder)
        "$GATE_HELPER" require-passed-phase "$PROMPT" failures \
            --receipt "$RUN_ROOT/$PROMPT/failures/phase.gate.json"
        "$GATE_HELPER" require-passed-phase "$PROMPT" stability \
            --receipt "$RUN_ROOT/$PROMPT/stability/phase.gate.json"
        ;;
esac
if [[ "$preflight_only" == 1 ]]; then
    echo "PASS V14 preflight prompt=$PROMPT phase=$PHASE; production remains active"
    exit 0
fi

mkdir -p "$RUN_ROOT/$PROMPT"
chmod 700 "$RUN_ROOT" "$RUN_ROOT/$PROMPT"
mkdir "$RUN_DIR"
chmod 700 "$RUN_DIR"

production_stopped=1
sudo -n systemctl stop "$PRODUCTION"
wait_closed 8080

candidate_started=1
IFS= read -r reasoning_prefill_text < "$REASONING_PREFILL"
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
    --reasoning-prefill "$reasoning_prefill_text"
wait_candidate_ready

while systemctl is-active --quiet "$UNIT"; do
    sleep 5
done
"$GATE_HELPER" verify-phase --receipt "$RUN_DIR/phase.gate.json"
echo "COMPLETE unit=$UNIT prompt=$PROMPT phase=$PHASE; restoring accepted V1"
