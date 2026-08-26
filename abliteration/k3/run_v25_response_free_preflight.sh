#!/usr/bin/env bash
# Prove V25's exact control/feature templates without generating a model token.
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

SERVER=/home/chuck/ik_llama-v25-ecf7446e/build-v25/bin/llama-server
SERVER_REPO=/home/chuck/ik_llama-v25-ecf7446e
SERVER_BUILD=$SERVER_REPO/build-v25
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
TOOLS=/models/.abliteration/k3/eval-tools-v25-v1
PREFLIGHT_HELPER=$TOOLS/preflight_v25_reasoning_prefill.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
BASE_EVALUATOR=$TOOLS/evaluate_api.py
EVALUATOR=$TOOLS/evaluate_reasoning_prefill_api_v25.py
REVIEW_HELPER=$TOOLS/prepare_manual_review.py
STATE_HELPER=$TOOLS/verify_v25_calibration_state.py
STATE_CORE=$TOOLS/verify_v10_calibration_state.py
GATE_HELPER=$TOOLS/gate_v25_calibration.py
GATE_CORE=$TOOLS/gate_v10_calibration.py
REQUEST_PREFIX=$TOOLS/v10-calibration-request-prefix.json
CALIBRATION_LAUNCHER=$TOOLS/run_v25_calibration_server.sh
TEST_SUITE=$TOOLS/test_v25_calibration.py
PROTOCOL=$TOOLS/V25_PROTOCOL.md
PRIOR_RESULTS=$TOOLS/V24_RESULTS.md
SYSTEM_PROMPT=$TOOLS/v10-system-prompt-02-semantic-contract.txt
REASONING_PREFILL=$TOOLS/v25-reasoning-prefill.txt
ENGINE_MANIFEST=$TOOLS/v25-engine-sources.sha256
PARTITION=/models/.abliteration/k3/v10-calibration-partition-v1
PARTITION_MANIFEST=$PARTITION/manifest.json
FAILURES=$PARTITION/calibration.failures.jsonl
STABILITY=$PARTITION/calibration.stability.jsonl
REMAINDER=$PARTITION/calibration.remainder.jsonl
RECEIPTS=/models/.abliteration/k3/v25-engine-test-receipts-v1
RUN_ROOT=/models/.abliteration/k3/v25-response-free-preflight-v1
BEHAVIOR_ROOT=/models/.abliteration/k3/v25-calibration-run-v1
CONTROL_RECEIPT=$RUN_ROOT/control.json
PREFLIGHT_RECEIPT=$RUN_ROOT/preflight.json
PRODUCTION=glm-server.service
CONTROL_UNIT=kimi-k3-q5attn-abl-v25-v1-control-preflight.service
CANDIDATE_UNIT=kimi-k3-q5attn-abl-v25-v1-feature-preflight.service
CONTROL_ALIAS=kimi-k3-q5attn-abl-v25-v1-control-preflight
CANDIDATE_ALIAS=kimi-k3-q5attn-abl-v25-v1-no-newline-dry-ttf-preflight

production_stopped=0
candidate_started=0
current_unit=
inventory_tmp=

die() {
    echo "run_v25_response_free_preflight: $*" >&2
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
    inventory_tmp=$(mktemp /tmp/k3-v25-v2-inventory.XXXXXX)
    stat -c '%n\t%s\t%Y\t%Z\t%D\t%i\t%a' "${paths[@]}" > "$inventory_tmp"
    cmp "$V2_INVENTORY" "$inventory_tmp" >/dev/null || die "V2 shard inventory changed"
}

startup_has_diagnostic() {
    local unit=$1
    local pid=$2
    journalctl \
        --unit="$unit" \
        "_PID=$pid" \
        --no-pager \
        --output=cat \
        --grep='(^|[^[:alpha:]])(WARN|ERR|ERROR|warning|error)([^[:alpha:]]|$)' \
        --quiet
}

wait_candidate_ready() {
    local unit=$1
    local attempt
    local journal_match
    local main_pid
    for ((attempt = 0; attempt < 900; attempt++)); do
        if ! systemctl is-active --quiet "$unit"; then
            echo "candidate unit exited during load" >&2
            return 1
        fi
        main_pid=$(systemctl show "$unit" --property=MainPID --value)
        if [[ "$main_pid" =~ ^[1-9][0-9]*$ ]]; then
            journal_match=$(journalctl \
                --unit="$unit" \
                "_PID=$main_pid" \
                --no-pager \
                --output=cat \
                --grep='HTTP server listening' \
                --lines=1 \
                2>/dev/null || true)
            if [[ "$journal_match" == *'HTTP server listening'* ]]; then
                [[ "$(readlink -f "/proc/${main_pid}/exe")" == "$SERVER" ]] || return 1
                check_sha256 \
                    0da60971041065fd716c8a60f5db04e87f28dba4dc3b30b28db3367e29449b28 \
                    "/proc/${main_pid}/exe"
                if startup_has_diagnostic "$unit" "$main_pid"; then
                    journalctl --unit="$unit" "_PID=$main_pid" --no-pager --output=cat >&2
                    die "$unit emitted a startup warning or error"
                fi
                echo "READY unit=$unit pid=$main_pid"
                return 0
            fi
        fi
        sleep 1
    done
    echo "candidate did not announce HTTP readiness" >&2
    return 1
}

stop_candidate() {
    if [[ "$candidate_started" == 1 && -n "$current_unit" ]]; then
        sudo -n systemctl stop "$current_unit"
        wait_closed 8081
        candidate_started=0
        current_unit=
    fi
}

# Invoked by the signal/exit trap below.
# shellcheck disable=SC2329
restore_production() {
    local status=$?
    local attempt
    trap - EXIT INT TERM HUP
    set +e
    if [[ "$candidate_started" == 1 && -n "$current_unit" ]]; then
        sudo -n systemctl stop "$current_unit"
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

start_candidate() {
    local mode=$1
    local alias
    local dry_breakers
    local reasoning_prefill_text
    local -a feature_args=()
    if [[ "$mode" == control ]]; then
        current_unit=$CONTROL_UNIT
        alias=$CONTROL_ALIAS
    else
        current_unit=$CANDIDATE_UNIT
        alias=$CANDIDATE_ALIAS
        dry_breakers=':"*'
        IFS= read -r reasoning_prefill_text < "$REASONING_PREFILL"
        feature_args=(
            --dry-multiplier 2.0
            --dry-base 1.75
            --dry-allowed-length 4
            --dry-penalty-last-n -1
            --dry-sequence-breaker "$dry_breakers"
            --reasoning-prefill "$reasoning_prefill_text"
        )
    fi
    candidate_started=1
    sudo -n systemd-run \
        --unit="$current_unit" \
        --collect \
        --property=Type=exec \
        --property=User=chuck \
        --property=Group=chuck \
        --property=LimitMEMLOCK=infinity \
        --property=TimeoutStopSec=60 \
        --property=WorkingDirectory="$SERVER_REPO" \
        -- "$SERVER" \
        --model "$MODEL" \
        --alias "$alias" \
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
        "${feature_args[@]}"
    wait_candidate_ready "$current_unit"
}

verify_files() {
    local -a expected_tools=(
        V24_RESULTS.md
        V25_PROTOCOL.md
        capture_server_provenance.py
        evaluate_api.py
        evaluate_reasoning_prefill_api_v25.py
        gate_v10_calibration.py
        gate_v25_calibration.py
        preflight_v25_reasoning_prefill.py
        prepare_manual_review.py
        run_v25_calibration_server.sh
        run_v25_response_free_preflight.sh
        test_v25_calibration.py
        v10-calibration-request-prefix.json
        v10-system-prompt-02-semantic-contract.txt
        v25-engine-sources.sha256
        v25-reasoning-prefill.txt
        verify_v10_calibration_state.py
        verify_v25_calibration_state.py
    )
    local -a observed_tools
    mapfile -t observed_tools < <(
        find "$TOOLS" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort
    )
    [[ "${observed_tools[*]}" == "${expected_tools[*]}" ]] \
        || die "V25 response-free tool-tree membership changed"
    check_sha256 a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6 "$PRODUCTION_SERVER"
    check_sha256 0da60971041065fd716c8a60f5db04e87f28dba4dc3b30b28db3367e29449b28 "$SERVER"
    check_sha256 3eff5f49a244e829da599ee4bd2892de8bd32dda64fb6b9281b554d52d307c00 "$SERVER_BUILD/examples/mtmd/libmtmd.so"
    check_sha256 df570684d977932616a6e5bb576ddd9ec0462c99e1025816632d832f059bbec7 "$SERVER_BUILD/src/libllama.so"
    check_sha256 a70146840462a3714e32fd3de8df78b2c8925583190e00e726bb04a6c7881466 "$SERVER_BUILD/ggml/src/libggml.so"
    check_sha256 9b24b25ca9b9fae18934c889da401847b5a826b885ce7d82f2b3ee615054aa85 "$SERVER_REASONING_TEST"
    check_sha256 49a633350ff6da0de27d7749be8298416b198b0c86198bf4a6304053bf10fe72 "$SERVER_DRY_TEST"
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
    check_sha256 44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9 "$SYSTEM_PROMPT"
    check_sha256 e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c "$REASONING_PREFILL"
    check_sha256 d0d55fe2ce79a580bd5812da14c9ca5bda40cbd2129c3115c1fb6db3f77a7062 "$PROTOCOL"
    check_sha256 47e22bdc0b0b85e36f994d57acb8fa6caba4953e8b085f5009e8f4a1f7462d07 "$PRIOR_RESULTS"
    check_sha256 6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22 "$PROVENANCE_HELPER"
    check_sha256 1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a "$BASE_EVALUATOR"
    check_sha256 7d29ab87317472028364f8586a8496e11312e3610df8ad621c00934b3230db3c "$EVALUATOR"
    check_sha256 6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54 "$REVIEW_HELPER"
    check_sha256 c58dedeae74e97e4b77209377a86e245fc476ddc5bcbc349c7d237ac431c3925 "$STATE_HELPER"
    check_sha256 291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae "$STATE_CORE"
    check_sha256 c494e557361a343ad43dbb7df518369c4584381c3c11ba4a17b76ec5fd90c5dd "$GATE_HELPER"
    check_sha256 5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59 "$GATE_CORE"
    check_sha256 5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220 "$REQUEST_PREFIX"
    check_sha256 0d38adeea642e1f23d74f490a97681577b9322345066436b6ae616d778a57cf2 "$CALIBRATION_LAUNCHER"
    check_sha256 8a8f9abc634f7f4027e204d9200740f77d1149fbf7521937584ade2a3038bd11 "$TEST_SUITE"
    check_sha256 2c76803ff318cd472b72896c43a5efdb4f9696f91d6a3c3b0432842e79350631 "$PREFLIGHT_HELPER"
    check_sha256 69997801099183606e4f24e1597bdf02f550acb4c655691cd89e078f87cacac5 "$ENGINE_MANIFEST"
    check_sha256 da323ac2826309ba37f07829f4fe6f2c78175dfff9f32227e842bbb5244e9bbf "$PARTITION_MANIFEST"
    check_sha256 204dd0a5e95314f83a2869420c6c6bd74d55d97aca66a85bcbcb8d3a73e369a8 "$FAILURES"
    check_sha256 55f56229fa3730ff17bd2abe91f5b848fe33ab5f6f8ef6d4aa4c997dcff28b79 "$STABILITY"
    check_sha256 cbea682929ecc470d02a84d1ffc4166a3780d464204e93fa85206b3ea7c0f96a "$REMAINDER"
    [[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == ecf7446e02e5a473c8f8316d201836532b707b21 ]] \
        || die "candidate engine checkout changed"
    [[ -z "$(git -C "$SERVER_REPO" status --porcelain)" ]] \
        || die "candidate engine checkout is dirty"
    (cd "$SERVER_REPO" && sha256sum --check "$ENGINE_MANIFEST")
    "$SERVER_REASONING_TEST"
    "$SERVER_DRY_TEST" "$SERVER_REPO/models/ggml-vocab-llama-spm.gguf"
    ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-reasoning-prefill$'
    ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-greedy-dry$'
    [[ "$(stat -c %a "$RECEIPTS")" == 700 ]] || die "receipt directory mode changed"
    local -a expected_receipts=(
        v25-local-asan-ubsan-greedy-dry.xml
        v25-local-asan-ubsan-reasoning-prefill.xml
        v25-local-normal-greedy-dry.xml
        v25-local-normal-reasoning-prefill.xml
        v25-remote-normal-greedy-dry.xml
        v25-remote-normal-reasoning-prefill.xml
    )
    local -a observed_receipts
    mapfile -t observed_receipts < <(
        find "$RECEIPTS" -maxdepth 1 -type f -printf '%f\n' | LC_ALL=C sort
    )
    [[ "${observed_receipts[*]}" == "${expected_receipts[*]}" ]] \
        || die "receipt directory membership changed"
    check_sha256 132201dd79a9e06959d52dfd0cb69aa99ddcb8ef6747fae7b67ce638e2e1a32f "$RECEIPTS/v25-local-asan-ubsan-greedy-dry.xml"
    check_sha256 0343dc4bcb6e5e435a685cf94276ee87d390118d8db3ee34f7a1f6626ed3054c "$RECEIPTS/v25-local-asan-ubsan-reasoning-prefill.xml"
    check_sha256 37472f96058498ee42fd29d2fba7ce3ad8b61c26af200d53cf1cdb81a4c45cd1 "$RECEIPTS/v25-local-normal-greedy-dry.xml"
    check_sha256 388d86c55dd6f7f09b7dbcf6d0a0b49ce6791f9a438b2d4d822e4ee170fd4c26 "$RECEIPTS/v25-local-normal-reasoning-prefill.xml"
    check_sha256 b68f3829765b24bd5cfb26531a7b15e1b22f7af2f74cd952229e2594fe497f4c "$RECEIPTS/v25-remote-normal-greedy-dry.xml"
    check_sha256 12c452c51aed11c5379195bfa61c7786e54bbce94f9e4c689aa4eda8de12d15f "$RECEIPTS/v25-remote-normal-reasoning-prefill.xml"
    local receipt
    for receipt in "${observed_receipts[@]}"; do
        [[ "$(stat -c %a "$RECEIPTS/$receipt")" == 600 ]] \
            || die "receipt mode changed: $receipt"
    done
    [[ "$(<"$COMPLETE")" == 845361056864 ]] || die "V2 byte count changed"
    [[ -s "$MODEL" ]] || die "missing V2 shard 1"
    [[ -r /home/chuck/.glm-api-key ]] || die "API key file is unreadable"
    verify_v2_inventory
    production_ready || die "accepted production is not exact and healthy"
    port_closed 8081 || die "response-free port 8081 is already open"
    ! systemctl is-active --quiet "$CONTROL_UNIT" || die "control unit is already active"
    ! systemctl is-active --quiet "$CANDIDATE_UNIT" || die "feature unit is already active"
    [[ ! -e "$RUN_ROOT" ]] || die "refusing to reuse response-free run root: $RUN_ROOT"
    [[ ! -e "$BEHAVIOR_ROOT" ]] || die "V25 behavior root already exists"
    local phase
    for phase in failures stability remainder; do
        ! systemctl is-active --quiet \
            "kimi-k3-q5attn-abl-v10-prompt25-${phase}-cal.service" \
            || die "V25 behavior unit is already active: $phase"
    done
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

if [[ $# -eq 1 && "$1" == --verify-files-only ]]; then
    trap restore_production EXIT INT TERM HUP
    verify_files
    echo "PASS V25 response-free file/test verification; production remains active"
    exit 0
fi

if [[ $# -ne 2 || "$1" != --run || "$2" != --no-response ]]; then
    die "usage: $0 --run --no-response | --verify-files-only | --check-production-only"
fi

trap restore_production EXIT INT TERM HUP
verify_files
mkdir "$RUN_ROOT"
chmod 700 "$RUN_ROOT"

production_stopped=1
sudo -n systemctl stop "$PRODUCTION"
wait_closed 8080

start_candidate control
"$PREFLIGHT_HELPER" control \
    --system-prompt-file "$SYSTEM_PROMPT" \
    --reasoning-prefill-file "$REASONING_PREFILL" \
    --output "$CONTROL_RECEIPT"
stop_candidate

start_candidate candidate
"$PREFLIGHT_HELPER" candidate \
    --system-prompt-file "$SYSTEM_PROMPT" \
    --reasoning-prefill-file "$REASONING_PREFILL" \
    --control-receipt "$CONTROL_RECEIPT" \
    --output "$PREFLIGHT_RECEIPT"
stop_candidate

echo "COMPLETE V25 response-free preflight; restoring accepted V1"
