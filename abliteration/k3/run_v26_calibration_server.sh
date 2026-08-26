#!/usr/bin/env bash
# Run one fixed V26 quote-star-DRY phase PID and restore accepted V1 production.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

SERVER=/home/chuck/ik_llama-v26-78bdb309/build-v26/bin/llama-server
SERVER_REPO=/home/chuck/ik_llama-v26-78bdb309
SERVER_BUILD=$SERVER_REPO/build-v26
SERVER_REASONING_TEST=$SERVER_BUILD/bin/test-reasoning-prefill
SERVER_DRY_TEST=$SERVER_BUILD/bin/test-greedy-dry
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
TOOLS=/models/.abliteration/k3/eval-tools-v26-v2
STATE_HELPER=$TOOLS/verify_v26_calibration_state.py
STATE_CORE=$TOOLS/verify_v10_calibration_state.py
EVALUATOR=$TOOLS/evaluate_reasoning_prefill_api_v26.py
BASE_EVALUATOR=$TOOLS/evaluate_api.py
REVIEW_HELPER=$TOOLS/prepare_manual_review.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
REQUEST_PREFIX=$TOOLS/v10-calibration-request-prefix.json
GATE_HELPER=$TOOLS/gate_v26_calibration.py
GATE_CORE=$TOOLS/gate_v10_calibration.py
PROTOCOL=$TOOLS/V26_PROTOCOL.md
PRIOR_RESULTS=$TOOLS/V25_RESULTS.md
REASONING_PREFILL=$TOOLS/v26-reasoning-prefill.txt
ENGINE_MANIFEST=$TOOLS/v26-engine-sources.sha256
DRY_REPLAY_SOURCE=$TOOLS/dry_replay_v26.cpp
PREFLIGHT_HELPER=$TOOLS/preflight_v26_reasoning_prefill.py
RESPONSE_FREE_LAUNCHER=$TOOLS/run_v26_response_free_preflight.sh
TEST_SUITE=$TOOLS/test_v26_calibration.py
PREFLIGHT_ROOT=/models/.abliteration/k3/v26-response-free-preflight-v1
CONTROL_RECEIPT=$PREFLIGHT_ROOT/control.json
PREFLIGHT_RECEIPT=$PREFLIGHT_ROOT/preflight.json
RUN_ROOT=/models/.abliteration/k3/v26-calibration-run-v1
PRODUCTION=glm-server.service

production_stopped=0
candidate_started=0
inventory_tmp=

die() {
    echo "run_v26_calibration_server: $*" >&2
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
    inventory_tmp=$(mktemp /tmp/k3-v26-v2-inventory.XXXXXX)
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
                    d65ea913f2bab718729b866b3d02c749ed93f6247d3a18e1807be7b617e95c7c \
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
    die "usage: $0 [--preflight-only prompt26 PHASE --no-response] | prompt26 PHASE | --check-production-only"
fi

case "$PROMPT" in
    prompt26)
        ALIAS=kimi-k3-q5attn-abl-v26-quote-star-dry-ttf-cal
        PROMPT_FILE=$TOOLS/v10-system-prompt-02-semantic-contract.txt
        PROMPT_SHA=44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9
        ;;
    *) die "prompt must be prompt26" ;;
esac
case "$PHASE" in
    failures) DATASET=$FAILURES ;;
    stability) DATASET=$STABILITY ;;
    remainder) DATASET=$REMAINDER ;;
    *) die "phase must be failures, stability, or remainder" ;;
esac
# The immutable V10 gate core derives this compatibility tag for prompt26.
UNIT=kimi-k3-q5attn-abl-v10-${PROMPT}-${PHASE}-cal.service
RUN_DIR=$RUN_ROOT/$PROMPT/$PHASE

trap restore_production EXIT INT TERM HUP

# Every check in this block completes before production is stopped.
expected_tools=(
    V25_RESULTS.md
    V26_PROTOCOL.md
    capture_server_provenance.py
    dry_replay_v26.cpp
    evaluate_api.py
    evaluate_reasoning_prefill_api_v26.py
    gate_v10_calibration.py
    gate_v26_calibration.py
    preflight_v26_reasoning_prefill.py
    prepare_manual_review.py
    run_v26_calibration_server.sh
    run_v26_response_free_preflight.sh
    test_v26_calibration.py
    v10-calibration-request-prefix.json
    v10-system-prompt-02-semantic-contract.txt
    v26-engine-sources.sha256
    v26-reasoning-prefill.txt
    verify_v10_calibration_state.py
    verify_v26_calibration_state.py
)
mapfile -t observed_tools < <(
    find "$TOOLS" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort
)
[[ "${observed_tools[*]}" == "${expected_tools[*]}" ]] \
    || die "V26 finalized tool-tree membership changed"
check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$PRODUCTION_SERVER"
check_sha256 d65ea913f2bab718729b866b3d02c749ed93f6247d3a18e1807be7b617e95c7c "$SERVER"
check_sha256 8114a991223494452fcc68f1ee5aa30d7c9b45cc82743cd1fd4603a0ff68f117 "$SERVER_BUILD/examples/mtmd/libmtmd.so"
check_sha256 40e87f0923c0441b0028ca4c6bd69634de06f461895860cb97716a1d2b7fbf92 "$SERVER_BUILD/src/libllama.so"
check_sha256 531e2bd3d589154576f30763f81776e66ad4f605415a5e7598d99bf81bdadd4c "$SERVER_BUILD/ggml/src/libggml.so"
check_sha256 523a2f44d17fd30d5fc923b22ef18b4a9956103c78d1fbced58d12ef43751939 "$SERVER_REASONING_TEST"
check_sha256 dcac9b99da0f6a291f431ae6748544914121dce9eb3f9889ced16cd1e2850a85 "$SERVER_DRY_TEST"
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
check_sha256 e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c "$REASONING_PREFILL"
check_sha256 1fd5e237f56d7a002c08d556d26055d6f3bc13af40d65d40bcc29e5ef17bbd31 "$EVALUATOR"
check_sha256 1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a "$BASE_EVALUATOR"
check_sha256 6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54 "$REVIEW_HELPER"
check_sha256 73a477a84d5dfb63f4b7d45a562104dae0a6375cc5f2614a722bafc3498fc23d "$STATE_HELPER"
check_sha256 291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae "$STATE_CORE"
check_sha256 6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22 "$PROVENANCE_HELPER"
check_sha256 5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220 "$REQUEST_PREFIX"
check_sha256 f5fc7165c40cdc8b081e3def5e864a9bde15dd9577c7de5b084483f838709c81 "$GATE_HELPER"
check_sha256 5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59 "$GATE_CORE"
check_sha256 420662e1064d65ca8cc5b383491c52b6495dee1e4afcf381e2ba8f832930531d "$PROTOCOL"
check_sha256 2538a52ef9b2b7ca0cc65ab160470041635d2776a7fd84e1171a055c4b56eec8 "$PRIOR_RESULTS"
check_sha256 9e6f05f3e00f4c3a917f24a882e29d61c09be34fee73d1b4b2cc0ad8313a7154 "$ENGINE_MANIFEST"
check_sha256 a7f5015d2bd5edf95846b340c90c0b8694b057555fa0699e9a23d42ac0fe2429 "$PREFLIGHT_HELPER"
check_sha256 4cdccd3ebaa42015fa3d83d5dfa88be08d3aa34593ddcf46a5742d7e676a1e5a "$DRY_REPLAY_SOURCE"
check_sha256 cf61ab027dff72c238738a2edd9965399a9d7a2f6106d1eb69541b6ba553819a "$RESPONSE_FREE_LAUNCHER"
check_sha256 ffe69e7616830b67edb82368458336535c976ca9cf3b715809ba4c78b1f87f40 "$TEST_SUITE"
check_sha256 6a48abe2f2ffd229f5f9e23469c2110121789a617177e6c880d135fb432ab9ef "$CONTROL_RECEIPT"
check_sha256 e240940d204c1bb3d22c31279459d554388d50a7a3b0a80761cba23433f40074 "$PREFLIGHT_RECEIPT"
[[ "$(stat -c %a "$PREFLIGHT_ROOT")" == 700 ]] || die "preflight root mode changed"
[[ "$(stat -c %a "$CONTROL_RECEIPT")" == 600 ]] || die "control receipt mode changed"
[[ "$(stat -c %a "$PREFLIGHT_RECEIPT")" == 600 ]] || die "preflight receipt mode changed"
[[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == 78bdb3092f1cb1cd9a95018aa0ff275ffa3f982a ]] \
    || die "candidate engine checkout changed"
[[ -z "$(git -C "$SERVER_REPO" status --porcelain)" ]] || die "candidate engine checkout is dirty"
(cd "$SERVER_REPO" && sha256sum --check "$ENGINE_MANIFEST")
"$SERVER_REASONING_TEST"
"$SERVER_DRY_TEST" "$SERVER_REPO/models/ggml-vocab-llama-spm.gguf"
ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-reasoning-prefill$'
ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-greedy-dry$'
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
    echo "PASS V26 preflight prompt=$PROMPT phase=$PHASE; production remains active"
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
dry_breakers='"*'
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
    --dry-multiplier 2.0 \
    --dry-base 1.75 \
    --dry-allowed-length 4 \
    --dry-penalty-last-n -1 \
    --dry-sequence-breaker "$dry_breakers" \
    --reasoning-prefill "$reasoning_prefill_text"
wait_candidate_ready

while systemctl is-active --quiet "$UNIT"; do
    sleep 5
done
"$GATE_HELPER" verify-phase --receipt "$RUN_DIR/phase.gate.json"
echo "COMPLETE unit=$UNIT prompt=$PROMPT phase=$PHASE; restoring accepted V1"
