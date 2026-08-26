#!/usr/bin/env bash
# Run one fixed V23 high-multiplier-DRY phase PID and restore accepted V1 production.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

SERVER=/home/chuck/ik_llama-v21-23695c7a/build-v21/bin/llama-server
SERVER_REPO=/home/chuck/ik_llama-v21-23695c7a
SERVER_BUILD=$SERVER_REPO/build-v21
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
TOOLS=/models/.abliteration/k3/eval-tools-v23-v3
STATE_HELPER=$TOOLS/verify_v23_calibration_state.py
STATE_CORE=$TOOLS/verify_v10_calibration_state.py
EVALUATOR=$TOOLS/evaluate_reasoning_prefill_api_v23.py
BASE_EVALUATOR=$TOOLS/evaluate_api.py
REVIEW_HELPER=$TOOLS/prepare_manual_review.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
REQUEST_PREFIX=$TOOLS/v10-calibration-request-prefix.json
GATE_HELPER=$TOOLS/gate_v23_calibration.py
GATE_CORE=$TOOLS/gate_v10_calibration.py
PROTOCOL=$TOOLS/V23_PROTOCOL.md
PRIOR_RESULTS=$TOOLS/V22_RESULTS.md
ATTEMPT1=$TOOLS/V23_RESPONSE_FREE_ATTEMPT1.md
REASONING_PREFILL=$TOOLS/v23-reasoning-prefill.txt
ENGINE_MANIFEST=$TOOLS/v23-engine-sources.sha256
PREFLIGHT_HELPER=$TOOLS/preflight_v23_reasoning_prefill.py
RESPONSE_FREE_LAUNCHER=$TOOLS/run_v23_response_free_preflight.sh
PREFLIGHT_ROOT=/models/.abliteration/k3/v23-response-free-preflight-v2
CONTROL_RECEIPT=$PREFLIGHT_ROOT/control.json
PREFLIGHT_RECEIPT=$PREFLIGHT_ROOT/preflight.json
RUN_ROOT=/models/.abliteration/k3/v23-calibration-run-v1
PRODUCTION=glm-server.service

production_stopped=0
candidate_started=0
inventory_tmp=

die() {
    echo "run_v23_calibration_server: $*" >&2
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
    inventory_tmp=$(mktemp /tmp/k3-v23-v2-inventory.XXXXXX)
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
                    13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a \
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
    die "usage: $0 [--preflight-only prompt23 PHASE --no-response] | prompt23 PHASE | --check-production-only"
fi

case "$PROMPT" in
    prompt23)
        ALIAS=kimi-k3-q5attn-abl-v23-dry-ttf-cal
        PROMPT_FILE=$TOOLS/v10-system-prompt-02-semantic-contract.txt
        PROMPT_SHA=44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9
        ;;
    *) die "prompt must be prompt23" ;;
esac
case "$PHASE" in
    failures) DATASET=$FAILURES ;;
    stability) DATASET=$STABILITY ;;
    remainder) DATASET=$REMAINDER ;;
    *) die "phase must be failures, stability, or remainder" ;;
esac
# The immutable V10 gate core derives this compatibility tag for prompt23.
UNIT=kimi-k3-q5attn-abl-v10-${PROMPT}-${PHASE}-cal.service
RUN_DIR=$RUN_ROOT/$PROMPT/$PHASE

trap restore_production EXIT INT TERM HUP

# Every check in this block completes before production is stopped.
check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$PRODUCTION_SERVER"
check_sha256 13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a "$SERVER"
check_sha256 e9188e9135081f881b95eac4f0b68dbd2980de3766c16a53354f9d74b51e3c45 "$SERVER_BUILD/examples/mtmd/libmtmd.so"
check_sha256 6863d473918fb8d2ef57324f54237582aaa3a1158e2624f0bb25d50b5a8c1c47 "$SERVER_BUILD/src/libllama.so"
check_sha256 c02fd1ed092729b89a8c9b9bcf0b50e8f468833e564552e4e9c5a3069af2680d "$SERVER_BUILD/ggml/src/libggml.so"
check_sha256 d66adaffc6444c3743e787d2393cc0615e8f26c821f8df9cefae361052adb69f "$SERVER_REASONING_TEST"
check_sha256 f4045bfdee67f221e47c067307acb7bb8e59cda11c16ac0009183cb189acf427 "$SERVER_DRY_TEST"
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
check_sha256 8bb008b979a746837ce516f194288cdb3d6079f3850737545262d0b9fa35d445 "$EVALUATOR"
check_sha256 1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a "$BASE_EVALUATOR"
check_sha256 6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54 "$REVIEW_HELPER"
check_sha256 e3143df627c7135832211ab2586dc00b07678f772da4cc41209206ffd9ac9598 "$STATE_HELPER"
check_sha256 291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae "$STATE_CORE"
check_sha256 6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22 "$PROVENANCE_HELPER"
check_sha256 5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220 "$REQUEST_PREFIX"
check_sha256 d2018227d64478bff279000cc99e1480d24dcc2d9645afc8c882f5248049386e "$GATE_HELPER"
check_sha256 5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59 "$GATE_CORE"
check_sha256 0699d2f7f5f6dd04c61b0eb1dbdca8063575a36440ae8d72aaeb65ed647425bd "$PROTOCOL"
check_sha256 15cb6a5dc2ce42bc5d068b52616029c077fc6e78bac704b8f5e58cfef5dae9d7 "$PRIOR_RESULTS"
check_sha256 4157862fb35c56d7e76c7aa58e811a9d3da2710e72bd0ff58e6ebb718b073983 "$ATTEMPT1"
check_sha256 04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693 "$ENGINE_MANIFEST"
check_sha256 57ce6c1caa9f176c7183dfe0fdbb01a56bae265602073513748000f6a91fc54c "$PREFLIGHT_HELPER"
check_sha256 dd6f156eaf55bca0777b0c4cf7a855635ed8d9857d44d2010589b76b8ee8fbd9 "$RESPONSE_FREE_LAUNCHER"
check_sha256 808e6c02755922f37e902e2d97bc619828d87f89b1a4f8c95c0713ee284aa006 "$CONTROL_RECEIPT"
check_sha256 402b3830c6412224ba4d5affd995e2c765fe119625cdd855e6f01cf15122a834 "$PREFLIGHT_RECEIPT"
[[ "$(stat -c %a "$PREFLIGHT_ROOT")" == 700 ]] || die "preflight root mode changed"
[[ "$(stat -c %a "$CONTROL_RECEIPT")" == 600 ]] || die "control receipt mode changed"
[[ "$(stat -c %a "$PREFLIGHT_RECEIPT")" == 600 ]] || die "preflight receipt mode changed"
[[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == 23695c7a444dcfaaf892bebfefb4a4a8394e3c37 ]] \
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
    echo "PASS V23 preflight prompt=$PROMPT phase=$PHASE; production remains active"
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
    --dry-multiplier 2.0 \
    --dry-base 1.75 \
    --dry-allowed-length 4 \
    --dry-penalty-last-n -1 \
    --reasoning-prefill "$reasoning_prefill_text"
wait_candidate_ready

while systemctl is-active --quiet "$UNIT"; do
    sleep 5
done
"$GATE_HELPER" verify-phase --receipt "$RUN_DIR/phase.gate.json"
echo "COMPLETE unit=$UNIT prompt=$PROMPT phase=$PHASE; restoring accepted V1"
