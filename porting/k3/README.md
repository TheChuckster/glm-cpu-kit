# Porting Kimi K3 to ik_llama.cpp

The port lives on [`TheChuckster/ik_llama.cpp`](https://github.com/TheChuckster/ik_llama.cpp),
branch `kimi-k3`. It is a fork because ikawrakow declined the work himself on
[ik #2203](https://github.com/ikawrakow/ik_llama.cpp/issues/2203) — *"Kimi-K3 is
seriously beyond my hardware limits. Not sure I want to just blindly copy the
mainline K3 PR"* — which is a hardware and review-confidence objection, and
exactly the one a port validated on real hardware answers.

## Current status (2026-08-23)

| | |
|---|---|
| upstream base | ik `main` `8337e4cd` |
| fork tip | `d39033a5` (`kimi-k3`) |
| architecture + graph | **complete** — 93 layers, SiTU, AttnRes, latent MoE, 69 KDA + 24 MLA |
| recurrent correctness | **complete** — per-step SSM/conv checkpoints at `e3b9f045` |
| parser/tools | **complete** — clean reasoning/content and nested K3 tool calls, 5/5 |
| production quant | `kimi-k3-q5attn`, 19 shards, about 788 GiB on disk |
| quality | wikitext PPL **1.3253 +/- 0.031** |
| live speed | **42.607 PP tok/s**, **4.453 TG tok/s** (2026-08-22) |
| deployment | `glm-model use kimi-k3-q5attn`; API alias `kimi-k3` |

The branch was rebased from old base `40dffce6` onto upstream `8337e4cd` and
reconciled against Firedancer's `kimi-k3` and `main-patches` branches. Their
DS4/KV patches were already present; the missing malformed-request HTTP 400 fix
was added as `cfac74d2`. Upstream's newly shared KDA fields were consolidated
with the K3 implementation reconciled in `f921647b`; `d39033a5` adds the
deployed message-termination fix.

Verification after the rebase: full build; seven focused parser, Jinja, and
delta-net tests; the fixture, composition, and delta-net numerical oracles; and
a live structured tool call all pass. The pre-rebase tip is archived at
`archive/kimi-k3-pre-rebase-20260822`.

The rest of this document preserves the architecture analysis and the original
implementation sequence. Treat future-tense passages as the porting record;
the status table above is authoritative. The much more detailed chronological
debugging record is in [`GRAPH-BUILDER-SPEC.md`](GRAPH-BUILDER-SPEC.md).

## What ik had underneath the port (checked, not assumed)

The original estimate here was written before ik landed DeepSeek-V4. That work
changed the shape of the port substantially:

- **AttnRes does NOT map onto ik's `hc_pre` — this claim was wrong.** ik gained
  `GGML_OP_HC_PRE` from DeepSeek-V4 and mainline's K3 builder calls
  `ggml_dsv4_hc_pre`, so this originally read "a call, not an implementation".
  They are different ops sharing a name fragment: ik's takes `scale[3]`,
  `bias[S*S+2S]` and runs Sinkhorn iterations, mainline's is a plain weighted sum
  over `ne1`. Feeding an AttnRes stack to ik's would assert. Build the sum from
  primitives instead — there are at most 8 checkpoints, so it is nearly free.
  See `GRAPH-BUILDER-SPEC.md`.
- **Every primitive K3 needs already exists in ik**: `softplus`, `l2_norm`,
  `sigmoid`, `tanh`, `sum_rows`, `soft_max`, `ssm_conv`, `delta_net`, `hc_pre`,
  `hc_post`, `concat`, `scale`. Mainline's PR touches **no ggml files at all**.
- **SiTU needs no kernel.** Mainline composes it from `tanh`/`sigmoid`/`mul`;
  there is no `ggml_situ`. A fused AVX-512 version is an optimisation, not a
  prerequisite.

## Step 0: the expert-count cap — done

```
src/llama-hparams.cpp:10   #define LLAMA_MAX_EXPERTS 512  // Qwen3 Next
src/llama-hparams.cpp:171  GGML_ASSERT(hparams.n_expert <= LLAMA_MAX_EXPERTS);
```

K3 has **896** routed experts, so it trips an arch-generic assert in
`load_hparams` before any `kimi-k3` hook could run.

Audited rather than assumed, and in ik it is genuinely free: the constant appears
**only** at its definition and that one assert, nothing allocates against it, and
`n_expert` is `uint32_t`. Raised to 1024.

Correcting the earlier note here: mainline's standalone bump
([#26192](https://github.com/ggml-org/llama.cpp/pull/26192)) was **not** rejected
on technical grounds — the PR-template bot closed it over its description
formatting and AI-generated content. Mainline is doing the same bump inside the
K3 PR itself. There was never a technical objection to inherit.

## Serving K3 is not like serving K2.x

Two things from Moonshot's README that a port has to accommodate, both of which
invalidate settings that are correct for K2:

- **K3 always thinks.** There is no `enable_thinking` equivalent, so
  `--reasoning off` is meaningless. Use `--reasoning-format deepseek`. The
  embedded template accepts `thinking_effort`; the production row sets it to
  `low` and applies a 1024-token default reasoning budget so agent requests
  finish in finite time. Raise effort per request for hard problems.
- **Preserved thinking history.** K3 was trained expecting the *complete*
  assistant message replayed on every turn — `reasoning_content` and
  `tool_calls`, not just `content`. That is exactly the message shape
  [ik #1605](https://github.com/ikawrakow/ik_llama.cpp/issues/1605) historically
  reported a silent HTTP 400 for the `kimi_k25` family. The current fork passes
  the replay gate for K3; clients still must retain the complete message.

---

## First: do NOT copy deltafin's architecture

[gavamedia/deltafin](https://github.com/gavamedia/deltafin) runs K3 on a 64 GB
Mac by streaming MXFP4 experts on demand from disk. It is careful work and its
research notes are the best public writeup of K3's internals — but its central
technique is the wrong answer for this box, and the numbers say so clearly.

Every token reads **16 experts × 92 MoE layers × 17.5 MB = 25.8 GB** of expert
weights. That figure is the whole ballgame, and where those bytes come from
decides everything:

| | deltafin (M1 Max, 64 GB) | chuckdancer (1,133 GB) |
|---|---|---|
| expert cache coverage | ~2.4% | ~67% |
| bytes/token from storage | ~25 GB | ~8.5 GB (uniform routing) |
| storage read rate | ~6.6 GB/s | **4.6 GB/s measured** (md1, RAID0 ×2 NVMe) |
| result | 16–76 s/token | **~0.5–1.5 tok/s** |

Measured on chuckdancer with `dd iflag=direct` (page cache bypassed): 5.4 GB/s
sequential, 4.2–5.1 GB/s on 20 MB expert-sized random reads. md1 is RAID0 across
**two** NVMe, not four.

Now compare against simply making the model fit. A ~2.7 bpw K3 is ~930 GB,
fully RAM-resident under `--mlock` keeps those reads at memory bandwidth rather
than storage bandwidth. The early **10–20 tok/s** estimate was optimistic:
bytes-per-token analysis later found 71.2 GiB of always-read plus active-expert
traffic, and the production Q5-attention model measures **4.453 tok/s**. That is
still several times faster and far more stable than NVMe expert streaming.

**Streaming is a ~15× regression on this hardware.** It is the right design for
a 64 GB machine, where nothing else is possible. Here, capacity is the one thing
we have. Take deltafin's *architecture reference* — the ops below, the MXFP4
layout, the KDA gate math — and none of its runtime.

The quality caveat stands and is unresolved: K3's routed experts ship *already*
4-bit (mxfp4, QAT'd from SFT onward), so a 2.7 bpw quant is a requantisation of
already-quantised weights. Prefer dynamic quants (unsloth `UD-*`, ubergarm
`IQ*_K`), which hold sensitive tensors higher — that matters far more than usual
here. Whether the result beats GLM-5.2 Q4 is an open empirical question, and the
reason not to sink weeks into this before a quant exists to measure.

---

## The four new ops

`k3_ops_oracle.py` implements each one, transcribed from Moonshot's own
`modeling_kimi_linear.py` with source lines cited, and cross-checked against
PyTorch (`--check-torch` agrees to ≤7.6e-6, float32).

Three of these fail **silently**. Get one subtly wrong and the model runs and
emits plausible, slightly-worse text — and K3 is far too large to diff against a
reference implementation locally. That is why the harness comes before the code.

### 1. SiTU — `hidden_act: "situ"`, replaces SwiGLU everywhere

```
gate, up = split(x)
situ_a   = beta * tanh(gate / beta) * sigmoid(gate)      # beta = 4.0
up       = linear_beta * tanh(up / linear_beta)          # linear_beta = 25.0
out      = situ_a * up
```

A range-limited SwiGLU: as `beta → ∞`, `beta*tanh(g/beta) → g` and `situ_a →
g*sigmoid(g)` = SiLU. The tanh soft-clips both paths. That clipping is not
cosmetic — K3 is QAT'd with MXFP8 activations and these are the bounds the
quantiser trained against.

Cheapest possible mistake: implementing it as SwiGLU. It will look fine.

Because it replaces SwiGLU *everywhere*, it lands on the hot path — it needs an
AVX-512 path or it becomes the bottleneck by itself.

### 2. AttnRes — replaces the residual stream, applied twice per layer

```
v      = cat(block_residual, prefix_sum)      # (T, nblocks+1, H)
k      = v * rsqrt(mean(v²) + eps)            # RMSNorm, WITHOUT its weight
scores = sum(k * (norm.weight * proj.weight), -1)
out    = softmax(scores) @ v                  # weighted sum over UNNORMALISED v
```

Rather than `h += f(h)` uniformly, each layer takes a softmax-weighted average
over previous blocks' outputs — selective retrieval across depth.
`attn_res_block_size = 12`, so ceil(93/12) = 8 blocks.

Two things for the C port:

- `norm.weight` and `proj.weight` collapse into **one `[hidden]` vector**,
  constant per layer. Fold at load time. Runtime is then an RMSNorm, a dot
  product, a softmax over 9 elements, and a weighted sum — trivial FLOPs. The
  real cost is keeping the per-block residual history live.
- The RMSNorm deliberately has **no weight applied to `v`**, and the output sum
  is over **unnormalised** `v`. Normalising the output is the obvious wrong turn.

mainline's PR reuses DeepSeek4's `HC_PRE` for this weighted sum. ik is landing
DS4 right now (#2190, #2194), so this may get cheaper — check before writing it.

### 3. Gated MLA — sigmoid gate before `o_proj`

```
g = sigmoid(g_proj(hidden_states))   # NOTE: the layer INPUT, not attn output
attn_output = attn_output * g
attn_output = o_proj(attn_output)
```

Three lines, and it still gets a fixture: gating on the attention output instead
of the layer input is a silent, entirely plausible-looking bug.

### 4. KDA gate — the highest-risk item

```
g = g + dt_bias.view(H, K)
a = exp(A_log)                       # [H] → (H,1)   or   [K] → (1,K)
g = lower_bound * sigmoid(a * g)     # lower_bound = -5.0
```

**Corrected against the shipped GGUF.** This section previously said K3 ships
`A_log` with shape `[128] = [K]`, one decay per channel. It does not — the GGUF
has `ssm_a` at `[96] = [H]`, one per **head**, same as Qwen3-Next.

The full-rank-ness comes from somewhere else: the gate is produced by a low-rank
projection, `ssm_f_a [7168, 128]` → `ssm_f_b [128, 12288]`, yielding
`12288 = H×K = 96×128` values per token. So `a = exp(A_log)` is per-head, but the
`g` it multiplies is per-channel, and the product entering the recurrence is a
full `[B,T,H,K]` tensor either way.

The conclusion is unchanged and the fix is the same — `ggml_delta_net`'s inner
loop must broadcast a per-channel decay — but the reason matters if you are
reading tensor shapes to decide what to implement. `ssm_dt.bias` is `[12288]`,
which is the giveaway: a per-head gate would not need `H×K` biases.

**Done.** ik's assert now reads `g->ne[1] ∈ {1, S_v}`, and the kernel applies the
gate to the state in place, once, before anything reads it — which is what
mainline's kernel does, and the only option for a per-channel decay, since it
cannot be factored out of `v_prime`/`out_val`. Decay is indexed by the **column**
(key) axis, per mainline's `S[i][:] *= exp(g[i])`.

Verified numerically before trusting it: the per-head path is bit-identical
before and after the refactor (max diff 1.7e-17, so no Qwen3-Next regression),
the per-channel path matches an independent matrix-form reference exactly, and a
per-channel gate with all channels equal reproduces the per-head result.

The fused AVX-512 kernel originally could not express a per-channel gate in its
signature, so full-rank gates were skipped and the scalar path ran. It handles
them now (`a9c84ba5`): +29% prompt processing, no change to generation.

### Not an op, but easy to get wrong: latent MoE

Three widths in one block:

```
topk = router(hidden)                  # router scores the FULL 7168 hidden
h    = down_proj(hidden)               # 7168 → 3584
y    = moe(h, topk)                    # 16 routed experts live at 3584
y    = up_proj(norm(y))                # 3584 → 7168
y    = y + shared_experts(identity)    # 2 shared experts at 7168, on the ORIGINAL input
```

ik's DeepSeek-style MoE runs everything at one width, so this is a new code path,
not a parameter change. Note the shared experts consume `identity` — the block's
input — not the down-projected tensor.

---

## The shipped model, read directly (not inferred)

Dumped from the GGUF with `porting/k3/gguf_peek.py` (dependency-free — chuckdancer
has no numpy, and `gguf-py` hard-imports it just to read a header). The GGUF
already declares `general.architecture = kimi-k3`, so the arch name is fixed.

**Layer layout — 93 blocks:**
- `blk.0` alone is dense FFN (`leading_dense_block_count = 1`); `blk.1..92` are latent MoE.
- **24 full-attention (MLA) layers** at indices 3, 7, 11, … 87, 91, **and 92**.
  Note 92 breaks the every-fourth pattern — the last layer is full attention.
- The other **69 are KDA**. Which is which comes from
  `kimi-k3.attention.head_count_kv`, a per-layer **array** (0 = KDA, 1 = full).
  That is unusual and easy to miss: it is a list, not a scalar.

**KDA layer** (`blk.0`): `attn_q/k/v` `[7168,12288]`, `attn_output` `[12288,7168]`,
plus `ssm_a [96]`, `ssm_beta [7168,96]`, `ssm_conv1d_{q,k,v} [4,1,12288]`,
`ssm_dt.bias [12288]`, `ssm_f_a [7168,128]`, `ssm_f_b [128,12288]`,
`ssm_g [7168,12288]`, `ssm_norm [128]`.

**Full-attention layer** (`blk.3`) is DeepSeek-style MLA, which ik already has
via `LLM_ARCH_DEEPSEEK2` and the DS4 work: `attn_q_a [7168,1536]`,
`attn_q_a_norm [1536]`, `attn_q_b [1536,18432]`, `attn_kv_a_mqa [7168,576]`,
`attn_kv_a_norm [512]`, `attn_k_b [128,512,96]`, `attn_v_b [512,128,96]`,
`attn_output [12288,7168]` — plus **`attn_gate [7168,12288]`**, the MLA output
gate. Its input width of 7168 confirms it gates on the **layer input**, not the
attention output, which is the silent bug the fixture guards.

**AttnRes ships pre-folded.** `attn_res_score [7168]` and `ffn_res_score [7168]`
— one vector per site, two sites per layer. The notes predicted `norm.weight` and
`proj.weight` would collapse into a single `[hidden]` vector to fold at load
time; unsloth's converter already did the fold, so there is nothing to do but use
them.

**Latent MoE** (`blk.1`) is exactly the three-width shape described below:
router `ffn_gate_inp [7168,896]` and `exp_probs_b.bias [896]` at full width;
`ffn_routed_down [7168,3584]` → `ffn_routed_norm [3584]` → `ffn_routed_up [3584,7168]`;
experts `ffn_{gate,up}_exps [3584,3072,896]` and `ffn_down_exps [3072,3584,896]`
at the latent width; shared experts `ffn_*_shexp [7168,6144]` at **full** width
(6144 = 2×3072, the two shared experts merged).

**Hparams worth having in one place:** `block_count 93`, `embedding_length 7168`,
`context_length 1048576`, `vocab_size 163840`, `expert_count 896`,
`expert_used_count 16`, `expert_shared_count 2`, `expert_feed_forward_length 3072`,
`expert_latent_length 3584`, `expert_gating_func 2` (sigmoid), `expert_weights_norm true`,
`attention.head_count 96`, `key_length 576`, `value_length 74`,
`key_length_mla 192`, `value_length_mla 128`, `q_lora_rank 1536`,
`kv_lora_rank 512`, `rope.dimension_count 64`, `rope.freq_base 10000`,
`ssm.conv_kernel 4`, `kda.head_dim 128`, `kda.gate_lower_bound -5.0`,
`activation.situ_beta 4.0`, `activation.situ_linear_beta 25.0`,
`attn_res.block_size 12`.

## Components reused from ik

The port was tractable because the genuinely hard infrastructure already existed:

- `ggml_delta_net` — gated delta-rule linear attention (Qwen3-Next)
- `ggml_ssm_conv` — the short convolution KDA needs (kernel size 4)
- hybrid recurrent+attention KV cache: per-sequence state slots, save/restore,
  mixed-batch handling. The gnarliest part, and it exists.
- MLA via `LLM_ARCH_DEEPSEEK2` — K3 has 24 full-attention layers
- sigmoid-router grouped-topk MoE
- MXFP4, plus `MXFP4_R8` (landed 2026-07-28) — matches K3's native weight format
- DS4 / `HC_PRE` infrastructure (the analysis above explains why K3's AttnRes
  still needed its own composition)

The port added SiTU composition, the full-rank KDA gate, latent MoE, the MLA
output gate, `LLM_ARCH_KIMI_K3` plumbing, and the graph builder against ik's
monolithic `src/llama.cpp` `build_*` style. It was a re-implementation, not a
mainline cherry-pick; the numerical fixtures below are what made that safe.

### MXFP4 on-disk layout

For the conversion path (`compressed-tensors` `mxfp4-pack-quantized`):

```
<name>.weight_packed : U8 [rows, cols/2]   two e2m1 nibbles per byte, LOW nibble first
<name>.weight_scale  : U8 [rows, cols/32]  e8m0 exponent, value = 2^(byte - 127)
```

e2m1 magnitudes are `0, 0.5, 1, 1.5, 2, 3, 4, 6` (× sign). 4 bits + 8 bits per
32 = **4.25 bpw**. Moonshot QAT'd at exactly this format, so 4.25 bpw *is* full
fidelity — requantising below it is a real loss, not a free win.

---

## Using the harness

```bash
python3 k3_ops_oracle.py --check-torch     # validate the transcription vs PyTorch
python3 k3_ops_oracle.py --emit fixtures/  # write raw f32 arrays + manifest.json
python3 k3_ops_oracle.py --verify fixtures/ # re-derive, compare sha256
```

Fixtures are raw little-endian float32, C order, so a C test reads them with
`fread`. Shapes and hashes are in `manifest.json`, which is committed; the `.f32`
arrays are not, since `--emit` reproduces them byte-for-byte from a fixed seed.

`--verify` exists to catch an accidental edit to an op silently moving the
goalposts. The fixtures are a contract.

The completed implementation order, dependency-first:

0. `LLAMA_MAX_EXPERTS` 512 → ≥896, and audit what assumed 512
1. SiTU — self-contained, needed by every expert, easiest to validate
2. MLA output gate — three lines
3. AttnRes — check whether ik's DS4 `HC_PRE` lands first
4. full-rank KDA gate — the risk; do it with the fixture in hand
5. latent MoE — new code path
6. `LLM_ARCH_KIMI_K3` plumbing + graph builder + conversion

A trusted quant exists and the port is validated end to end. `UD-Q2_K_XL` is
still ~2.5 bpw over weights Moonshot already QAT'd at 4.25 bpw, so the quant
ceiling remains real even though the deployed model is useful.

## Historical viability experiment (completed)

`UD-Q2_K_XL` was downloaded (861 GB, `/models/Kimi-K3-UD-Q2_K_XL`) and tested
with this throwaway reference build before the production ik implementation was
written. It established that the fitting quant was coherent enough to port.

unsloth built their GGUFs against their own fork, and its
[PR #48](https://github.com/unslothai/llama.cpp/pull/48) says the full-size model
"loads and generates correctly across the four published quants." So:

```bash
git clone --depth 1 --branch kimi-k3-fullsize-vision \
    https://github.com/unslothai/llama.cpp ~/llama.cpp-k3
cd ~/llama.cpp-k3
cmake -B build -DGGML_NATIVE=ON -DGGML_CUDA=OFF -DLLAMA_CURL=OFF
cmake --build build --config Release -j "$(nproc)"
```

That branch is mainline #26185 (K3 text) + two commits of full-size fixes:
`n_expert_used` read per-layer so the dense prefix and MoE layers need not agree,
KV cache sized from the actual KDA-layer count instead of all 92 layers, and
expert tensors kept in source order to avoid a whole-layer-resident repack. It is
**mainline**, so it lacks ik's fused-MoE kernels — PP will be slower than a
finished ik port would give. That is fine: this measures *quality and viability*,
not final speed.

**RAM:** K3 needs ~861 GB mlocked; it cannot coexist with the GLM server's
~441 GB (1,302 > 1,133). Stop `glm-server` first, run K3 on a **different port**
(not 8080) from the dedicated build dir, and restart `glm-server` after. Nothing
about this touches `~/ik_llama.cpp` or the registry.

The completed ik port then supplied fused-MoE performance, parser/tool support,
and the regression results recorded at the top of this file.

The unsloth build remains useful as an independent reference when the ik fork's
logits or perplexity need to be compared against another implementation.
