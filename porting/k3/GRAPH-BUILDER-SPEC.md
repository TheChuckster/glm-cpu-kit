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

**CORRECTION — this was wrong, and it cost 92 layers.** `ffn_routed_norm` does
NOT sit between the down-projection and the experts. It normalises the expert
**output**:

```
down (7168 -> 3584)  ->  experts  ->  ffn_routed_norm  ->  up (3584 -> 7168)
```

Implementing the misreading above put the norm on the expert *input*. Fixing it
took perplexity from 56.18 to **23.86**.
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

### PPL 56.18 -> 23.86: ffn_routed_norm was on the wrong side of the experts

| | chunk 1 | final (8 chunks, n_ctx=512) |
|---|---|---|
| before | 23.58 | 56.18 |
| after | **7.97** | **23.86** |
| reference (n_ctx=8192) | 1.25 | 1.55 |

Still ~15x off, so more remains — but this confirms the method. Both of the last
two real bugs (the SiLU-for-sigmoid gate, and this) were found by reading the
reference implementation line by line and diffing it against the builder, not by
reasoning about shapes or by model-level bisection.

Worth being explicit about the failure mode, since it recurred: **the notes in
this file were themselves wrong**, and the code faithfully implemented the wrong
note. Deriving a claim once and then trusting it is how that happens. Anything
here not backed by a diff against the reference or a passing fixture should be
treated as a hypothesis.

Components still not diffed line-by-line against the reference:
- `build_kda_layer` (the conv fusion, the state, the ordering around the gate)
- `build_mla_layer` (the absorbed path)
- the layer loop's interaction with `n_layer_dense_lead` and the final output head

### The delta-net kernel is verified correct

`test_delta_net_gate.c` is self-validating and needs no oracle: a per-channel
gate whose channels all hold the same value is mathematically identical to a
per-head gate holding that value, so the two paths must agree.

```
g = -0.25   max|per_head - per_channel| = 0.000e+00   ok
g = -1.00   max|per_head - per_channel| = 0.000e+00   ok
g = -3.00   max|per_head - per_channel| = 0.000e+00   ok
```

Exact agreement. This exercises the real C kernel — the decay-first refactor, the
per-channel indexing, the `g_chan_stride` arithmetic — which neither hand-tracing
nor the numpy composition checks reached. Milliseconds, no model load.

### `ssm_a` is the pre-folded `-exp(A_log)`, measured

Both readings produce an all-negative tensor, so the shipped values could never
settle it, and unsloth built this GGUF with their own fork rather than the
mainline converter whose comment documents the folding. Measured instead:

| reading | chunk 1 | PPL |
|---|---|---|
| `ssm_a == -exp(A_log)` (current) | 7.97 | **23.86** |
| `ssm_a == raw A_log` | 56.72 | 148.75 |

The folded reading is right by a factor of six.

### Where this leaves the remaining gap

Verified correct, each by a method that could have failed: the four op
compositions (numpy vs oracle), the delta-net kernel including the per-channel
gate (self-consistency, exact), the `ssm_a` convention (measurement), the
AttnRes banking rule, the KDA/MLA/output-head structures, and `build_output`'s
norm type (all line-by-line against the reference).

Ruled out with evidence: state contamination, the curve shape, quant
degradation, and the MLA cache/mask.

So the residual ~15x is not in any component checked so far, and the techniques
that found seven bugs are now exhausted. The next honest step is
intermediate-tensor comparison — dumping the `cb` callback layer by layer on
both engines and finding the first divergence. That requires the reference to be
runnable at one-token scale, which it is (a single forward pass, not 40 tokens),
so it is feasible even at mainline's speed. That is the recommended next move,
and it is a different kind of work from everything above.

### Neither attention family is broken

`KIMI_K3_MUTE_KDA` / `KIMI_K3_MUTE_MLA` build each layer as normal — so
`inp_s_seq_qnext` and the caches stay referenced and the graph allocates — then
zero that family's contribution to the residual. (The earlier *skip* switches
aborted precisely because they left the input tensor unreferenced.)

| | PPL (4 chunks, n_ctx=512) |
|---|---|
| baseline | **16.49** |
| KDA muted (69 layers) | 44,956 |
| MLA muted (24 layers) | 244.8 |

The logic here is that a family producing noise would be *worse than nothing*:
muting it would improve perplexity. Neither does. Muting KDA is catastrophic
(2,700x) and muting MLA is 15x worse, so both are carrying substantial correct
signal, roughly in proportion to their layer counts.

That is a real constraint on what is left. The defect is not a broken attention
family; it is something subtler, distributed, or outside both — the AttnRes
schedule interacting with 93 layers, the MoE, or an accumulation effect.

### Everything verified so far, and by what method

| component | method | result |
|---|---|---|
| situ, attn_res, mla_gate, kda_gate | numpy vs oracle | exact |
| delta-net kernel, per-channel gate | self-consistency (equal channels ≡ per-head) | exact, 0.000e+00 |
| `ssm_a` convention | measurement (23.86 vs 148.75) | folded `-exp(A_log)` |
| KDA / MLA / output-head structure | line-by-line vs reference | match |
| AttnRes banking rule | line-by-line vs reference | match |
| `build_output` norm type | source | RMS, correct |
| KDA and MLA families | mute-and-measure | both contributing |
| state contamination | forced reset | bit-identical, ruled out |
| MLA cache/mask | context-length scaling | ruled out |
| quant degradation | reference used a *worse* quant | ruled out |

Seven bugs were found and fixed along the way. The residual gap survives all of
the above, which is genuinely useful information: it is not in any single
component, and it is not any of the things that were cheap to check.

### AttnRes is essential and working

| | PPL (4 chunks, n_ctx=512) |
|---|---|
| with AttnRes | **16.49** |
| plain residual add | 21,277 |

Three orders of magnitude. The hand-rolled AttnRes — the component with no ik
precedent, built from primitives because `ggml_hc_pre` turned out to be a
different op — is carrying the model. Earlier this was only judged by eyeballing
generated text; this is the quantitative version, and it clears it.

## Summary of state

Everything checkable has been checked and passes:

- four op compositions, exact against the oracle
- the delta-net kernel's per-channel gate, exact by self-consistency
- the `ssm_a` convention, settled by measurement (6x)
- KDA, MLA, output-head and AttnRes structures, line-by-line against the reference
- both attention families and AttnRes, each shown essential by mute-and-measure
- state contamination, MLA cache/mask, curve shape and quant, all ruled out

Seven bugs found and fixed. PPL went 56.18 → 23.86 (8 chunks) and output went
from `,,,…,…` to grammatical English.

**The residual gap survives all of it.** That is the honest position: the defect
is not in any component that can be isolated by composition testing,
self-consistency, source diffing, or ablation — all four techniques are now
exhausted.

One caveat worth stating plainly about the target. The reference 1.55 was
measured at `n_ctx=8192` on a `Q2_K` conversion by a different converter, and
this port's own 8192 run gives 34.07, so the honest gap is ~22x on the closest
comparison available rather than the ~36x a naive 56-vs-1.55 reading suggests.
It is still far too large to attribute to quantisation.

The remaining technique is layer-by-layer intermediate comparison: instrument the
`cb` callback on this build and on `~/llama.cpp-k3`, run ONE forward pass on each
(feasible even at mainline's speed, unlike generating tokens), and find the first
tensor that diverges. That localises the defect directly instead of inferring it,
and it is the correct next investment.

### Residual-stream profile: the banking schedule fires correctly

`llama-eval-callback` dumps intermediate tensor values for one forward pass, so
the residual magnitude can be read across all 93 layers with no reference engine
required. First element of `l_out-N`:

```
L0  0.014   L6  0.525   L12 0.033 <-   L18 0.170   L24 0.0007 <-  L30 0.341   L36 0.0011 <-
L1  0.010   L7  0.584   L13 0.087      L19 0.013   L25 0.0031     L31 0.304   L37 0.0083
L2  0.058   L8  0.747   L14 0.970      L20 0.240   L26 0.0034     L32 0.325   L38 0.061
...         L10 3.217   ...            L23 1.863   ...            L35 2.107   L39 0.080
```

A clean sawtooth: the stream accumulates for twelve layers, then collapses at
**12, 24, 36** — exactly `il % attn_res_block_size == 0`. That is the banking
rule working as the reference specifies ("on checkpoint layers it is banked into
res_stack and restarts from the attention output alone"), confirmed dynamically
rather than by reading the code. The schedule, the modulus and the restart
semantics are all correct.

It also shows `state_reset-0` in the graph, independently confirming the
recurrent state is cleared at sequence start.

So this diagnostic did not find the defect either — but it is the right tool and
costs one forward pass. The natural extension is to run the identical dump on
`~/llama.cpp-k3` and diff the two profiles layer by layer; the first layer whose
magnitude diverges is where to look. That is now a mechanical comparison rather
than an open-ended hunt, and it is the one avenue left.

### Layer-by-layer diff against the reference: gradual divergence, no broken layer

Both engines dumped with `llama-eval-callback` on the same prompt. Note the
reference prefixes its lines `common_debug_cb_eval:` where ik uses `ggml_debug:`;
the tensor names (`l_out-N`) are identical. First element of `l_out-N`:

| layer | this port | reference | |
|---|---|---|---|
| 0 | 0.0144 | 0.0142 | agree |
| 2 | 0.0582 | 0.0581 | agree |
| 3 | 0.0418 | 0.0409 | agree (MLA layer) |
| 5 | 0.2205 | 0.2154 | agree |
| 6 | 0.5247 | 0.4461 | ~18% |
| 7 | 0.5844 | 0.2748 | **2x** |
| 9 | 0.788 | 0.6201 | diverged |
| 10 | 3.2172 | 3.14 | (re-converges) |
| 12 | 0.0331 | 0.0662 | 2x |

**This changes the diagnosis.** The two engines agree closely through the first
several layers — including layer 3, a full-attention layer — and then drift
apart progressively. There is no layer where the value jumps by orders of
magnitude, which is what a structurally wrong layer would produce and what every
hypothesis so far assumed.

That is consistent with a small systematic difference compounding with depth
rather than one broken component, and it fits everything else already
established: each component verifies in isolation, both attention families are
essential, AttnRes is essential, and the ops are exact against the oracle.

The strongest candidate for "small difference, large downstream effect" in this
architecture is **expert selection**. With 896 experts and top-16 routing, a tiny
numerical difference in the router logits changes *which* experts fire, and a
different expert set produces a genuinely different output rather than a slightly
perturbed one. That compounds across 92 MoE layers and would not show up in any
single-component check.

Worth testing next, in order:
1. Dump the selected expert IDs per layer on both engines and diff them. If the
   sets diverge early, routing is the mechanism.
2. Check whether `exp_probs_b` is applied for SELECTION only (the noaux_tc
   scheme) rather than also scaling the weights, and whether ik's
   `llm_build_moe_ffn` does the same as mainline's `build_moe_ffn` on that point.
3. Compare `ffn_moe_logits` values directly - they are already named in the dump.

Caveat on method: this compares a single element per tensor, which is noisy.
Comparing an L2 norm per layer would be sounder, and is a small change to the
extraction.

### Expert-selection mechanism matches (bias is selection-only)

ik's `llm_build_moe_ffn` implements the noaux_tc scheme the same way mainline
does — the router bias affects **which** experts are chosen, not how their
outputs are weighted:

```c
ggml_tensor * selection_probs = probs;
if (exp_probs_b != nullptr) {
    selection_probs = ggml_add(ctx, probs, exp_probs_b);
    cb(selection_probs, "ffn_moe_probs_biased", il);   // same name as the reference
}
if (lctx.model.arch == LLM_ARCH_LLAMA4) {              // does NOT apply to K3
    selection_probs = logits;
}
```

So the mechanism is right. What has **not** been compared is the *outcome*: the
actual expert IDs selected per layer. The reference dump exposes these directly
as `ffn_moe_topk-N`, alongside `ffn_moe_probs`, `ffn_moe_probs_biased`,
`ffn_moe_weights` and `ffn_moe_weights_norm` — a complete picture of routing at
every layer, already captured in `/models/.ds4-run/ref-raw.log`.

**That is the next concrete step, and it is now cheap**: re-run this port's dump
capturing `ffn_moe_topk`, and diff the selected expert IDs against the reference
layer by layer. If they agree at layer 1 and diverge later, routing is following
the upstream drift rather than causing it. If they disagree at layer 1 — the
first MoE layer, where both engines still agree on the residual to within 1% —
then routing is the cause, and the gradual divergence downstream is its
consequence.

That is a genuine fork in the diagnosis, and one dump answers it.

### Method note for whoever picks this up

The two engines use different dump prefixes (`ggml_debug:` in ik,
`common_debug_cb_eval:` in mainline) but identical tensor names, so extraction
scripts need the prefix parameterised. `/models/.ds4-run/ref-raw.log` already
holds a complete reference pass and does not need regenerating — it costs ~14
minutes to reproduce.

## FOUND: expert selection diverges at layer 1

`ffn_moe_topk-1`, the 16 selected expert IDs at the first MoE layer, where the
two engines still agree on the residual to within 1%:

```
          this port                      reference
token 0   498, 767, 679, ... 210,656,485   498, 764, 545, ... 537,232,688
token 1   767, 679, 788, ... 210,656,592   373, 167, 585, ... 369,351,546
token 2   767, 788, 679, ... 485,656,210   644, 246, 676, ... 721,349,228
```

By the criterion set before running this: divergence at layer 1 means **routing
is the cause**, not a downstream consequence of drift. Every layer after this
inherits a different expert set, which is why the residual divergence compounds
from layer 6 onward rather than appearing suddenly.

### The second signal is more diagnostic than the first

Look at how *this port's* selections behave across tokens: tokens 1 and 2 pick
almost the same experts (767, 679, 788 recurring, plus 210/656/485 in the tail),
while the reference's three tokens share almost nothing. Token-to-token
stability like that is the signature of a **token-independent term dominating
the selection score** — i.e. `exp_probs_b` swamping the actual router logits.

Note also that token 0's top-1 agrees (498) and rank 2 nearly agrees (767 vs
764). So the logits are not garbage; they are being *outranked*.

### Where to look

The mechanism was already verified as correct (bias applied to selection only,
`LLM_ARCH_LLAMA4` override not applicable), so the fault is in the operands, not
the formula. Candidates, in order:

1. **Scale mismatch between `probs` and `exp_probs_b`.** ik computes
   `probs = sigmoid(logits)` in (0,1) then adds the raw bias. If K3's bias is
   stored on a different scale than ik assumes, it dominates. Compare
   `ffn_moe_probs-1` and `ffn_moe_probs_biased-1` values against the reference —
   both are already dumped by both engines.
2. **`ffn_moe_logits-1` itself.** Already captured in
   `/models/.ds4-run/mine-routing.raw` and `ref-raw.log`; diff them directly. If
   the logits agree and only the biased probs diverge, it is (1).
3. The routed-down input handed to the router. The builder passes the *normed*
   full-width tensor, matching the reference's `identity`, but this is worth
   confirming against the dump rather than by reading.

This is the first hypothesis in the entire debugging effort that both explains
the magnitude of the gap and predicts a specific, already-captured observable.

---

# SOLVED: PPL 23.86 -> 1.32

`hparams.expert_gating_func` **defaults to `LLM_EXPERT_GATING_FUNC_SOFTMAX`**,
and the K3 hparams loader never read the GGUF key. All 92 MoE layers routed with
softmax instead of the sigmoid K3 declares (`expert_gating_func = 2`).

```
mine:      ffn_moe_probs-1 = SOFT_MAX(logits)  ->  0.0013, 0.0009, 0.0015
reference: ffn_moe_probs-1 = SIGMOID(logits)   ->  0.1367, 0.1045, 0.1574
```

The damage was worse than a wrong activation. Softmax over **896** experts gives
probabilities near 1/896 ~ 0.001, while `exp_probs_b` holds values near 0.03 —
so `selection_probs = probs + bias` was dominated by the **token-independent**
bias. Expert selection barely varied per token, which is exactly the signature
visible in the `ffn_moe_topk` dumps two sections above: this port picked
767/679/788 for consecutive tokens where the reference picked entirely different
sets.

| | PPL |
|---|---|
| before | 23.86 |
| **after** | **1.3192 +/- 0.030** |
| reference (`Q2_K`, n_ctx 8192) | 1.5499 |

Below the reference, at a shorter context and with a better quant
(`UD-Q2_K_XL`), which is the ordering one would expect. The per-chunk curve now
**descends** (1.35 -> 1.32) like DS4's, instead of climbing.

## How it was found, and what that says about the method

The eval-callback diff. The router logits matched the reference to ~0.001 — so
every input was right — but the very next op was `SOFT_MAX` here against
`SIGMOID` there, sitting side by side in the two dumps.

Worth being precise about why the earlier checks all passed. Every one of them
verified a *component against its own specification*: the ops matched the
oracle, the kernel was self-consistent, the structures matched the reference
line by line, both attention families were essential. This bug lived in
**hparam loading**, upstream of all of it, and it made a correct component
compute the wrong thing. No amount of component verification finds that.

What did find it was comparing two engines' *actual execution* on the same
input. That is a different class of evidence, and in hindsight it should have
come earlier — it was recommended for several cycles while cheaper avenues were
exhausted first. The cheaper avenues did find seven real bugs, so the ordering
was not wrong, but the lesson stands: when every component verifies and the
whole is still wrong, the fault is in how the components are configured, not in
what they compute.

## Full bug list

1. `LLAMA_MAX_EXPERTS` 512 blocked loading at all
2. `ggml_delta_net` could not express a per-channel gate
3. Strided views reaching the scalar delta-net path (garbage -> English)
4. KDA output gate used SiLU instead of sigmoid (69 layers)
5. `ggml_add` broadcast silently corrupting every prompt pass
6. Dense FFN added the residual twice
7. Recurrent state never reset
8. `ffn_routed_norm` on the expert input instead of the output (92 layers)
9. **`expert_gating_func` unread — softmax instead of sigmoid (92 layers)**

Every one silent: no crash, no assert, full speed.

---

## Remaining work, precisely specified

The port is numerically correct (PPL 1.32) and serves through
`glm-model use kimi-k3`. Two things are left, both well-bounded.

### 1. Chat parser — blocks agentic use

K3's template uses `<|open|>`, `<|sep|>`, `<|close|>`, `<|end_of_msg|>` (and
`<|kimi_image_placeholder|>` for the vision path). ik has no parser for that
family, so it falls through to a generic one and the markers land in `content`:

```
content: think<|sep|><|open|>response<|sep|>Paris<|close|>response<|sep|><|close|>message<|sep|>
```

The answer is correct and `reasoning_content` is populated properly — this is
purely a wrapper problem, but an agent harness will choke on it.

**Where the fix goes:** `common/chat.cpp` around line 2520, where templates are
detected by marker strings and dispatched to a parser. The Kimi-K2 case
immediately above is the model to copy:

```c
if (src.find("<|tool_calls_section_begin|>") != std::string::npos &&
    src.find("<|tool_call_begin|>")          != std::string::npos) {
    return common_chat_params_init_kimi_k2(tmpl, params);
}
```

A K3 case keys on `<|open|>` + `<|sep|>` + `<|close|>` together (all three, since
`<|sep|>` alone is not distinctive) and extracts the text between
`<|open|>response<|sep|>` and the matching `<|close|>`.

**The RAW model output, captured with `--reasoning-format none`** (the decisive
measurement — the post-processed string was ambiguous about where sections start):

```
<|sep|>The user just said "Say hi." ... <|close|>think<|sep|><|open|>response<|sep|>ANSWER<|close|>...
```

It begins at `<|sep|>`, not `<|open|>`, because the chat template ends with
`<|open|>think` as the generation prompt — so the model resumes mid-section. The
full emission is therefore:

```
<|sep|> REASONING <|close|>think<|sep|> <|open|>response<|sep|> ANSWER <|close|>response<|sep|><|close|>message<|sep|>
        ^^^^^^^^^                                              ^^^^^^
        reasoning_content                                      content
```

That is everything needed to write the parser. Sketch, following
`common_chat_params_init_kimi_k2` (`common/chat.cpp:1338`) and the PEG API used
at `:880-925`:

```cpp
data.thinking_start_tag = "<|open|>think<|sep|>";
data.thinking_end_tag   = "<|close|>think<|sep|>";
data.preserved_tokens   = { "<|open|>", "<|sep|>", "<|close|>", "<|end_of_msg|>" };

auto parser = build_chat_peg_parser([&](common_chat_peg_builder & p) {
    auto generation_prompt = p.prefix(inputs.generation_prompt, "<|open|>think");
    auto reasoning = extract_reasoning
        ? p.optional("<|sep|>" + p.reasoning(p.until("<|close|>think<|sep|>")) + "<|close|>think<|sep|>")
        : p.eps();
    return generation_prompt + (reasoning << "<|open|>response<|sep|>"
                                          << p.content(p.until("<|close|>")));
});
```

Detection goes in the dispatch chain around `common/chat.cpp:2520`, keyed on
`<|open|>` + `<|sep|>` + `<|close|>` together. Note `llama-cli -p` does raw
completion rather than chat, so it is the wrong tool for capturing this — use the
server with `--reasoning-format none`, which is what produced the string above.

---

**The post-processed emission**, for reference (what the deepseek extractor leaves):

```
think<|sep|><|open|>response<|sep|>Paris<|close|>response<|sep|><|close|>message<|sep|>
```

which parses as a nestable section format:

```
<|open|>NAME<|sep|>  CONTENT  <|close|>NAME<|sep|>
```

Sections seen: `response` (the visible answer) nested inside `message`, with a
leading `think` section. So the parser extracts the body of the `response`
section and discards the rest. ik's parsers are built with
`build_chat_peg_parser(...)` — see `common_chat_params_init_kimi_k2` at
`common/chat.cpp:1338` for a worked example, including how `preserved_tokens`,
`thinking_start_tag` / `thinking_end_tag` and the PEG builder fit together.

Two things checked so the fix does not chase the wrong layer. `<|end_of_msg|>`
**is** correctly registered as an EOG token (163586), so generation stops where
it should — this is not a stopping problem. And the
`special_eos_id is not in special_eog_ids` warning at load also appears on
mainline in fairydreaming's run of this model, so it is a quirk of the K3 GGUF
rather than anything this port introduced. The remaining markers are purely
structural and need parsing, not tokenizer surgery. Mainline carries an
equivalent parser for this family — see llama.cpp #26398 for the DSV4 one — so
there is a reference to port rather than a grammar to reverse-engineer.

### 2. Fused kernel — DONE, and the premise was wrong

`iqk_fused_delta_net` could not express a per-channel gate in its signature, so
it declined and 69 of 93 layers ran the scalar reference path. This section used
to call that "the entire speed story."

It taught the fused kernel a full-rank gate (`86057247`) and measured A/B — same
binary, same model, same flags, only the dispatch guard in `ggml.c` differing:

| | scalar | fused |
|---|---|---|
| PP @ N_KV=0 | 30.33 | **39.21** |
| PP @ N_KV=512 | 29.82 | **38.61** |
| PP over 1024 tokens | 30.07 | **38.91** |
| TG @ N_KV=0 | 3.66 | 3.68 |
| TG over 256 runs | 3.65 | 3.67 |
| PPL (8 chunks, n_ctx 512) | 1.3192 +/- 0.030 | 1.3240 +/- 0.031 |

**+29% prompt processing. Nothing on generation.** The "entire speed story" claim
was false, and it was false for a reason worth writing down: the delta-net
recurrence is *sequential over tokens*, so its cost scales with how many tokens
are in flight. A 512-token ubatch runs 512 steps of it per layer; generating one
token runs one. Set against the ~15 GB of expert weights a single token already
reads (16 of 896 experts, 92 layers), one step is a rounding error.

The 3.7 tok/s is the MoE path. Nothing here measured it.

**How the claim got made.** It was an inference — the scalar path is obviously
slower, 69 layers is obviously most of them, and the number was obviously bad —
written down as a cause without a measurement. It survived into the README, the
runbook and `kimi-opencode.sh`'s user-facing warning. The A/B that refuted it
took twenty minutes and could have been run at any point. Same failure mode as
the eleven silent bugs, one level up: plausible, self-consistent, unverified.

PPL moved 1.3192 -> 1.3240, which is inside the error bar and is *expected* — the
fused kernel accumulates in a different order. It is also the proof the fused
path is actually being taken: `ggml.c`'s scalar math is untouched, so had the
dispatch not switched, the result would have been bit-identical.

Note the scalar path is *correct* but was effectively dead code in ik before this
port — the strided-view bug lived there undisturbed. It is now the reference
oracle for the fused kernel rather than a live path; keep it.

#### What the test could not have caught

`test_delta_net_gate.c` as originally written would have passed a broken kernel.
Two gaps, both fixed:

- Its gate was **constant across channels**, so a transposed or mis-strided read
  landed on the same number. It now also runs a gate that varies per
  (token, channel, head) against the recurrence written out longhand.
- Its head_dim was **8**, and `iqk_fused_delta_net` only accepts 64 and 128 — so
  it never reached the fused kernel at all. `HEAD_DIM` is now a compile-time knob;
  run it at 8, 64 and 128 to cover both implementations.

#### A real inconsistency in ik, found by the test

The fused and portable paths disagree on how a **per-head** gate and beta are
laid out, and neither is wrong on its own terms. `build_fused_delta_net` permutes
them *without* `ggml_cont` for the per-head case, so the fused kernel gets a view
and reads the underlying pre-permute buffer — head-fastest. `ggml.c`'s portable
path reads the same pointer token-fastest. On x86 the per-head case never reaches
that path, so nothing has ever noticed.

Only the per-channel case is `ggml_cont`'d, and both paths read *it*
token-fastest, which is why K3 was unaffected. The test now feeds each path the
layout it expects and says why; anyone who deletes that fixup because it looks
like a hack will get three failures that are not their fault.

### Parser attempt 1: written, tested, reverted

The sketch above was implemented (`common_chat_params_init_kimi_k3` plus
detection keyed on `<|open|>`/`<|sep|>`/`<|close|>`) and tested on the live
server. It compiled, and detection fired — but the rule did not parse:

```
content  : 'The user asks a simple factual question... Answer: Tokyo<|close|>think<|sep|><|open|>response<|sep|>Tokyo<|'
reasoning: (none)
```

Reasoning ended up in `content` and `reasoning_content` was empty — **worse than
the generic fallback**, which at least separates reasoning correctly. Reverted
(commit `576c137d`) and the previous behaviour verified restored.

The likely culprit is `p.prefix(inputs.generation_prompt, "<|open|>think")`.
K3 resumes mid-section, so the prefix handling is doing something other than what
the sketch assumes, and when the top-level rule fails the whole parse falls
through.

**The lesson, which is the useful part:** having the correct grammar is not
sufficient. Every other component in this port was validated against an oracle,
a self-consistency property, or a reference implementation before being trusted;
this parser was written from the grammar and tested only end-to-end, which is the
one place in the whole effort that shortcut was taken — and it regressed.

Whoever picks this up should first establish how `p.prefix` behaves when the
model resumes inside a section (the Ministral parser at `common/chat.cpp:880`
uses it with a *complete* tag, which is a different case), and ideally exercise
the rule against a captured string offline before putting it in front of a served
model.

## The fused and scalar delta-net kernels read the gate TRANSPOSED

Found while scoping the speed work, and it retroactively explains the contiguity
bug. The two paths index `g_data` differently:

```c
// scalar, ggml.c   - token-fastest: [n_tokens, ..., n_heads]
g_data[batch*(n_tokens*n_heads) + head*n_tokens + t]

// fused, iqk_mul_mat.cpp:1481 and :1601 - head-fastest: [n_heads, n_tokens]
g_data[g_batch_offset + t*n_heads + head_idx]
```

`build_fused_delta_net` permutes `g` into `[n_tokens, 1|S_v, H_v, n_seqs]`, but
`ggml_permute` returns a **view** — the underlying buffer keeps its original
head-fastest layout. So:

- the **fused** kernel reads the raw buffer head-fastest, which is correct for an
  unmaterialised view;
- the **scalar** path reads token-fastest, which is only correct once the view has
  been made contiguous.

That is exactly the bug fixed earlier by adding `ggml_cont` on the per-channel
path, and it is why the scalar path had been silently wrong in ik without anyone
noticing: the fused kernel handles every per-head gate, so the scalar path was
effectively dead code.

**Consequences for the speed work.** Adding a per-channel gate to
`iqk_fused_delta_net` is not just a matter of an extra loop:

1. It must index head-fastest with a channel stride, matching the *unmaterialised*
   view — not the layout the scalar path expects.
2. Once it accepts per-channel gates, `build_fused_delta_net`'s `ggml_cont` on
   that path becomes wrong for the fused route and right for the scalar one, so
   the cont has to move behind whichever path is actually taken.
3. `test_delta_net_gate.c` remains the correctness bar and will catch a
   per-channel implementation that does not reduce to the per-head case — but
   note it calls `ggml_delta_net` directly with contiguous inputs, so it exercises
   the scalar path. Extending it to cover the fused route means feeding it a
   permuted view, which is worth doing first.

Keep the scalar path as the reference oracle rather than deleting it once the
fused one handles K3.

### Why parser attempt 1 failed: `p.prefix` semantics

Read without touching the served model. `common/chat-peg-parser.cpp:833`:

```c
common_peg_parser common_chat_peg_builder::prefix(const std::string & s, const std::string & delimiter) {
    if (s.empty())         return eps();
    if (delimiter.empty()) return literal(s);
    auto pos = s.rfind(delimiter);
    ...
    return literal(s.substr(0, pos));      // <-- everything BEFORE the delimiter
}
```

It returns a literal matching the part of the generation prompt that precedes the
delimiter — it exists so that a model which *re-echoes* its generation prompt can
have that echo consumed. It matches nothing only when the prompt **ends** with the
delimiter, giving `substr(0, 0)`.

That is the Ministral case (`p.prefix(generation_prompt, "[THINK]")` where the
prompt ends in `[THINK]`), and it is **not** K3's. K3's generation prompt is a
multi-part section structure ending in `<|open|>think`, so `rfind` lands
mid-string and the rule demanded the model emit all the preceding prompt text —
which it never does. The top-level rule failed and everything fell through to the
raw string, exactly matching the observed symptom.

**The fix for attempt 2:** drop `p.prefix` entirely. K3 does not echo its prompt;
its first emitted token is `<|sep|>`, so the rule should simply start there:

```cpp
auto reasoning = extract_reasoning
    ? p.optional("<|sep|>" + p.reasoning(p.until(THINK_CLOSE)) + THINK_CLOSE)
    : p.eps();
return reasoning << RESP_OPEN << p.content(p.until(RESP_CLOSE));
```

Before trying it live again, exercise the rule against the captured string
offline — the earlier attempt went straight to a served model, which is the one
shortcut in this whole effort that was not paid for.

### Setting up the offline parser test — partial, three things learned

Attempted the offline harness before touching the served model again. It did not
compile, but the three failures are each worth recording since they are what the
next attempt needs:

1. **The test target is disabled.** `tests/CMakeLists.txt:185` has
   `llama_build_and_test(test-chat-peg-parser.cpp peg-parser/simple-tokenize.cpp)`
   **commented out**, so `test-chat-peg-parser` is not among the build targets.
   Uncommenting it is enough to get it building.
2. **`p.sequence({...})` takes parsers, not strings.** Raw `std::string` literals
   cannot be mixed into the initializer list the way they can with `+` and `<<`
   in a parser expression; wrap them (`p.literal(...)`) or build with the
   operators instead.
3. **`ctx.result()` does not exist.** The existing cases obtain the parsed
   `common_chat_msg` some other way — read the block around
   `tests/test-chat-peg-parser.cpp:463-475`, which is the shortest complete
   example of parse-then-inspect.

The rule itself is unchanged from the diagnosis above: no `p.prefix`, start at
`<|sep|>`, and the captured string to test against is

```
<|sep|>REASONING<|close|>think<|sep|><|open|>response<|sep|>ANSWER<|close|>response<|sep|><|close|>message<|sep|>
```

Nothing here touched the serving path: only a test target was built, and it
failed to compile, so `build-k3/bin/llama-server` is untouched and K3 kept
serving throughout.


## Parser: DONE

Landed on attempt three. `content` now holds only the answer:

```
content   : 'Tokyo'
reasoning : 'The user is asking a simple factual question: the capital of Japan...'
```

and a longer code answer comes back as clean markdown with no markers at all.

**What made the difference was the fixture, not the rule.** Attempts one and two
failed on a fixture captured from the post-processed `content` field, which has
already had the message opener stripped — so it tested the parser against a
string the parser never sees. Attempt two passed 3/3 offline and still
crash-looped the server. The working fixture came from the **crash message**,
which shows the untouched input:

```
<|open|>message role="assistant"<|sep|><|open|>think<|sep|> R <|close|>think<|sep|>
<|open|>response<|sep|> ANSWER <|close|>response<|sep|><|close|>message<|sep|>
```

The model emits its own message opener — so the original `p.prefix` diagnosis was
wrong twice over: `prefix()` exists precisely to consume a re-echoed prompt, and
removing it was the wrong correction. The final rule wraps the opener in its own
`p.optional` so a turn rendered without it still parses.

Also note `p.optional` takes a parser: a bare `std::string` only works inside an
expression where another operand is already a parser, so a lone marker needs
`p.literal(...)`.

The offline harness was still the right call — it caught the compile errors and
the rule shape for free, and the target had to be un-commented in
`tests/CMakeLists.txt` to exist at all. The lesson is narrower than "test
offline": **an offline test is only as good as where its fixture came from.**
