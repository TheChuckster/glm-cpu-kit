# GLM-5.2 CPU Inference Runbook

A step-by-step guide to running GLM-5.2 (753B MoE) inference on CPU on a dual-socket EPYC box,
written for someone starting from scratch. It's based on what we worked out getting this running
on our first machine, a single-socket EPYC 9575F.

Target machine for this guide: Google Cloud, 2x EPYC 9B45 (96 cores per socket, 192 cores across
2 sockets), 768 MiB L3, 1.4 TiB RAM, running Ubuntu 24.04 LTS.

> The biggest difference from a single-socket box is NUMA (two memory domains). Read section 4
> carefully; that's where a dual-socket build succeeds or fails.

---

## 0. The mental model

LLM inference on CPU has two phases, each with a different bottleneck:

| Phase | What it is | Bottleneck | How to make it fast |
|---|---|---|---|
| PP (prompt processing) | Reading your prompt / context | Compute (matmuls) | ik_llama.cpp fused-MoE + AVX-512/VNNI + many cores |
| TG (token generation) | Producing the answer, one token at a time | Memory bandwidth | more/faster RAM channels; nothing else |

The equation for generation speed:

```
TG (tokens/sec)  ≈  usable_memory_bandwidth  /  (active_params × bytes_per_param)
```

What follows from this:
- MoE models suit CPU well. GLM-5.2 is 753B total but only ~40B active per token, so TG depends
  on 40B, not 753B. A dense 70B model would generate slower than this 753B MoE.
- TG can't be made GPU-fast on CPU. More cores, higher clocks, and VNNI don't help TG; only
  memory bandwidth does. Expect tens of tok/s, not hundreds.
- PP can be made fast (that's where the engine and cores matter), but big agentic prompts
  (50k-150k tokens) still take minutes because the prompt is large. Keep working context lean.
- RAM capacity is what makes this possible. 1.4 TiB lets you hold a frontier model that would
  otherwise need a $100k+ GPU rig. You trade datacenter speed for capacity and $0 per token.

Rough expectation for this dual-socket box (24 DDR5 channels, ~600-700 GB/s effective with good
NUMA): TG around 18-25 tok/s, PP a few hundred tok/s at short context, roughly 2x a single socket.

---

## 1. Provision the machine (GCP)

1. Create the instance (2x 9B45, 1.4 TiB). Ubuntu 24.04 LTS boot disk (at least 50 GB).
2. Attach fast storage for the model. The Q4 model is ~440 GB; you want NVMe, not a slow disk.
   - Preferred: Local SSD (NVMe), attach several and RAID0 them, or a large Hyperdisk Extreme.
   - You need at least 500 GB of fast storage (1 TB if you'll also keep a Q8 copy).
3. SSH in. Everything below runs as a sudo-capable user.

```bash
sudo apt-get update
sudo apt-get -y install build-essential cmake git python3 python3-pip \
    numactl unzip curl jq linux-tools-common linux-tools-$(uname -r) htop
```

### Set up the model storage (example: 4x Local SSD, RAID0, xfs at /models)
```bash
# find the local NVMe devices (adjust names to your machine)
lsblk -d -o NAME,SIZE,MODEL | grep -i nvme
# create a RAID0 across them (no redundancy, which is fine since the model is re-downloadable)
sudo mdadm --create /dev/md0 --level=0 --raid-devices=4 /dev/nvme0n1 /dev/nvme1n1 /dev/nvme2n1 /dev/nvme3n1
sudo mkfs.xfs /dev/md0
sudo mkdir -p /models
sudo mount /dev/md0 /models
sudo chown "$USER":"$USER" /models
# persist across reboots
echo "/dev/md0 /models xfs defaults,nofail 0 0" | sudo tee -a /etc/fstab
```

---

## 2. Confirm the CPU exposes what we need

```bash
lscpu | grep -iE 'model name|socket|core|numa'
grep -oE 'avx512_vnni|avx512_bf16|avx_vnni' /proc/cpuinfo | sort -u   # want vnni + bf16
```
You should see 2 sockets, 192 cores, and `avx512_vnni` + `avx512_bf16`. These are what make the
int8/bf16 matmuls fast (see section 5).

---

## 4. NUMA: the dual-socket make-or-break

A dual-socket box has two memory domains (one per CPU). Each socket's cores access their own RAM
fast and the other socket's RAM slowly (over the inter-socket link). The 440 GB model has to be
spread across both, or half your cores will be starved reading across the link.

```bash
numactl --hardware        # shows the nodes, their RAM, and inter-node "distances"
```
You'll typically see 2 nodes (or 4 if the BIOS/hypervisor set NPS2). On a cloud VM you usually
can't change NPS (no BIOS access), so you work with what's exposed.

The rule: interleave the model's memory across all nodes so both sockets' memory controllers
contribute bandwidth. Two ways, benchmark both (section 8):

- A) Let the engine distribute (preferred starting point): the ik_llama.cpp flag `--numa distribute`
  spreads threads and memory across nodes.
- B) Force interleave with numactl: launch under `numactl --interleave=all …`, which round-robins
  every memory page across nodes.

Do not let it load onto one node with threads on both; that's the worst case. And avoid
`--no-mmap` (see section 9). Also enable transparent hugepages (usually on by default):
```bash
cat /sys/kernel/mm/transparent_hugepage/enabled   # want [always] or madvise
```

> A nuance: because TG is bandwidth-bound, interleaving across both sockets (more aggregate
> bandwidth) usually beats pinning to one socket, even with the cross-socket penalty. But
> benchmark it; on some topologies single-socket-pinned (`numactl --cpunodebind=0 --membind=0`,
> 96 threads) wins for pure TG. Test both and keep the winner.

---

## 5. Build the inference engine: ik_llama.cpp (not mainline)

This was the single biggest win. For MoE models, ik_llama.cpp (ikawrakow's fork) has fused-MoE
kernels that gave us about 7x the prompt-processing throughput of mainline `llama.cpp`
(roughly 16 to 110-129 tok/s on our single socket). It's the main CPU-MoE engine. Use it.

```bash
cd ~
git clone --depth 1 https://github.com/ikawrakow/ik_llama.cpp
cd ik_llama.cpp
# GGML_NATIVE=ON => -march=native => compiler emits AVX-512/VNNI/BF16 for this CPU.
cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DLLAMA_CURL=OFF
cmake --build build --config Release -j "$(nproc)"
```
Verify VNNI actually got compiled in (should be non-zero):
```bash
objdump -d build/bin/libggml-cpu.so | grep -c vpdpbusd   # AVX-512 VNNI int8 dot-product
```
> `GGML_AVX512_VNNI=OFF` in CMakeCache is a red herring: `GGML_NATIVE=ON` already enables it via
> the compiler. The `objdump` count is the source of truth.

Fused MoE is on by default (`--no-fmoe` disables it). We tested `--run-time-repack` (RTR): only
about 2% gain and it forces `--no-mmap`, so skip RTR. We tested MTP speculative decoding and it
made TG 2x slower on MoE (verifying drafts pulls in many experts' weights), so don't use
spec-decode on MoE.

---

## 6. Download the model: GLM-5.2 Q4_K_XL (unsloth dynamic quant)

Use Q4_K_XL (unsloth's dynamic 4-bit), not Q8. Q4 is near-lossless per Unsloth's tests, ~440 GB
(vs ~800 GB for Q8), gives about 2x the PP, and leaves plenty of RAM headroom. Q8's marginal
quality isn't worth double the bandwidth per token.

New cloud boxes often have flaky or absent IPv6 that stalls HF downloads. Force IPv4 and
parallelize:
```bash
mkdir -p /models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL && cd /models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL
BASE="https://huggingface.co/unsloth/GLM-5.2-GGUF/resolve/main/UD-Q4_K_XL"
# 11 shards; shard 1 is tiny (~9 MB metadata), which is expected, not truncation.
seq -w 1 11 | xargs -P4 -I{} curl -4 -sfL -C - --retry 1000 --retry-all-errors \
  -o "GLM-5.2-UD-Q4_K_XL-000{}-of-00011.gguf" \
  "$BASE/GLM-5.2-UD-Q4_K_XL-000{}-of-00011.gguf"
```
Verify the total against HuggingFace before trusting it (~467 GB listed / ~440 GB on disk):
```bash
curl -4 -s "https://huggingface.co/api/models/unsloth/GLM-5.2-GGUF/tree/main/UD-Q4_K_XL" \
  | jq -r '.[] | select(.path|endswith(".gguf")) | "\(.lfs.size)\t\(.path)"'
```

---

## 7. The server launch script

Create `~/serve-glm.sh`. The key flags and the reasoning behind each:

```bash
#!/bin/bash
set -e
MODEL=$(ls /models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL/GLM-5.2-UD-Q4_K_XL-00001-of-*.gguf | head -1)
[ -n "$MODEL" ] || { echo "model not found"; exit 1; }

# DUAL-SOCKET: interleave across NUMA nodes. Try --numa distribute first; if a benchmark
# shows numactl interleave is better, wrap the exec line with: numactl --interleave=all
exec ~/ik_llama.cpp/build/bin/llama-server \
    --model "$MODEL" \
    --alias glm-5.2 \                         # lowercase; must match how the client requests it
    --host 0.0.0.0 --port 8080 \
    --numa distribute \                        # the dual-socket lever
    --ctx-size 1048576 \                       # GLM-5.2 trains to 1M; DSA keeps KV tiny (~48 GB @ 1M)
    --defrag-thold 0.1 \
    --parallel 1 \                             # single user: one slot = full context, best latency
    --threads 192 --threads-batch 192 \        # = PHYSICAL cores. never use SMT threads (see 11)
    --batch-size 2048 --ubatch-size 2048 \
    -fa on \                                   # flash attention
    --cache-type-k q8_0 --cache-type-v q8_0 \  # quantized KV to save memory at long context
    --mlock \                                  # pin weights in RAM (no page-fault jitter)
    --jinja \                                  # use the model's embedded chat template
    --chat-template-kwargs '{"enable_thinking": false}' \  # required; see 10
    --repeat-penalty 1.1 --repeat-last-n 256 \ # stops repetition loops; see 10
    --metrics \                                # Prometheus /metrics endpoint
    --api-key-file ~/.glm-api-key
```
```bash
# one-time
head -c 24 /dev/urandom | base64 | tr -d '/+=' > ~/.glm-api-key
chmod +x ~/serve-glm.sh
```

> Don't reserve cores unless you specifically want telemetry to stay responsive during grinds
> (fewer inference threads means /metrics answers under load, at a small perf cost). By default,
> use all cores. `--slots` is not a valid flag in ik (the `/slots` endpoint is on by default).
> Verify every flag with `--help` before adding it; a bad flag makes the server dump help and
> crash-loop.

---

## 8. Run it as a service and benchmark

```bash
sudo tee /etc/systemd/system/glm-server.service >/dev/null <<EOF
[Unit]
Description=GLM-5.2 ik_llama.cpp server
After=network-online.target
[Service]
Type=simple
User=$USER
ExecStart=$HOME/serve-glm.sh
Restart=on-failure
LimitMEMLOCK=infinity
TimeoutStartSec=0
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now glm-server
# wait for it: health returns 200 when the model is loaded and mlock'd (a few minutes)
watch -n5 'curl -s -o /dev/null -w "%{http_code}\n" localhost:8080/health -H "Authorization: Bearer $(cat ~/.glm-api-key)"'
```

Benchmark PP/TG and the NUMA choice (this decides section 4):
```bash
KEY=$(cat ~/.glm-api-key)
# raw engine bench (stop the service first to free RAM), try each NUMA strategy:
sudo systemctl stop glm-server
M=/models/GLM-5.2-Q4_K_XL/UD-Q4_K_XL/GLM-5.2-UD-Q4_K_XL-00001-of-00011.gguf
~/ik_llama.cpp/build/bin/llama-bench -m "$M" -p 2048,4096 -n 128 -t 192 -fa 1 --numa distribute
numactl --interleave=all ~/ik_llama.cpp/build/bin/llama-bench -m "$M" -p 2048,4096 -n 128 -t 192 -fa 1
numactl --cpunodebind=0 --membind=0 ~/ik_llama.cpp/build/bin/llama-bench -m "$M" -p 2048 -n 128 -t 96 -fa 1
# keep whichever gives the best TG; put it in serve-glm.sh; restart the service.
```
Also sweep threads for TG. Bandwidth saturates before you use all cores, so fewer threads
sometimes gives higher TG: `llama-bench ... -t 96,128,160,192`. Keep the peak.

---

## 9. Common pitfalls we hit (save yourself the pain)

- `--no-mmap` plus `--mlock` causes OOM. `--no-mmap` makes an anonymous copy and the page cache
  holds a copy too, so double memory. Use mmap plus mlock (that is, do not pass `--no-mmap`).
- Model name casing. The server `--alias`, the client's requested model, and any proxy config
  must all use the same string (`glm-5.2`), or a client may "not recognize the model" and
  silently fall back.
- Small shard is normal. Shard `00001` being ~9 MB (not ~48 GB) is expected; it holds split
  metadata.
- Frontier models trend toward more total and fewer active params, which favors this box (more
  RAM, and fewer active means faster TG). Kimi K2 (32B active) already generates faster than GLM
  here; a small-active coder (Qwen3-Coder-Next, 3B active) runs about 5-10x faster if you want
  speed over max quality.

---

## 10. Making it usable from a coding harness

Pick the right harness. GLM-5.2 speaks OpenAI-compatible. Prefer a harness that talks OpenAI
directly to the server:

- opencode: point it straight at `http://<server>:8080/v1`, model `glm-5.2`. No translation layer.
- Claude Code speaks Anthropic `/v1/messages`, so it needs a translator (litellm). We hit real
  bugs there: litellm routing to the OpenAI Responses API (`ResponseCompletedEvent` gives a broken
  stream), `count_tokens` 404s, and worst of all, multiple stale litellm instances on one port
  serving different configs. If you must use Claude Code plus litellm, run exactly one litellm
  instance, and know the translation layer is fragile.

Server-side settings that prevent harness breakage:
- `enable_thinking: false` (in the launch script). GLM emits reasoning blocks that break agentic
  harnesses (empty-thinking-block 400s in Claude Code plan mode; malformed streams). Turn it off.
- `--repeat-penalty 1.1` (plus a client-side `frequency_penalty ~0.4`). Quantized models with
  greedy or near-greedy sampling (what agents and subagents use for tool-calling) fall into
  infinite repetition loops. This is the fix.
- Long, generous client timeouts (30-60 min). A big agentic prompt legitimately takes many
  minutes of silent PP; short timeouts turn "slow" into "error."

Context discipline matters. Agentic harnesses accumulate context (100k+ tokens), and PP time
scales with prompt size. Symptoms of too much context: silent multi-minute stalls, then timeouts.
Keep working context lean, `/clear` between tasks, and use subagents to partition work into small
focused contexts. DSA sparse attention means big context won't crash (KV stays tiny), but it will
be slow; that's a usability choice, not a bug.

Claude Code specifics if you go that route: launch with `--permission-mode acceptEdits` (not
`auto`, which fires an extra safety-classifier model call per action, multiplying the load), and
set `MAX_THINKING_TOKENS=0` plus `ANTHROPIC_MODEL=glm-5.2`.

---

## 11. SMT / hyperthreading: don't

We tested it conceptually and it's a known regression for LLM inference:
- TG: SMT adds threads, not bandwidth, so it's pure contention. No gain.
- PP: the two SMT siblings share one core's AVX-512 vector units, which the single thread already
  saturates, so they fight for the same pipes. No gain, often a small loss.

Use `--threads = physical cores`. Tune downward for TG if a sweep shows it (bandwidth saturation),
never upward into SMT.

---

## 12. Observability (recommended)

We built a full stack; replicate the parts that matter (all separate from inference):

- node_exporter for CPU/mem/load/disk/net/temps, into Prometheus.
- llama-server `/metrics` (needs the API key), a Prometheus job with
  `authorization: { credentials: <key> }`.
- Grafana dashboard: TG/PP tok/s, KV-cache usage, requests, system, temps, power.
- Loki plus a tiny journald-to-Loki pusher, feeding a live engine-activity logs panel. This is
  the fix for "the harness spinner tells me nothing": you watch the engine's own PP, generation,
  and slot lines stream.
- On bare metal we also scraped BMC power via Redfish; on GCP use Cloud Monitoring instead. Note
  that some BMC power sensors report bogus spikes, so filter implausible values.

Under 100% CPU load the server's HTTP endpoints (`/metrics`, `/slots`, `/health`) can get starved
and time out; that's "busy," not "down." The journal-based Loki view keeps working because it's
OS-level.

---

## 13. Quick performance-vs-goal cheat sheet

| You want | Do this |
|---|---|
| Max quality, patient use | GLM-5.2 Q4, this setup. TG ~18-25 tok/s here. |
| Faster generation | Fewer-active model: Kimi K2 (32B) or Qwen3-Coder-Next (3B active, ~5-10x). |
| Fast first-token on big context | Add a GPU for attention/KV offload (`-ngl 99 -ot exps=CPU`), or keep context small. |
| Serve many users | Wrong tool: CPU aggregate throughput is low. Use GPUs. |
| Don't time out on huge prompts | Raise client timeout to 30-60 min; but really, keep context lean. |

---

## Build order (summary)

1. Provision and RAID0 NVMe into `/models`, install deps.
2. `numactl --hardware`, understand your NUMA nodes.
3. Build ik_llama.cpp with `GGML_NATIVE=ON`; verify VNNI via `objdump`.
4. Download GLM-5.2 Q4_K_XL (IPv4, parallel).
5. Write `serve-glm.sh` with `--numa distribute`, thinking off, repeat-penalty, threads=physical.
6. Benchmark NUMA strategies and thread counts; keep the winner.
7. systemd service, `LimitMEMLOCK=infinity`.
8. Harness: opencode direct; keep context lean; long timeouts.
9. Prometheus, Grafana, and Loki for visibility.

All of the above was worked out on a single-socket 9575F. The dual-socket 9B45 should roughly
double memory bandwidth (about 2x TG), but only if the NUMA interleaving in section 4 is done right.
