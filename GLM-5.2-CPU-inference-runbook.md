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
objdump -d build/ggml/src/libggml.so | grep -c vpdpbusd   # AVX-512 VNNI int8 dot-product
```
> The path matters. ggml lives at **`ggml/src/libggml.so`** in an ik build tree; `bin/libggml-cpu.so`
> is mainline llama.cpp's layout, and checking that in an ik tree reports 0 for a perfectly good
> build. This runbook and `install.sh` both had it wrong until DS4 forced a rebuild.
>
> `GGML_AVX512_VNNI=OFF` in CMakeCache is a separate red herring: `GGML_NATIVE=ON` already enables
> it via the compiler. The `objdump` count is the source of truth.

Fused MoE is on by default (`--no-fmoe` disables it). We tested `--run-time-repack` (RTR): only
about 2% gain and it forces `--no-mmap`, so skip RTR. We tested MTP speculative decoding and it
made TG 2x slower on MoE (verifying drafts pulls in many experts' weights), so don't use
spec-decode on MoE.

> Both of those conclusions were reached on a **440 GB** model, and both are worth re-measuring per
> model rather than inheriting. RTR was rejected largely because forcing `--no-mmap` on 440 GB was
> unaffordable — at DeepSeek-V4-Flash's 155 GB it is not, and ik has since added `MXFP4_R8`
> ([#2196](https://github.com/ikawrakow/ik_llama.cpp/pull/2196)), a repacked MXFP4 CPU kernel that
> did not exist when we tested. See §6b.

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

In practice you don't run that by hand: `glm-model download <variant>` does the same thing driven
by the registry, and refuses to mark a variant ready until every shard's size matches HuggingFace.

---

## 6a. Other models: Kimi, and how to judge whether one fits

The registry (`serving/glm-variants.conf`) is not GLM-only. Anything ik_llama.cpp can load and that
fits in RAM can be a variant; field 9 (`opts`) carries the flags that model needs, and field 8
(`engine`) carries *which build of ik* serves it (see §6b — new architectures need newer engines
than the one already working, and repointing the global engine to gain one model re-rolls the dice
on every other model on the box).

**Registered and runnable today:**

| variant | quant | size | notes |
|---|---|---|---|
| `base` | unsloth UD-Q4_K_XL | 440 GB | GLM-5.2, the reference |
| `kimi-k2.7-code` | unsloth UD-Q4_K_XL | 584 GB | current Kimi coder |
| `kimi-k2.6` | ubergarm Q4_X, 4.549 bpw | 584 GB | built to match Moonshot's official int4; ubergarm targets ik specifically |

Kimi K2.x is ~1T total but only **~32B active** against GLM's ~40B. Since TG is bandwidth-bound
(§0), fewer active parameters means Kimi **generates faster** than GLM here, at ~145 GB more RAM.

**The sizing rule that actually matters.** Judge a model by the size of the *quant file*, not by
parameter count, and check it against RAM *before* downloading half a terabyte:

```bash
glm-model upstream            # every registered variant: what HF publishes now, and does it fit
glm-model upstream kimi-k3    # just one
```

That command exists because a registry row is a *guess* about what a publisher will name their
files. It prints what is really in the repo, so you can correct `subdir`/`prefix` before fetching.

### Known ik + Kimi issue: multi-turn tool calls can 400 silently

[ik #1605](https://github.com/ikawrakow/ik_llama.cpp/issues/1605), open since 2026-04-09 with no
comments, affects both registered Kimi variants (K2.6 and K2.7-Code share the `kimi_k25` template
family):

> Silent HTTP 400 when an assistant message has content + tool_calls in multi-turn conversations.

It never fires on the first request — only after a tool-call round-trip, when the client replays an
assistant message carrying *both* content and `tool_calls`. The free text between `</think>` and
`<|tool_calls_section_begin|>` appears to break GBNF grammar generation. The server returns 400
instantly with an empty body, and a fresh request seconds later succeeds. That combination makes it
read like a flaky network rather than a template bug.

Note the reporter already had thinking off, so **`--reasoning off` does not avoid this** — the
registry's `opts` fix the reasoning-channel problem, not this one. Untested candidate workarounds:
`--skip-chat-parsing` (forces a pure content parser, at the cost of tool-call parsing), or driving
Kimi without grammar-constrained tool calling. Neither is verified here.

Practical read: Kimi is fine for chat and single-shot coding on this box; treat **agentic
tool-calling loops as unproven** until this is either fixed upstream or worked around locally. GLM
remains the known-good agentic path.

### Engine vintage matters for Kimi

[ik #1686](https://github.com/ikawrakow/ik_llama.cpp/pull/1686) (merged 2026-04-24) fixed the
Kimi-K2 parser ignoring `enable_thinking=false` and dumping the whole response into
`reasoning_content`. Any ik build older than that mis-handles the registry's `--reasoning off`.
Check with `git -C ~/ik_llama.cpp log -1 --date=short --format=%ad` — anything from 2026-05 onward
is fine.

### Why "turn thinking off" is per-model (and why `opts` exists)

§10 explains why reasoning blocks must be off for agentic harnesses. There is no single flag:

- **GLM** — its template takes a kwarg: `--chat-template-kwargs '{"enable_thinking": false}'`
- **Kimi** — its template has **no such variable at all**; it unconditionally opens the assistant
  turn with `<think>`. The kwarg is silently a no-op. Use `--reasoning off` instead.

That silent no-op is the trap: pass GLM's flag to Kimi and nothing errors, you just get thinking
output that breaks the harness. Before `opts` existed these flags were hardcoded in `serve-glm.sh`,
so every model got GLM's.

### Kimi K3: why it is registered but not servable (as of 2026-07-28)

K3 (released 2026-07-27) is a ~2.8T MoE — 896 experts, 16 active + 2 shared, 93 layers, hybrid
KDA-linear + MLA attention, multimodal, 1M context. Two independent blockers:

**1. It does not fit at 4-bit.** Moonshot ships the routed experts *already* 4-bit (`mxfp4`,
group 32; attention, shared experts, dense MLP, lm_head and the vision tower stay bf16). The native
release is **1,561 GB** against this box's 1,133 GB. So a true 4-bit K3 cannot run here at all, and
anything that fits is a **requantisation of already-4-bit weights** down to ~2–3 bpw. Expect it to
land *below* GLM-5.2 Q4_K_XL, not beside it.

| what exists | size | fits 1,133 GB? |
|---|---|---|
| native mxfp4 (true 4-bit) | 1,561 GB | no, +428 GB over |
| unsloth `UD-Q8_K_XL` (34 shards) | 1,561.2 GB | no |
| unsloth `UD-Q4_K_XL` (32 shards) | 1,508.7 GB | no |
| GrEarl `Q2_K`, 2.673 bpw | 929 GB | yes, tight |
| **unsloth `UD-Q2_K_XL` (19 shards)** | **861.3 GB** | **yes, ~270 GB spare** |
| unsloth `UD-IQ2_XXS` (16 shards) | 711.1 GB | yes |
| unsloth `UD-IQ1_M` (15 shards) | 648.9 GB | yes |
| unsloth `UD-IQ1_S` (14 shards) | 594.0 GB | yes |

unsloth published the sub-4-bit tiers on 2026-07-29, so **a fitting quant now exists** —
`UD-Q2_K_XL` is the largest, at roughly 2.5 bpw.

Their Q8 tier is the same size as Moonshot's native MXFP4 release, which states the
double-quantisation problem as plainly as it can be put: above 4 bits there is no information left
to keep, and below it you are destroying what the QAT put there. Whether ~2.5 bpw over
already-4-bit-QAT weights beats the GLM-5.2 Q4 running here is **unmeasured**, and it is the
question that decides whether any of the engine work below is worth doing.

GrEarl's is the only K3 GGUF that exists, and its own README says the author lacks the hardware to
run or validate it. It is deliberately **not** registered. Prefer a *dynamic* quant (unsloth `UD-*`,
ubergarm `IQ*_K`) when they land: keeping sensitive tensors at higher precision matters far more
than usual when the source is already quantised.

**2. No engine we run can load it — now the only blocker.** ik has no `kimi-k3` arch; mainline's
PR [#26185](https://github.com/ggml-org/llama.cpp/pull/26185) is open and conflicted; unsloth built
these GGUFs against their own fork ([unslothai/llama.cpp#48](https://github.com/unslothai/llama.cpp/pull/48))
and their README says to use it. No `opts` value substitutes for a missing arch.

There is also a cap that precedes all the arch work: `LLAMA_MAX_EXPERTS` is **512** (raised for
Qwen3 Next) and K3 has **896**, so it trips a generic assert in `load_hparams` before any arch hook
runs — in ik that is `src/llama-hparams.cpp:9`, asserted at `:165`. Mainline's PR to raise it
(#26192) was closed unmerged. One line, but step zero.

Two further things from Moonshot's README invalidate settings that are correct for K2.x:

- **K3 always thinks** — no `enable_thinking` equivalent, and effort is a top-level
  `reasoning_effort` field (`low`/`high`/`max`, default `max`) that llama.cpp has no flag for. So
  `--reasoning off` is meaningless for K3; the registry uses `--reasoning-format deepseek` instead.
  With `max` as default, thinking dominates the cost of a reply.
- **Preserved thinking history** — multi-turn and tool calls require the *complete* assistant
  message replayed, including `reasoning_content` and `tool_calls`. That is exactly the shape
  ik #1605 400s on. For K2 you can avoid it; for K3 it is mandatory, so #1605 becomes **blocking**
  for agentic K3 rather than a caveat.

Porting it to ik is *tractable but not small*. ik already has the hard parts:

- `ggml_delta_net` — the gated delta-rule linear-attention kernel (from Qwen3-Next)
- `ggml_ssm_conv` — the short convolution KDA needs (kernel size 4)
- the hybrid recurrent+attention KV cache: per-sequence state slots, save/restore, mixed-batch
  handling. This is the genuinely gnarly infrastructure and it is done.
- MLA via `LLM_ARCH_DEEPSEEK2` for K3's 24 full-attention layers; sigmoid-router grouped-topk MoE;
  MXFP4 (`MXFP4_R8` landed 2026-07-28), matching K3's native weight format
- DS4 / `HC_PRE`, which the PR reuses for the cross-layer residual, is landing upstream now

What would still have to be written: the `situ` activation (replaces SwiGLU *everywhere*, so it
needs an AVX-512 path or it becomes the bottleneck); **the full-rank KDA gate** — K3 sets
`use_full_rank_gate`, a per-channel decay, where ik's `ggml_delta_net` takes a per-head scalar `g`,
so the inner recurrence has to change, and this is the main technical risk; latent MoE (routed
experts at 3584 latent vs 7168 hidden); the MLA output gate; and the arch plumbing plus a ~600-line
graph builder rewritten against ik's monolithic `src/llama.cpp` style rather than mainline's
`src/models/*.cpp` — a re-implementation, not a cherry-pick.

Mainline's version is 1,313 lines from an experienced llama.cpp contributor. Budget 1–3 weeks
against a PR that is days old and still churning.

**The reason not to do it yet is not difficulty, it's payoff:** a perfect port buys you K3 at
~2.7 bpw, which is very likely worse than the GLM-5.2 Q4 you already run. Wait for unsloth and
ubergarm — both have shipped every prior Kimi — then reassess. `glm-model upstream` is the check.

Groundwork is in `porting/k3/`: a validated numerical oracle for the four new ops, plus the port
sequence. See that README before writing any C.

### Streaming experts instead of fitting them: measured, and rejected

The "it doesn't fit" verdict above assumes the model is pinned with `--mlock`. It is fair to ask
whether K3 could instead be *streamed* — mmap the full 1,561 GB from NVMe and let the page cache
hold what it can, exploiting the fact that only 16 of 896 experts fire per token.
[gavamedia/deltafin](https://github.com/gavamedia/deltafin) does exactly this to run K3 on a 64 GB
Mac. We measured whether it makes sense here. It does not.

Each token reads **16 experts × 92 MoE layers × 17.5 MB = 25.8 GB** of expert weights. Where those
bytes come from is the entire question:

| | deltafin (M1 Max, 64 GB) | this box (1,133 GB) |
|---|---|---|
| expert cache coverage | ~2.4% | ~67% |
| bytes/token from storage | ~25 GB | ~8.5 GB (uniform routing) |
| storage read rate | ~6.6 GB/s | **4.6 GB/s measured** |
| result | 16–76 s/token | **~0.5–1.5 tok/s** |

Measured with `dd iflag=direct` (page cache bypassed) on `/models`: 5.4 GB/s sequential, 4.2–5.1
GB/s on 20 MB expert-sized random reads. Worth knowing: **md1 is RAID0 across two NVMe, not four**
— §1 suggests four, and this box has two.

Against that, a ~2.7 bpw quant that *fits* is ~930 GB, RAM-resident, reading ~16.4 GB/token at
memory bandwidth instead of storage bandwidth: **~10–20 tok/s**, the same class as GLM-5.2 Q4.

So streaming is a **~15× regression here**. It is the right design on a 64 GB machine, where
nothing else is possible; on this box capacity is the one thing we have, and the correct move is
to make the model fit and pin it. The general rule, worth remembering beyond K3: on this hardware,
**always prefer the largest quant that fits in RAM over streaming a better one from disk.** The gap
between memory bandwidth and NVMe is two orders of magnitude, and MoE sparsity does not close it.

---

## 6b. DeepSeek-V4-Flash: the first model that is *small* here

Everything above is written around models that barely fit. DeepSeek-V4-Flash-0731 does not have
that shape, and it is worth understanding why, because the reasoning generalises to whatever ships
next.

| | GLM-5.2 Q4 | Kimi K2.7 Q4 | **DS4-Flash MXFP4** |
|---|---|---|---|
| total params | 753B | ~1T | **284B** |
| active params | ~40B | ~32B | **~13B** |
| on disk | 440 GB | 584 GB | **155 GB** |
| bytes read per token | ~23 GB | ~19 GB | **~7–8 GB** |

Three things stack up in its favour on a bandwidth-bound box:

1. **Only ~13B active.** 43 layers, 256 routed experts, 6 active + 1 shared, `moe_intermediate_size`
   2048. TG ≈ bandwidth / bytes-per-token (§0), so a third of GLM's read per token is roughly three
   times the tokens per second from the same memory.
2. **The experts are natively fp4.** `expert_dtype: "fp4"` in the config — DeepSeek trained and
   shipped them at 4 bits. A straight MXFP4 conversion is therefore *lossless*, not a quantisation.
3. **DSA keeps long context cheap.** `index_topk 512`, `sliding_window 128`: attention is sparse by
   construction, so the O(n²) prompt-processing wall that dominates §10's "context is everything"
   advice is much further out.

### Choosing the quant: the ladder is the wrong axis

Because the source is already fp4, the usual "how many bits can I afford" ladder does not apply:

- **Above fp4 recovers nothing.** unsloth's `UD-Q8_K_XL` is 161.9 GB against `UD-Q4_K_XL`'s 155.1.
  Seven extra gigabytes buy precision the weights never had.
- **Below fp4 destroys what training put there.** The IQ1/IQ2 tiers (82–97 GB) exist, and on a box
  with 1.1 TB of RAM there is no reason to touch them.

What *is* worth choosing is the treatment of the ~5% of tensors that are **not** fp4 experts —
attention projections, the hyper-connection layers, the compressor, and the DSA indexer. Hence the
three registered rows are a *treatment* comparison, not a size ladder:

| variant | publisher | size | what it is |
|---|---|---|---|
| `ds4-flash` | ggml-org | 155.0 GB | straight MXFP4 conversion, one unsharded file. The reference. |
| `ds4-flash-mix` | antirez | 156.0 GB | MXFP4 experts, F16 hyper-connection/compressor/indexer, Q8 attention/shared/output, imatrix |
| `ds4-flash-unsloth` | unsloth | 155.1 GB | the control — see the garbling note below |

### Engine: this is the part that will bite you

`deepseek4` did not exist in ik_llama.cpp before **2026-07-22**, and was still being fixed daily
through 08-03. Two of those late fixes are CPU GEMM *correctness*, which matters more here than
anywhere else because this box has no GPU:

- [#2224](https://github.com/ikawrakow/ik_llama.cpp/pull/2224) — Fix IQ3_XXS CPU GEMM (08-01)
- [#2233](https://github.com/ikawrakow/ik_llama.cpp/pull/2233) — Fix IQ4_NL_R4 GEMM on CPUs with
  FANCY_SIMD, i.e. exactly this AVX-512 part (08-02)

Those are the fixes behind [#2214](https://github.com/ikawrakow/ik_llama.cpp/issues/2214) /
[#2218](https://github.com/ikawrakow/ik_llama.cpp/issues/2218), where DS4 loaded happily and then
emitted `"dekametersapl dekametersapl"` — perplexity looked normal, so it was a broken quantisation
kernel, not a broken model. ikawrakow's interim workarounds were `-rtr` and `-ctk q8_0`, and
`serve-glm.sh` already passes the latter.

**Do not turn flash attention off for DS4.** It was the first thing the #2218 reporter tried and
ikawrakow asked twice for it to be left on.

This is why the registry gained an `engine` field. Bringing up DS4 means moving the engine forward
by three weeks of commits; doing that globally would have silently re-rolled GLM and Kimi too. So
DS4 rows point at `build-ds4` while everything else stays on `build`:

```bash
cd ~/ik_llama.cpp && git fetch && git checkout 6038941
cmake -B build-ds4 -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DLLAMA_CURL=OFF
cmake --build build-ds4 --config Release -j 60
objdump -d build-ds4/ggml/src/libggml.so | grep -c vpdpbusd   # 13271 here
```

Note the objdump path: **`ggml/src/libggml.so`**, not `bin/libggml-cpu.so`. The latter is mainline
llama.cpp's layout; checking it in an ik tree silently reports 0 and makes a correctly-built engine
look like it has no VNNI at all. (§5 and `install.sh` had this wrong.)

### Not every publisher shards

ggml-org and antirez both ship DS4 as **one ~155 GB file** with no `-000NN-of-000MM` suffix.
Publishers shard when they must, and 155 GB is small enough that several do not bother. The
registry's `shards` field takes `1` to mean exactly that, and `glm-model` and `serve-glm.sh` handle
both layouts.

### Thinking, tool calls, and what is still open

DS4 **always reasons**. There is no `enable_thinking` kwarg (§6a), so GLM's flag is a silent no-op
and Kimi's `--reasoning off` is not the lever either. Two options, and they are not equivalent:

- `--reasoning-format deepseek` — routes thoughts to `message.reasoning_content`. Note ik's own help
  text: *"except in streaming mode, which behaves as `none`"*, and harnesses stream.
- `--reasoning-budget 0` — ends thinking immediately. The closest equivalent to §10's thinking-off
  requirement, but this model is *trained* to reason, so it is a quality tradeoff rather than a free
  win. Measure it (`/models/.ds4-run/validate-ds4-flash-nothink/`) rather than assuming.

**Still open at pin 6038941:** [#2242](https://github.com/ikawrakow/ik_llama.cpp/pull/2242) fixes
DSV4 tool-call wiring. Without it ik falls back to the autoparser, which forces `string="true"` on
every argument — that diverges from what the template renders, so it **breaks prompt caching** and
loses parallel tool calls. Verify tool calling before pointing a coding harness at DS4; the
validation script checks for exactly this signature.

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
    --ctx-size 65536 \                         # 64K. Fits to 1M on RAM, but PP is O(n^2): a 128K
                                               # first-token is ~2-3 HOURS. See "Context window: the trap".
    --defrag-thold 0.1 \
    --parallel 1 \                             # single user: one slot = full context, best latency
    --threads 192 --threads-batch 192 \        # = PHYSICAL cores. never use SMT threads (see 11)
    --batch-size 2048 --ubatch-size 2048 \
    -fa on \                                   # flash attention
    --cache-type-k q8_0 --cache-type-v q8_0 \  # quantized KV to save memory at long context
    --mlock \                                  # pin weights in RAM (no page-fault jitter)
    --jinja \                                  # use the model's embedded chat template
    --repeat-penalty 1.1 --repeat-last-n 256 \ # stops repetition loops; see 10
    --metrics \                                # Prometheus /metrics endpoint
    --api-key-file ~/.glm-api-key \
    --chat-template-kwargs '{"enable_thinking": false}'   # PER-MODEL; see below and 6a
```

The last line is the only model-specific flag here, and in the real script
(`serving/serve-glm.sh`) it does **not** appear: it comes from field 8 (`opts`) of the registry
row, appended last so a variant can override any default above it. That indirection exists because
the equivalent flag differs per model family — Kimi needs `--reasoning off` and silently ignores
GLM's kwarg (§6a). Everything else on this command line is a property of the *box*, not the model,
and stays hardcoded.
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

### Context window: the trap (read this)

The single biggest usability failure mode. Prompt-processing is **O(n^2)** in attention, so the PP
*rate* collapses as context grows. DSA keeps the KV cache tiny in **memory**, but does nothing for the
**compute** — so a big context does not crash, it silently grinds for *hours*. Approximate first-token
latency vs. context on a single 9575F:

| Context | KV mem | First-token PP | Usable? |
|--------:|-------:|---------------:|:--------|
| 16K | ~2 GB | ~4 min | yes |
| 32K | ~4 GB | ~10-12 min | borderline |
| 64K | ~8 GB | ~40 min | slow |
| 128K | ~12 GB | **~2-3 HOURS** | no (the overnight-grind trap) |
| 1M | ~48 GB | most of a day | never |

**Two coordinated limits stop this, and they MUST be paired:**

1. **Server `--ctx-size` = the hard ceiling** (kit default 64K). Nothing can exceed it.
2. **Harness context limit set BELOW that ceiling**, so the harness auto-**compacts** before it hits
   the server. In opencode: `"glm-5.2": { "limit": { "context": 60000, "output": 8000 } }` for a 64K server.

The failure we actually hit: opencode had *no declared limit*, assumed a huge window, ballooned to 142K
tokens, and the (mistaken) 1M server ctx let it grind overnight for a client that had already timed out.
The *opposite* mistake — harness limit **above** the server ctx — makes the server reject the oversized
prompt so the harness **errors** instead of compacting. So: **`harness_limit < server_ctx`, always.** If
you raise one, raise both — and remember 128K = hour(s)-long first tokens.

Even within the limits, keep working context lean: `/clear` between tasks, small focused turns, subagents.
The only thing that makes big context *fast* is the **GPU hybrid** (attention offload) — it removes the
O(n^2) CPU wall entirely.

**Large-context / audit mode.** A few tasks genuinely need whole-codebase context — deep **security
auditing** (cross-file taint / data-flow) is the classic one. For those, run the server at
`CTX=131072` (128K) with opencode `limit.context: 120000`, and treat it as a **batch job**: kick off
"audit X, report findings," accept the ~2-3 h first token, then ask follow-ups against the *warm cache*
(fast). Use `opencode --continue` to resume if interrupted — but **do not bounce glm-server mid-audit**,
or resume re-pays the full multi-hour prompt-processing. Do the *drill-downs* in a separate **lean**
session (small context, minutes/turn); the 128K sweep is for mapping the attack surface, not iterating.

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
| Faster generation | Fewer-active model: `glm-model use kimi-k2.7-code` (~32B active vs GLM's ~40B), or Qwen3-Coder-Next (3B active, ~5-10x). |
| Coding specifically | `kimi-k2.7-code` (unsloth UD-Q4_K_XL, 584 GB). Genuine 4-bit, fits with ~550 GB spare. |
| Kimi K3 | Not yet: doesn't fit at 4-bit (1,561 GB native) and ik has no `kimi-k3` arch. See 6a. |
| Fast first-token on big context | Add a GPU for attention/KV offload (`-ngl 99 -ot exps=CPU`), or keep context small. |
| Serve many users | Wrong tool: CPU aggregate throughput is low. Use GPUs. |
| Don't time out on huge prompts | Raise client timeout to 30-60 min; but really, keep context lean. |

---

## Build order (summary)

1. Provision and RAID0 NVMe into `/models`, install deps.
2. `numactl --hardware`, understand your NUMA nodes.
3. Build ik_llama.cpp with `GGML_NATIVE=ON`; verify VNNI via `objdump`.
4. Download GLM-5.2 Q4_K_XL (IPv4, parallel) — or `glm-model download <variant>`.
5. Write `serve-glm.sh` with `--numa distribute`, thinking off, repeat-penalty, threads=physical.
6. Benchmark NUMA strategies and thread counts; keep the winner.
7. systemd service, `LimitMEMLOCK=infinity`. Install the registry and `glm-model` (`install.sh`
   step 4) — without `/etc/glm-variants.conf` the unit starts and immediately dies.
8. Harness: opencode direct; keep context lean; long timeouts.
9. Prometheus, Grafana, and Loki for visibility.

All of the above was worked out on a single-socket 9575F. The dual-socket 9B45 should roughly
double memory bandwidth (about 2x TG), but only if the NUMA interleaving in section 4 is done right.
