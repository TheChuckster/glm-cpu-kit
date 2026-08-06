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
