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
