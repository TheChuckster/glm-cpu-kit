#!/usr/bin/env bash
# Prove V18's exact control/feature templates without generating a model token.
set -euo pipefail
umask 077

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
TOOLS=/models/.abliteration/k3/eval-tools-v18-v1
PREFLIGHT_HELPER=$TOOLS/preflight_v18_reasoning_prefill.py
PROVENANCE_HELPER=$TOOLS/capture_server_provenance.py
BASE_EVALUATOR=$TOOLS/evaluate_api.py
PROTOCOL=$TOOLS/V18_PROTOCOL.md
PRIOR_RESULTS=$TOOLS/V17_RESULTS.md
SYSTEM_PROMPT=$TOOLS/v10-system-prompt-02-semantic-contract.txt
REASONING_PREFILL=$TOOLS/v18-reasoning-prefill.txt
ENGINE_MANIFEST=$TOOLS/v18-engine-sources.sha256
RUN_ROOT=/models/.abliteration/k3/v18-response-free-preflight-v1
CONTROL_RECEIPT=$RUN_ROOT/control.json
PREFLIGHT_RECEIPT=$RUN_ROOT/preflight.json
PRODUCTION=glm-server.service
CONTROL_UNIT=kimi-k3-q5attn-abl-v18-control-preflight.service
CANDIDATE_UNIT=kimi-k3-q5attn-abl-v18-feature-preflight.service
CONTROL_ALIAS=kimi-k3-q5attn-abl-v18-control-preflight
CANDIDATE_ALIAS=kimi-k3-q5attn-abl-v18-ttf-preflight

production_stopped=0
candidate_started=0
current_unit=
inventory_tmp=

die() {
    echo "run_v18_response_free_preflight: $*" >&2
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
    inventory_tmp=$(mktemp /tmp/k3-v18-v2-inventory.XXXXXX)
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
                    b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6 \
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
    local reasoning_prefill_text
    local -a prefill_args=()
    if [[ "$mode" == control ]]; then
        current_unit=$CONTROL_UNIT
        alias=$CONTROL_ALIAS
    else
        current_unit=$CANDIDATE_UNIT
        alias=$CANDIDATE_ALIAS
        IFS= read -r reasoning_prefill_text < "$REASONING_PREFILL"
        prefill_args=(--reasoning-prefill "$reasoning_prefill_text")
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
        "${prefill_args[@]}"
    wait_candidate_ready "$current_unit"
}

verify_files() {
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
    check_sha256 44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9 "$SYSTEM_PROMPT"
    check_sha256 f9ec3a2be33028a47e4189b336bf4660dfe564f58e80427edc8e63c696cbcc10 "$REASONING_PREFILL"
    check_sha256 2b352f39f85eb0fc8405ab1c899dc78c34b0a80c00ab79454b3ca0b8e83110c8 "$PROTOCOL"
    check_sha256 147a851ba60f1a4fc5cdae9fec20c815e6ad29e6ba60bf4700088e968d0a962e "$PRIOR_RESULTS"
    check_sha256 6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22 "$PROVENANCE_HELPER"
    check_sha256 1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a "$BASE_EVALUATOR"
    check_sha256 2a0f03d7c6483ee1b75db2f0a705e47d0e52a67862c9159f564faababb71366c "$PREFLIGHT_HELPER"
    check_sha256 0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13 "$ENGINE_MANIFEST"
    [[ "$(git -C "$SERVER_REPO" rev-parse HEAD)" == 98de9a7f69ef3d387b676ad4a3ee14946ac88f94 ]] \
        || die "candidate engine checkout changed"
    [[ -z "$(git -C "$SERVER_REPO" status --porcelain)" ]] \
        || die "candidate engine checkout is dirty"
    (cd "$SERVER_REPO" && sha256sum --check "$ENGINE_MANIFEST")
    "$SERVER_TEST"
    ctest --test-dir "$SERVER_BUILD" --output-on-failure -R '^test-reasoning-prefill$'
    [[ "$(<"$COMPLETE")" == 845361056864 ]] || die "V2 byte count changed"
    [[ -s "$MODEL" ]] || die "missing V2 shard 1"
    [[ -r /home/chuck/.glm-api-key ]] || die "API key file is unreadable"
    verify_v2_inventory
    production_ready || die "accepted production is not exact and healthy"
    port_closed 8081 || die "response-free port 8081 is already open"
    ! systemctl is-active --quiet "$CONTROL_UNIT" || die "control unit is already active"
    ! systemctl is-active --quiet "$CANDIDATE_UNIT" || die "feature unit is already active"
    [[ ! -e "$RUN_ROOT" ]] || die "refusing to reuse response-free run root: $RUN_ROOT"
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
    echo "PASS V18 response-free file/test verification; production remains active"
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

echo "COMPLETE V18 response-free preflight; restoring accepted V1"
