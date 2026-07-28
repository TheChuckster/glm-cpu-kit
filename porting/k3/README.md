# Porting Kimi K3 to ik_llama.cpp

Groundwork for adding the `kimi-k3` architecture to ik_llama.cpp. Nothing here
runs K3 — this is the reference material and the numerical harness you check an
implementation against, assembled while the ecosystem catches up.

Status as of 2026-07-28 (K3 is one day old):

| | |
|---|---|
| ik_llama.cpp | no `kimi-k3` arch |
| mainline llama.cpp | unmerged PR [#26185](https://github.com/ggml-org/llama.cpp/pull/26185), text-only |
| trusted GGUF quant | none published (unsloth repo is a README; ubergarm has no K3 repo) |

Track both with `glm-model upstream`.

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
fully RAM-resident under `--mlock`, reading ~16.4 GB/token at memory bandwidth
rather than storage bandwidth — **~10–20 tok/s**, in the same class as the
GLM-5.2 Q4 already running here (18–25 tok/s).

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

**The porting problem in one line:** K3 ships `A_log` with shape `[128] = [K]` —
one decay per **channel**, broadcast across heads (`use_full_rank_gate: true`).
Qwen3-Next's gated DeltaNet, which is what ik's `ggml_delta_net` implements, has
one **scalar** decay per head.

So the gate entering the recurrence is a full `[B,T,H,K]` tensor rather than
`[B,T,H]`, and `ggml_delta_net`'s inner loop must broadcast a per-channel decay.
This is the single highest-risk change in the port. The fixture covers both the
per-channel/lower-bound path (K3) and the per-head/softplus path
(Kimi-Linear-48B), so one kernel can be proven to serve both.

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

## What ik already has

The port is tractable because the genuinely hard infrastructure is done:

- `ggml_delta_net` — gated delta-rule linear attention (Qwen3-Next)
- `ggml_ssm_conv` — the short convolution KDA needs (kernel size 4)
- hybrid recurrent+attention KV cache: per-sequence state slots, save/restore,
  mixed-batch handling. The gnarliest part, and it exists.
- MLA via `LLM_ARCH_DEEPSEEK2` — K3 has 24 full-attention layers
- sigmoid-router grouped-topk MoE
- MXFP4, plus `MXFP4_R8` (landed 2026-07-28) — matches K3's native weight format
- DS4 / `HC_PRE`, which mainline's PR reuses for AttnRes, landing now

Still to write: SiTU, the full-rank KDA gate, latent MoE, the MLA output gate,
`LLM_ARCH_KIMI_K3` plumbing, and a ~600-line graph builder — rewritten against
ik's monolithic `src/llama.cpp` `build_*` style rather than mainline's
`src/models/*.cpp`. That makes it a re-implementation, not a cherry-pick.
Mainline's version is 1,313 lines from an experienced contributor.

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

Suggested order of work, dependency-first:

1. SiTU — self-contained, needed by every expert, easiest to validate
2. MLA output gate — three lines
3. AttnRes — check whether ik's DS4 `HC_PRE` lands first
4. full-rank KDA gate — the risk; do it with the fixture in hand
5. latent MoE — new code path
6. `LLM_ARCH_KIMI_K3` plumbing + graph builder + conversion

Do not start before a trusted quant exists to measure against. Until then the
port cannot be validated end-to-end, and the quality question that decides
whether any of this is worth running stays open.
