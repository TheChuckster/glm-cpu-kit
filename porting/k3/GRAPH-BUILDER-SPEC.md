# build_kimi_k3: what the graph has to do

Working notes for the one piece still missing. Everything here was read out of
mainline's `src/models/kimi-k3.cpp` (PR #26185, head `pwilkin@a614fab1`) and
checked against ik at pin `6038941`. Written down rather than kept in a head
because the details below are exactly the ones that fail *silently*.

Current state: all 2573 tensors load, hparams parse, and the model dies at
`llama-build-context.cpp:2933` — the dispatch default — because
`build_kimi_k3()` does not exist. That abort is the only thing between here and
a first token.

---

## Correction: `ggml_hc_pre` is NOT mainline's `ggml_dsv4_hc_pre`

The plan and `README.md` both claimed AttnRes was "a call, not an
implementation" because ik gained `GGML_OP_HC_PRE` from the DeepSeek-V4 work and
mainline's K3 builder calls `ggml_dsv4_hc_pre`. **They are different operations
that happen to share a name fragment.**

```c
// mainline: plain weighted sum, reduces over ne1
ggml_dsv4_hc_pre(ctx, src /*[n_embd, n_ckpt, n_tok]*/, probs /*[n_ckpt, n_tok]*/)

// ik: Sinkhorn-normalised hyper-connection pre-op
ggml_hc_pre(ctx, x, scale /*[3]*/, bias /*[S*S+2S]*/, int S, int n_iters, float eps)
```

ik's asserts `scale->ne[0] == 3` and `x->ne[0] == S*S + 2*S`. Feeding it an
AttnRes stack would assert, and if it somehow didn't, the answer would be wrong.

**Do not use `ggml_hc_pre` for AttnRes.** Build the weighted sum from
primitives. `attn_res_block_size` is 12 over 93 layers, so there are at most
`ceil(93/12) = 8` banked checkpoints — an explicit accumulate over ≤8 tensors
costs `n_embd × 8 × n_tokens` FLOPs, which is nothing next to one MoE layer, and
it is provably correct.

---

## AttnRes (`res_mix` / `res_push`)

Runs at **three** kinds of site: before `attn_norm`, before `ffn_norm`, and once
more on the final output with `model.output_res_score`.

```
scores_c = sum_rows( rms_norm(resi[c], eps) * score_w )    for each banked ckpt c
scores_x = sum_rows( rms_norm(cur,     eps) * score_w )    the live residual
probs    = softmax( concat(scores_c..., scores_x) )        over the ckpt axis
out      = Σ_c resi[c] * probs[c]  +  cur * probs[last]
```

Three things that are silently wrong if you get them backwards:

1. **The RMSNorm takes no weight on the values.** `score_w` multiplies inside
   the *scoring* path only. The weighted sum at the end uses the **raw**,
   un-normalised tensors. Normalising the output is the obvious wrong turn.
2. **The live residual is scored but never banked.** It is concatenated onto the
   scores and multiplied back in separately, which keeps the bank append-only.
3. **Banking uses the RAW layer input**, not the mixed `cur`.

### The banking rule, which is the subtle one

```cpp
ggml_tensor * prefix_sum = inpL;
cur = res_mix(prefix_sum, layer.attn_res_score, ...);

bool banked = false;
if (il % res_bs == 0) { res_push(prefix_sum); banked = true; }   // RAW input

... attn_norm, then KDA or MLA ...

// On a banking layer the residual RESTARTS from the attention output alone.
prefix_sum = banked ? cur : ggml_add(prefix_sum, cur);
```

That `banked ? cur : add(...)` is the whole point of the architecture — on a
checkpoint layer the running residual is dropped (it has just been banked) and
the stream restarts. Writing the usual `prefix_sum + cur` unconditionally
produces a model that runs and generates plausible, slightly-worse text.

The FFN site always adds normally: `prefix_sum = ggml_add(prefix_sum, cur)`.

---

## SiTU

Replaces SwiGLU everywhere — dense FFN, routed experts, shared experts.

```cpp
a = beta * tanh(gate / beta) * sigmoid(gate);     // situ_beta        = 4.0
u = linear_beta * tanh(up / linear_beta);         // situ_linear_beta = 25.0
out = a * u;                                      // skip the up transform if linear_beta <= 0
```

Composed from `ggml_scale`/`ggml_tanh`/`ggml_sigmoid`/`ggml_mul` — no new kernel.
As `beta → ∞` this becomes SiLU, so implementing it as SwiGLU looks fine and is
wrong; the tanh soft-clip is the bound K3's MXFP8 QAT trained against.

ik's `llm_build_ffn` takes an `LLM_FFN_*` activation enum and has no SiTU, so the
FFN and both expert paths need the multiply written out rather than delegated.

---

## Layer loop

```
for il in 0..92:
    prefix_sum = inpL
    cur = res_mix(prefix_sum, attn_res_score)
    if il % 12 == 0: res_push(prefix_sum); banked = true
    cur = rms_norm(cur, attn_norm)
    cur = is_recurrent(il) ? KDA(cur) : MLA(cur)
    prefix_sum = banked ? cur : prefix_sum + cur

    cur = res_mix(prefix_sum, ffn_res_score)
    cur = rms_norm(cur, ffn_norm)
    cur = (il < n_layer_dense_lead) ? dense_situ_ffn(cur) : latent_moe(cur)
    prefix_sum = prefix_sum + cur
    inpL = prefix_sum

cur = res_mix(inpL, model.output_res_score)
cur = get_rows(cur, inp_out_ids)
cur = rms_norm(cur, output_norm)
logits = mul_mat(model.output, cur)
```

`is_recurrent(il)` comes from `hparams.recurrent_layer_arr`, already populated
from the per-layer `head_count_kv` array (0 = KDA). Do **not** infer it from a
stride: layer 92 breaks the every-fourth pattern.

---

## MLA layers (24 of them)

Mainline notes **"K3 MLA is nope-only, so there is no position input"** — there
is no RoPE applied in the MLA path despite `rope.dimension_count = 64` and
`rope.freq_base = 10000` being present in the GGUF. Verify against the reference
before wiring any rope call; a spurious RoPE is another silent-quality bug.

Otherwise DeepSeek-style, which ik already has in `build_deepseek4.cpp`:
`q_a → q_a_norm → q_b`, `kv_a_mqa → kv_a_norm → k_b/v_b`, then `wo`. Widths come
from the new `n_embd_head_k_mla` (192) / `n_embd_head_v_mla` (128) hparams, NOT
from `n_embd_head_k(0)`, which is the compressed 576.

Plus the K3-only bit: a sigmoid **output gate** from `attn_gate`
`[n_embd, n_head*v_mla]`, computed from the **layer input** (the `cur` going into
the layer, i.e. post-`attn_norm`) and multiplied into the attention output
*before* `wo`. Gating the attention output instead of the layer input is a
plausible-looking silent bug; the `[n_embd, ...]` input width is the giveaway.

---

## KDA layers (69 of them)

ik's `delta_net::build_layer_attn_linear` is Qwen3-Next-shaped and cannot be
reused wholesale — Qwen3-Next fuses q/k/v/z into one `ssm_in` projection and uses
a single `ssm_conv1d`; K3 has separate `attn_q/k/v` plus **three** separate
`ssm_conv1d_{q,k,v}`. What IS reusable is the core:
`delta_net::build_fused_delta_net(ctx0, q, k, v, g, beta, state, il, cb, repeat_type)`.

Per layer:
- `q,k,v = mul_mat(attn_{q,k,v}, cur)` → each `[n_head*head_dim] = 12288`
- each goes through its own causal conv1d, kernel 4, with its own third of the
  conv state (mainline's `kimi_k3_conv1d` takes a `qkv` index selecting the third)
- gate: `g = f_b(f_a(cur))` → `[12288] = [H=96, K=128]`, then
  `g = g + ssm_dt.bias` (also `[12288]`), then
  `g = kda_gate_lower_bound * sigmoid(exp(ssm_a) * g)` with lower bound **-5.0**
  and `ssm_a` **per-head [96]** broadcast across the 128 channels
- `beta = sigmoid(mul_mat(ssm_beta, cur))` → `[96]`, one per head
- the recurrence consumes `g` as a full `[B,T,H,K]` tensor — this is the
  per-channel gate the `ggml_delta_net` change already landed for
- output gate `ssm_g` `[n_embd, 12288]`, then `ssm_norm` `[128]`, then `wo`

The state layout ik expects is `[S_v, S_v*H_v, 1, n_seqs]` and the gate must be
`[n_tokens, head_dim, n_head, n_seqs]` for the per-channel path (`ne[1] > 1` is
what selects it; see the assert in `ggml_delta_net`).

---

## Latent MoE (layers 1-92)

Three widths in one block. `n_expert_latent` = 3584, `n_embd` = 7168.

```
probs = sigmoid(mul_mat(ffn_gate_inp, cur)) + exp_probs_b      # router at FULL width
topk  = 16 of 896, group-limited (expert_group_used_count = 1), weights normalised
h     = mul_mat(ffn_routed_down, cur)                          # 7168 -> 3584
h     = rms_norm(h, ffn_routed_norm)
y     = moe_situ(h, topk)                                      # experts at 3584, ff 3072
y     = mul_mat(ffn_routed_up, y)                              # 3584 -> 7168
out   = y + shared_experts_situ(cur)                           # shared see the ORIGINAL cur
```

Note `ffn_routed_norm` sits **between** the down-projection and the experts.
The shared experts run at full width (`[n_embd, 6144]`, 6144 = 2 × 3072) and take
the block input, not the down-projected tensor.

`expert_weights_norm = true`, `expert_gating_func = 2` (sigmoid),
`expert_weights_scale = 1.0`.

ik's `llm_build_std_moe_ffn` assumes one width throughout and a fixed activation,
so this is a new code path rather than a call with different arguments.

---

## Debug loop

`llama-sweep-bench -m <shard1> -c 512 --dry-run -t 8` skips reading tensor DATA:
the whole load path runs in seconds instead of the ~3 minutes it takes to pull
861 GB off NVMe. Use it for every iteration that is not actually generating.

For numerical checks there is a reference build of these exact weights on the box
at `~/llama.cpp-k3` (unsloth's fork). Its `k3-run.sh` passes `--mlock`, which
will fail — the memlock ulimit is 141 GB against an 861 GB model. Drop the flag.

Op-level fixtures for situ / attn_res / mla_output_gate / kda_gate live in
`fixtures/`; `k3_ops_oracle.py --verify fixtures/` checks them.

---

## KDA gate: two details that invalidate the oracle's docstring

Read out of mainline's builder, and both change the arithmetic.

### 1. `ssm_a` ships pre-transformed

The GGUF's `ssm_a` is **not** `A_log`. The converter folds it, so the stored
value is `-exp(A_log)`, i.e. `exp(A_log) == -ssm_a`. `k3_ops_oracle.py`'s
`kda_gate()` takes raw `A_log` and computes `exp(A_log)` itself — correct as a
transcription of Moonshot's Python, wrong as a description of the GGUF. Anything
validating the C against that fixture must feed it `A_log`, not `ssm_a`.

### 2. `gate_lower_bound` is not a clamp — it selects a different activation

```
unset (Kimi-Linear):  g = -exp(A_log) * softplus(f_b(f_a(x)) + dt_bias)
set   (K3, -5.0):     g = lower_bound * sigmoid(exp(A_log) * (f_b(f_a(x)) + dt_bias))
```

Reading `-5.0` as a floor and clamping to it produces a completely different
gate. In code, with `A = ssm_a = -exp(A_log)` reshaped `[1, n_head, 1]`:

```c
g = mul_mat(ssm_f_b, mul_mat(ssm_f_a, cur));
g = add(g, ssm_dt_b);
g = reshape_3d(g, head_dim, n_head, n_tokens);
g = mul(g, A);                       // broadcast per-head over head_dim
g = sigmoid(scale(g, -1.0f));        // the double negation gives sigmoid(+exp(A_log)*...)
g = scale(g, gate_lower_bound);      // -5.0
```

`A` broadcasts a **per-head** scalar across the 128 channels, while the values it
multiplies are per-channel — which is exactly why the gate reaching the
recurrence is full-rank even though `ssm_a` is `[96]`.

### 3. The gate layout differs between ik and mainline — permute required

```
mainline g: [head_dim, n_head,  n_seq_tokens, n_seqs]     ne0 = channel
ik       g: [n_tokens, head_dim, n_head,      n_seqs]     ne0 = TOKEN
```

ik's assert is `g->ne[0] == n_tokens && (g->ne[1] == 1 || g->ne[1] == S_v)`.
So the builder must permute mainline's layout before calling
`build_fused_delta_net`. Getting this wrong reads gate values transposed —
runs fine, silently wrong.

### 4. Q and K are L2-normalised

`ggml_l2_norm(Q, eps)` and the same for K, **not** rms_norm. ik's delta_net
kernel also normalises internally (`q_norm_inv`/`k_norm_inv`), so check whether
that would double-normalise before wiring both.

---

## MLA output gate

Confirmed against the reference: `g = sigmoid(mul_mat(attn_gate, cur))` where
`cur` is the **normed** layer input, applied to the attention output, and only
then the output projection. So `wo` must NOT be handed to the attention helper -
it is applied after the gate.

## MLA layer, confirmed details

- **No RoPE.** The nope/rope split still exists structurally (192 = 128 nope + 64
  rope) and `k_pe`/`q_pe` are still sliced out and concatenated, but no rotation
  is applied — the converter asserts `mla_use_nope`. Do not add a rope call just
  because the GGUF carries rope keys.
- Absorbed path (what K3 ships, since `wk_b`/`wv_b` are present):
  `q_nope` is permuted, multiplied by `wk_b`, permuted back, then concatenated
  with `q_pe` to form Q. K is `concat(kv_cmpr_3d, k_pe)` and V is `kv_cmpr_3d`
  alone, with `wv_b` passed to the attention helper as the value-expansion.
- `kv_a_norm` is applied to the compressed KV **after** slicing off `k_pe`.
- ik's equivalent machinery lives in `src/graphs/build_deepseek2.cpp` and
  `build_deepseek4.cpp`; the shapes above map onto it, but ik's `build_attn`
  signature differs from mainline's and is what the port has to target.

---

## MLA in ik: what the remaining work actually is

ik's reusable MLA path is `llm_build_context::build_deepseek2_layer_attention`
(`src/graphs/build_deepseek2.cpp`), non-TP counterpart of
`build_deepseek2_tp_attention`. It cannot be called as-is for K3, for three
reasons, each of which has to be handled:

1. **It derives widths from `hparams.n_embd_head_k(0)`**, which for K3 is 576 —
   the compressed MQA width — not the 192 the projections use. K3 needs the
   `n_embd_head_k_mla` / `n_embd_head_v_mla` fields added in this port.
2. **It applies RoPE** from `inp_pos` / `rope_cache`. K3 is nope-only.
3. **It applies `wo` inside.** K3 has to gate the attention output first, so the
   projection must happen after.

**The absorbed path is forced, not chosen.** `wk_b` ships as
`[n_embd_head_qk_nope, kv_lora_rank, n_head]` = `[128, 512, 96]`, and
`ggml_mul_mat(a, b)` needs `a->ne[0] == b->ne[0]`. So `wk_b` can only multiply
something 128 wide — i.e. `q_nope` — giving the absorbed form. Producing
`k_nope` from `kv_cmpr` (512 wide) instead would need the transpose, which ik
only materialises as `wk_b_pp` in `llm_prepare_mla`. Hence:

```
q_nope    [128, n_head, n_tok] -> permute -> [128, n_tok, n_head]
q_absorbed = mul_mat(wk_b, q_nope)          -> [512, n_tok, n_head] -> permute back
Q = concat(q_absorbed, q_pe, 0)             -> [576, n_head, n_tok]
K = concat(kv_cmpr_3d, k_pe, 0)             -> [576, 1, n_tok]     (MQA: one head)
V = kv_cmpr_3d                              -> [512, 1, n_tok]
```

with `wv_b` `[512, 128, 96]` doing the value expansion afterwards.

### The KV cache: use ik's `-mla` path, not `llm_build_kv`

`attention.value_length = 74` is **junk** — it matches neither the 512 latent nor
the 128 per-head value width, and the standard MLA convention would put 512
there. It is never read, because K3 does not use a conventional K/V cache at all.

Mainline's builder selects `build_inp_mem_hybrid_k()` when `hparams.is_mla()`,
i.e. a **K-only** cache: the 576-wide compressed KV (`kv_lora_rank` 512 + rope
64) is cached once per token and V is taken from the same rows, with `wv_b`
expanding it at use. There is no separate V cache to size, which is exactly why
`value_length` was free to be nonsense.

So the target in ik is the `mla_attn >= 1` path that `build_deepseek2_*` already
uses, which caches the compressed form in `kv_l` — **not** `llm_build_kv`, which
would try to allocate an `n_embd_v_gqa`-wide V cache from that bogus 74.

Note also that `head_count_kv` is the per-layer 0/1 array, so anything deriving
KV geometry from it per layer sees 0 on the 69 KDA layers; the cache must only be
allocated for the 24 attention layers.

Until that path is wired the MLA layer aborts explicitly rather than filling a
mis-sized cache and producing quiet garbage.

---

## Status: runs end to end, output still incoherent

All 93 layers execute. `llama-cli`, 64 threads, `-fa 1 -mla 3`, no `--mlock`:

```
prompt eval  5 tokens @ 11.13 tok/s
eval        32 tokens @  3.60 tok/s
```

3.6 tok/s is far below the ~10-20 the sizing predicted, but speed is not worth
chasing until the output is right — the scalar delta-net path is in use (the
fused AVX-512 kernel declines per-channel gates) and that alone explains a lot.

### What the AttnRes bisect says

`KIMI_K3_NO_ATTNRES=1` swaps AttnRes for a plain residual add. Both settings
produce garbage, but *different* garbage:

```
with AttnRes:     " safety relation capital of of //  . the 1. The erset the"
without AttnRes:  "表格 Mok delegationolomammadammadomadomad，，，，，，"
```

With AttnRes the output at least echoes the prompt ("capital of") and stays in
English; without it, it collapses into repeated CJK. So AttnRes is contributing
something structurally right and is **not** the sole bug. Keep it on while
hunting elsewhere.

### Ruled out

- **Conv SiLU.** K3 does apply `ggml_silu` after the conv (`kimi_k3_conv1d`
  ends with it), which is what ik's `build_qkv` does unconditionally. Match.
- **Double L2-norm.** `build_qkv` L2-normalises q/k and the delta-net kernel
  normalises internally too, but L2-normalising an already-normalised vector is
  idempotent, so this is harmless.
- **q/k/v and conv ordering.** `build_qkv` slices the conv output as q at 0,
  k at `key_dim`, v at `2*key_dim`; the builder concatenates in that same order.
- **Gate range.** `g = -5.0 * sigmoid(...)` gives `decay = exp(g)` in
  ~(0.0067, 1), which is a sensible forget range.

### Still to check, roughly in order of suspicion

1. **KDA gate orientation.** The per-channel permute and `ssm_a`'s pre-folded
   `-exp(A_log)` interact; a sign or axis error here degrades quality without
   crashing. The `fixtures/kda_gate_*` files exist precisely for this — wire a
   C-level check against them rather than reasoning about it.
2. **MLA `kq_scale`.** Currently `1/sqrt(n_embd_head_k_mla)` = `1/sqrt(192)`.
   Confirm against the reference; MLA scale conventions vary with whether the
   rope half is counted.
3. **`KQ_mask` shape for the absorbed path.** `build_inp_KQ_mask()` is used, but
   ik's deepseek2 builds masks specific to its MLA modes.
4. **Per-layer state indexing on a hybrid model.** The KV cache is allocated for
   all 93 layers while only 24 attention layers use `k_l` and 69 use `s_l`;
   worth confirming nothing aliases.

The cheapest next move is a logit diff against `~/llama.cpp-k3` on a one-token
prompt, layer by layer, rather than more reasoning from shapes.

### Debug log: three real bugs fixed, output still wrong

All found by re-reading the code against ik's conventions rather than by
bisecting, and all of the same species — nothing crashes, nothing asserts.

1. **The recurrent state was never reset.** The builder passed a constant
   `false` for `reset_state_local`. ik derives it from the batch
   (`batch.pos[0] == 0`). Output unchanged in practice, because the buffer
   happened to be zeroed — but it would have corrupted the second sequence.
2. **`inp_out_ids` narrowed inside the last layer.** Handing it to the final
   attention made `cur` one row while `prefix_sum` still had `n_tokens`, and
   `ggml_add` *broadcasts* rather than failing, since `n_tokens % 1 == 0`. Every
   prompt pass silently corrupted the last residual. Single-token decode was
   unaffected (`inp_out_ids` is null there), which is what hid it. Narrow once
   at the end, after the final AttnRes mix.
3. **The dense FFN added the residual twice.** `llm_build_ffn`'s `add_input`
   adds the block input to its own output; Qwen3-Next passes `true` because it
   lets the helper do the residual. K3 adds `prefix_sum` itself, so `true` meant
   layer 0's input landed twice. **This one changed the output**, confirming the
   graph is sensitive to it.

### Also ruled out

- **Expert gating enum.** ik's `LLM_EXPERT_GATING_FUNC_SIGMOID == 2`, matching
  the GGUF's `expert_gating_func = 2`. No mismatch.
- **`ssm_a` sign convention — inconclusive, not wrong.** The tensor is all
  negative (`-0.59, -1.18, -0.63, ...`), which is consistent with BOTH
  `-exp(A_log)` (mainline's stated folding) and a raw `A_log` that happens to be
  negative. Either way the gate keeps the same form and a sane decay range, so
  this is a quality-level difference at worst, not the dominant bug.
- **Expert tensor shapes.** `ffn_gate_exps [3584, 3072, 896]` and
  `ffn_down_exps [3072, 3584, 896]` match the GGUF exactly.

### The reference control is impractical as a fast oracle

`~/llama.cpp-k3` on the same quant ran **56+ minutes without finishing** 40
tokens. It is mainline, so no fused-MoE kernels, on 861 GB with DS4 co-resident.
A layer-by-layer logit diff against it is therefore an overnight-scale operation
per data point, not an interactive one. Two consequences:

- Prefer the op fixtures (`k3_ops_oracle.py`) over model-level diffing.
- If a model-level diff is needed, do it at **one token**, dumping intermediate
  tensors via the `cb` callback on both engines, not by comparing generated text.

### Fourth bug: the KDA output gate was SiLU

`delta_net::build_gated_output` applies `silu(z) * normed`. K3 applies
`sigmoid(z) * normed`. Since `silu(z) = z*sigmoid(z)`, reusing that helper
multiplied **69 of the 93 layers** by an extra factor of `z`.

Self-inflicted, and worth recording as a process lesson: the first version of the
KDA tail was hand-written with `sigmoid`, and replacing it with "the existing
helper that does the same thing" silently changed the activation. The helper is
correct for Qwen3-Next; it is simply not K3's gate. Reuse in this port has to be
checked op-by-op, not by shape compatibility — every substitution so far that
type-checked and ran has been wrong in a different way.

The reference, for the record:

```c
ggml_tensor * normed = build_norm(o, layer.ssm_o_norm, nullptr, LLM_NORM_RMS, il);
ggml_tensor * gated  = ggml_mul(ctx0, normed, ggml_sigmoid(ctx0, g2));
gated = ggml_cont_2d(ctx0, gated, d_inner, n_tokens);
cur   = ggml_mul_mat(ctx0, layer.wo, gated);
```

Fixing it changed the output, so the graph is sensitive there — but the result is
still incoherent, so at least one more defect remains.

### Verified correct (checked against ik's own code, not assumed)

- **beta**: ik's `build_beta_gate` returns it RAW at `[num_v_heads, 1, n_tok, 1]`,
  no sigmoid — the kernel does it. Matches what the builder passes.
- **gate arithmetic**: ik's own Qwen3-Next gate is `softplus(alpha + dt) * ssm_a`,
  which only makes sense if `ssm_a` is already `-exp(A_log)`. So K3's
  `sigmoid(scale(mul(g, ssm_a), -1)) * lower_bound` is the right translation.
- **gate layout end-to-end**: `[S_v, H_v, n_tok, n_seqs]` → detected per-channel →
  `permute(1,2,0,3)` → `[n_tok, S_v, H_v, n_seqs]`, which is what the kernel's
  `g_head_offset + t + col*n_tokens` indexing expects. Traced by hand.
- **KQ_mask**: `build_inp_KQ_mask()` is exactly what `build_deepseek2` uses.
- **MLA concat order**: ik puts rope FIRST in both Q and the cache row; mainline
  puts nope/lora first. Either is fine because Q and K agree, and the V view is
  taken at the matching offset.
- **wv_b expansion shapes**: traced through the flash-attn output to `[128, 96, n_tok]`.

### The reference is not a usable oracle at all

`~/llama.cpp-k3` has now run **69+ minutes** on 40 tokens without finishing.
Mainline has no fused-MoE kernels, so every token reads 16 experts x 92 layers
uncompressed. Treat it as unavailable: the op fixtures and hand-tracing are the
tools here.

---

## ROOT CAUSE FOUND: the scalar delta-net path was reading strided views

`build_fused_delta_net` permutes `v`, `g` and `beta` into the layout
`ggml_delta_net` wants, which leaves them as **strided views**. Only the fused
AVX-512 kernel can read those — `iqk_fused_delta_net` is handed `v`'s
`nb1/nb2/nb3` explicitly. The scalar reference path in
`ggml_compute_forward_delta_net_f32` casts `v_data` / `g_data` / `beta_data` to
plain `float *` and indexes them with computed offsets, so it requires genuinely
contiguous buffers.

**This never mattered before.** The fused kernel handles every per-head gate, so
the scalar path was effectively dead code in ik. Kimi-K3's per-channel gate makes
the fused kernel decline (its signature cannot express a full-rank gate), and the
scalar path then reads permuted memory as though it were contiguous — at full
speed, with no assert, producing fluent-shaped nonsense.

The fix is three `ggml_cont` calls on the per-channel path only, so nothing
changes for Qwen3-Next. `q` and `k` were already fine: `ggml_l2_norm` produces
fresh contiguous tensors, which is also why *their* asserts passed and hid how
close the other three were to being read wrong.

### Before and after, same prompt, same seed

```
before:  ",,,…,…,…,…,…,…,…,…,…,…,…"
after:   "1.5 million. The capital of Germany is 1.5 million. The capital of Italy is..."
```

## Current state

K3 runs on ik and produces **grammatical, on-topic English**:

| prompt | output |
|---|---|
| `Q: What is the capital of France? A:` | ` What is the capital of Belgium? B: What is the capital of Austria? C: ...` |
| `def fibonacci(n):` | ` fibonacci(n): fibn(n): fibn(n): ...` |
| `a train travels 60 km in 45 minutes, its speed in km/h is` | ` 10.9. In 45 minutes, the train is 60 km/h. ...` |

Syntax is right, the model tracks the prompt's pattern, and it is clearly doing
attention. It is also repetitive and factually wrong.

**Two explanations, not yet separated.** `UD-Q2_K_XL` is ~2.5 bpw over weights
Moonshot QAT'd at 4.25 bpw, and both this repo's own analysis and pwilkin on the
mainline PR ("very informationally dense, that's why it doesn't quant so well")
predicted it would land below GLM-5.2 Q4. So this may simply be what this quant
is. But residual porting bugs would look similar, and there is no working
reference on this box to distinguish them — mainline ran 69+ minutes without
finishing 40 tokens.

The honest next step is a perplexity number, which is quantitative and needs only
one forward pass per chunk: `llama-perplexity` on wiki.test.raw. A value in the
teens-to-thirties means the port is broadly right and the quant is the limit;
hundreds or more means bugs remain.

## Perplexity: 56.18 — working, but not clean

```
llama-perplexity -c 512 -b 512 -t 64 -fa 1 -mla 3 --chunks 8
[1]23.58 [2]32.17 [3]36.99 [4]44.95 [5]46.67 [6]49.81 [7]54.17 [8]56.18
Final estimate: PPL = 56.1774 +/- 3.83870
PP throughput: 34.58 tok/s over 4096 tokens
```

A finite, converging perplexity is itself a result: a model with a structurally
broken layer produces hundreds or NaN, not 56. Combined with grammatical output,
that says the port is broadly correct.

But 56 is high, and **the per-chunk curve rises monotonically** (23.6 → 56.2)
rather than fluctuating around a mean. That shape is the thing to chase next: it
is what state contamination across chunks looks like. `llama-perplexity` may not
restart positions per chunk, in which case the KDA recurrent state carries over
and degrades — `reset_state` fires on `batch.pos[0] == 0`, which would then only
be true for the very first chunk.

Cheap way to tell them apart: run the identical perplexity command against
DeepSeek-V4 (known-good on this box). If DS4's curve also climbs, it is the
harness or the data; if DS4's is flat, K3 is carrying state it should not.

Note also PP here is 34.58 tok/s against 12 tok/s in `llama-cli`, because
perplexity batches 512 tokens at a time — worth remembering before drawing
conclusions about speed from the CLI numbers.

### The DS4 control, and what it rules out

Same harness, same file, same command — the only difference is the model:

| chunk | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| **DS4** (no recurrent state) | 3.88 | 4.73 | 3.11 | 2.79 | 2.65 | 2.54 | 2.48 | **2.41** |
| **K3** (69 KDA layers) | 23.58 | 32.17 | 36.99 | 44.95 | 46.67 | 49.81 | 54.17 | **56.18** |

DS4 converges downward, which is the normal shape for a running mean. K3 climbs
monotonically, meaning each successive chunk is worse than the average so far.

**State contamination is ruled out.** Forcing `reset_state` on every batch
produced a **bit-identical** result — 56.1774, same per-chunk values — so the
state was already being cleared correctly at chunk boundaries. The climb is
intrinsic to the model on this data, not leakage.

That leaves two live explanations, still unseparated:

1. **The quant.** `UD-Q2_K_XL` is ~2.5 bpw over weights Moonshot QAT'd at
   4.25 bpw. This repo predicted before any code was written that it would land
   below GLM-5.2 Q4, and pwilkin on the mainline PR reported K3 "doesn't quant so
   well". A model degraded that far would plausibly do disproportionately badly
   on harder text, which is what a climbing curve looks like.
2. **A remaining defect** that hurts more on some content than others.

PPL 56 against DS4's 2.41 is a wide enough gap that I would not call the port
finished on the strength of "it generates English". The way to settle it is a
reference perplexity number for this exact quant — from the mainline PR thread
(`fairydreaming` ran one) or from a machine where mainline is fast enough to
finish. Comparing against *any* trustworthy number for `UD-Q2_K_XL` separates
"this is what 2.5 bpw K3 is" from "there is still a bug".

### The reference number: PPL should be ~1.5, not 56

`fairydreaming` on mainline PR #26185 (2026-08-01), Kimi-K3-Q2_K, same wikitext:

```
llama-perplexity -c 8192 -b 8192 -ub 8192 -fit off -fa 1
[1]1.2461,[2]1.2755,[3]1.5372,[4]1.4771,[5]1.4036,[6]1.3488,[7]1.5159,...
Final estimate: PPL = 1.5499 +/- 0.00478
```

**This settles it: the port still has a defect.** 56.18 against a reference 1.55
is a ~36x gap. Context size does not explain it — DS4 on this box at the same
`n_ctx=512` gives 2.41, so short context is not what inflates a healthy model to
56. Nor does the quant: `UD-Q2_K_XL` is if anything a *better* quant than the
plain `Q2_K` measured above.

It also retires the curve-shape lead entirely. **The reference curve climbs too**
(1.2461 → 1.5499). Monotonic climb is normal for this model on this data, which
is consistent with the forced-reset experiment finding nothing. The *value* is
wrong, not the shape — a much better-posed target.

So the model is running, generating English, and numerically wrong by a wide
margin. Something is degrading it heavily without breaking it. Given the layer
counts, the most valuable next experiments are the ones that isolate a *family*:

- Get a per-layer-type signal. The earlier skip-switch attempt aborted because
  zeroing the KDA layers left `inp_s_seq_qnext` unreferenced; keep a dummy
  consumer alive and it becomes usable.
- Compare `n_ctx=512` vs a larger context. A defect in the MLA cache or mask
  would scale differently with context length than one in the KDA recurrence.
- Run the op fixtures through the real ik ops. That is what they exist for, and
  it is the only check here that does not need an 861 GB load per iteration.

### Context-length scaling: the defect is mostly length-independent

| | n_ctx=512 | n_ctx=8192 | reference (n_ctx=8192) |
|---|---|---|---|
| PPL | 56.18 | **34.07** | **1.55** |

Going to the reference's own context length recovers some of the gap (56 → 34)
but leaves ~22x. Two things follow:

- **Not primarily the MLA cache or mask.** A defect there — a mis-sized cache
  view, a wrong mask, positions applied incorrectly — would scale strongly with
  context length. This barely does.
- **So it lives in per-token computation**, which is where the search should now
  concentrate: the KDA layers (69 of 93, and by far the most novel code here),
  the latent MoE, AttnRes, or situ.

The residual length sensitivity that *does* exist (56 → 34) is consistent with
ordinary context benefit rather than a bug signature — a healthy model improves
with more context too.

### Ranked suspects, given everything now known

1. **KDA.** 69 of 93 layers, entirely new code, and the component whose scalar
   execution path had never been exercised in ik before this port (see the
   contiguity bug). The gate arithmetic, the conv fusion and the state handling
   have each been hand-verified but never checked numerically.
2. **AttnRes.** Hand-written against an op ik does not have. Disabling it makes
   output worse, so it is doing something right, but "better than nothing" is a
   long way from correct.
3. **Latent MoE.** The three-width structure is unusual and the router runs at a
   different width from the experts.
4. **situ.** Simplest of the four and used everywhere, so an error would be
   pervasive — but it is four lines and reads correctly.

The one check that does not cost an 861 GB load per iteration is running
`fixtures/*.f32` through the real ik ops and diffing against the oracle. Given
four candidates and ~15 minutes per model-level experiment, that harness is now
clearly worth building before more guessing.

### All four op compositions verified against the oracle

`verify_compositions.py` replays each builder's exact ggml op sequence in numpy
and diffs it against `k3_ops_oracle.py`. Seconds, no model load:

```
situ               max|diff| = 1.79e-07   ok
attn_res           max|diff| = 1.19e-07   ok
mla_output_gate    max|diff| = 1.19e-07   ok
kda_gate           max|diff| = 4.77e-07   ok
```

All four match to float32 precision, which settles several things that had only
been argued from shapes:

- **situ** is right, including which branch gets the `beta` clip and which gets
  `linear_beta`.
- **attn_res** is right: the RMSNorm carries no weight, the score vector is the
  pre-folded one, and the weighted sum runs over the RAW tensors.
- **The KDA gate is right**, including the `ssm_a == -exp(A_log)` convention and
  the per-head broadcast over 128 channels. The `scale(-1)` double negation
  reproduces the oracle exactly, so that reading was correct.
- **The MLA output gate** is a plain sigmoid product, as used.

So the defect is **not** in the four new ops. What that leaves, all of it
integration rather than arithmetic:

1. **The KDA recurrence integration** — the conv fusion into `build_qkv`'s layout,
   the state read/write, and whether the per-channel gate survives the trip
   through `ggml_delta_net` into the scalar kernel. The gate *math* is now proven;
   its *delivery* is not.
2. **The AttnRes banking rule.** `verify_compositions.py` tests the mix, not the
   loop-level `banked ? cur : add(prefix_sum, cur)` decision or the every-12-layers
   checkpoint schedule. That logic is verified only by reading.
3. **The latent MoE plumbing** — the three-width down/norm/experts/up path and the
   shared experts consuming the block input.
4. **The MLA absorbed path** — `wk_b`/`wv_b` and the cache row layout.

Given how many bugs in this port have been delivery-not-arithmetic (the strided
views, the `ggml_add` broadcast, the SiLU-for-sigmoid substitution), (1) and (2)
are where to look next.
