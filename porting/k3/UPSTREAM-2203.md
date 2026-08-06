# Draft reply to ik_llama.cpp #2203 — NOT POSTED

Ready to post to <https://github.com/ikawrakow/ik_llama.cpp/issues/2203>. Held
back deliberately: posting is outward-facing and unreviewed by anyone but the
author of the port. Read it, change what you disagree with, then post it
yourself.

The objection being answered is ikawrakow's own, and it is not a design
objection:

> Kimi-K3 is seriously beyond my hardware limits. Not sure I want to just blindly
> copy the mainline K3 PR (especially considering how much the two code bases
> have diverged).

Two concerns — no hardware to validate on, and no appetite for a blind port of
a diverged PR. A working port on hardware that does exist, with numbers, answers
the first. Being explicit about what touches shared code answers the second.

The most useful part for ik regardless of whether K3 is ever merged is the
**bug in the existing per-head gate handling** (last section). That is worth
reporting on its own.

---

## Draft

Kimi K3 runs on ik_llama.cpp. The port is at
[TheChuckster/ik_llama.cpp branch `kimi-k3`](https://github.com/TheChuckster/ik_llama.cpp/tree/kimi-k3),
built and measured on a single EPYC 9575F (64 cores, 12x DDR5-4800, 1.1 TB),
serving `unsloth/Kimi-K3-UD-Q2_K_XL` (802 GiB).

**Correctness.** Wikitext perplexity **1.3240 +/- 0.031** at n_ctx 512, against a
**1.5499** reference measured at n_ctx 8192 on a worse quant (plain `Q2_K`) from
the mainline PR thread. Below the reference at a shorter context with a better
quant is the ordering one would expect, and the per-chunk curve descends
(1.35 -> 1.32) rather than climbing. It answers correctly on facts, arithmetic
and code, with reasoning parsed into `reasoning_content`.

**Speed.** PP 38.9 tok/s, TG 3.67 tok/s at `-c 1024 -t 64 -fa 1 -mla 3`.
Generation is at the memory wall, not in any kernel: K3 reads 71.2 GiB per
token (16 of 896 experts active, and 81% of the traffic is non-expert weight the
quant ships at Q8_0), which at 3.67 tok/s is 281 GB/s on a 461 GB/s box. TG is
3.67 at 32 threads, 3.67 at 64, 3.66 at 96.

### What it touches

Almost all of it is additive — a new `LLM_ARCH_KIMI_K3`, its hparams loader and
tensor mapping, and a graph builder in `src/graphs/build_kimi_k3.cpp`. The
architecture needs AttnRes (cross-layer residual attention), SiTU
(range-limited SwiGLU), a latent MoE with three widths in one block, and MLA on
24 of its 93 layers with KDA on the other 69.

Three changes are **not** isolated to the new arch, and they are the ones worth
reviewing:

1. **`LLAMA_MAX_EXPERTS` 512 -> 1024.** K3 has 896 routed experts. This sizes
   stack arrays in the hparams and graph paths.

2. **`ggml_delta_net` accepts a per-channel forget gate.** K3 sets
   `use_full_rank_gate`, so `A_log` is `[128]` — one decay per channel rather
   than the per-head scalar Qwen3-Next uses. The assert is relaxed from
   `g->ne[1] == 1` to `g->ne[1] == 1 || g->ne[1] == S_v`.

3. **`iqk_fused_delta_net` implements that gate**, rather than declining and
   dropping 69 of 93 layers to the scalar path. Selected by a template
   parameter, so the per-head instantiation compiles to exactly the code it did
   before — every expression it evaluates is unchanged, and `decay` is set to
   literally `1.0f` on the per-channel path specifically so the output
   coefficients did not have to be rewritten. Worth +29% PP on K3, measured A/B
   with only the dispatch guard differing.

   A scalar decay factors out of the state-times-k and state-times-q sums, which
   is why the per-head path can fold it into the output coefficients and never
   touch the state until the update. A per-channel decay does not factor out, so
   it is applied per column inside the accumulation loop — the state is read and
   written exactly as often as before.

`porting/k3/test_delta_net_gate.c` in the kit is the correctness bar: a
per-channel gate with all channels equal must reduce to the per-head result, and
a gate that varies per (token, channel, head) must match the recurrence written
out longhand. It takes a `HEAD_DIM` knob because `iqk_fused_delta_net` only
accepts 64 and 128 — at 8 it silently tests the portable path instead.

### A pre-existing bug, independent of K3

Writing that test surfaced something in ik that has nothing to do with this port:

**`ggml_compute_forward_delta_net_f32`'s portable path and `iqk_fused_delta_net`
disagree about the memory layout of a per-head gate and beta.**

`build_fused_delta_net` permutes them *without* `ggml_cont` for the per-head
case, so `src3->data` still points at the pre-permute buffer and the fused
kernel reads it head-fastest — `g_data[batch*n_tokens*n_heads + t*n_heads + h]`.
The portable path reads the same pointer token-fastest —
`g_data[batch*n_tokens*n_heads + h*n_tokens + t]`. Those are transposes of each
other.

On x86 the per-head case always takes the fused path, so nothing has noticed.
Anything reaching the portable path with a per-head gate — a non-x86 build, or a
head_dim other than 64 or 128 — gets silently wrong results for Qwen3-Next, not
a crash. Only the per-channel case is `ggml_cont`'d, and both paths read that one
token-fastest, which is why K3 is unaffected.

I have not fixed it, because the right fix depends on which layout you consider
canonical, and that is your call.

### Offer

Happy to open this as a PR against `main`, split into reviewable pieces —
the delta-net gate work first, since it stands alone and comes with the test —
or to leave it as a fork and just file the layout bug separately. I can also run
anything you would like measured on this hardware; that seems to be the scarce
part.
