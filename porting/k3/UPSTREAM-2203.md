# Draft reply to ik_llama.cpp #2203 — NOT POSTED

Ready to post to <https://github.com/ikawrakow/ik_llama.cpp/issues/2203>. Held
back deliberately: posting is outward-facing and reviewed by nobody but the
author of the port. Read it, change what you disagree with, then post it
yourself.

**Three of the four items below are useful to ik whether or not Kimi K3 ever
lands**, and two are bug reports rather than features. If the whole thing is too
much, file those separately — they stand alone:

1. the per-head gate layout inconsistency in `ggml_delta_net` (silently wrong
   Qwen3-Next results on any non-x86 path)
2. `ssm_conv1d` prefix matching in the quantizer (produces a model that
   quantizes without error and aborts at the first token)
3. the `n_attention_wv` assert, which no hybrid-attention model can satisfy

The objection being answered is ikawrakow's own, and it is not a design
objection:

> Kimi-K3 is seriously beyond my hardware limits. Not sure I want to just blindly
> copy the mainline K3 PR (especially considering how much the two code bases
> have diverged).

Both halves are answerable. A working port on hardware that exists answers the
first. As for the second — the parser here is now a deliberate port *of* the
mainline PR rather than an independent invention, which is the opposite of
blind: it was written independently first, and every place the two differed
turned out to be a defect on this side.

---

## Draft

Kimi K3 runs on ik_llama.cpp. The port is at
[TheChuckster/ik_llama.cpp branch `kimi-k3`](https://github.com/TheChuckster/ik_llama.cpp/tree/kimi-k3),
built and measured on a single EPYC 9575F (64 cores, 12x DDR5-4800, 1.1 TB),
serving `unsloth/Kimi-K3-UD-Q2_K_XL`.

**Correctness.** Wikitext perplexity **1.33** at n_ctx 512 against a **1.55**
reference measured at n_ctx 8192 on a worse quant. Answers factual, arithmetic
and code questions reliably, with reasoning parsed into `reasoning_content` and
tool calls into `tool_calls`.

**Speed.** PP **40.0** tok/s, TG **4.30** tok/s, and it degrades gently with
context (32.0 PP / 4.03 TG at 12K). Generation is at the memory wall, not in any
kernel: K3 reads 56.4 GiB per token and gets 260 GB/s on a 461 GB/s box, and TG
is flat from 32 to 64 threads.

**One caveat stated up front**, because it bounds what the port is good for: at
2.479 bpw K3 degenerates into repetition loops on long prompts — every attempt
on a ~7000-token agent prompt, about half the time on a short chat prompt. That
is the quant, not the port (prose and short answers are reliable, and the next
quant up needs 1.5 TB of RAM). It is worth knowing before anyone tries to use
this agentically.

### 1. A bug in the existing per-head gate handling, independent of K3

**`ggml_compute_forward_delta_net_f32`'s portable path and `iqk_fused_delta_net`
disagree about the memory layout of a per-head gate and beta.**

`build_fused_delta_net` permutes them *without* `ggml_cont` for the per-head
case, so `src3->data` still points at the pre-permute buffer and the fused kernel
reads it head-fastest — `g_data[batch*n_tokens*n_heads + t*n_heads + h]`. The
portable path reads the same pointer token-fastest —
`g_data[batch*n_tokens*n_heads + h*n_tokens + t]`. Those are transposes.

On x86 the per-head case always takes the fused path, so nothing has noticed.
Anything reaching the portable path with a per-head gate — a non-x86 build, or a
head_dim other than 64 or 128 — gets **silently wrong Qwen3-Next results**, not a
crash. Only the per-channel case is `ggml_cont`'d, and both paths read that one
token-fastest, which is why K3 is unaffected.

**Fixed on the branch**, once it became clear the question answers itself: the
pointer both kernels receive is the pre-permute buffer, so head-fastest is what
is *actually in it* and the portable path was simply reading it wrong. The
per-channel case is `ggml_cont`'d and stays token-fastest on both paths.

`test_delta_net_gate.c` is the evidence. It previously needed

```c
beta_ph[fused ? t*H + h : h*T + t]
```

— a fixup whose only purpose was to paper over the disagreement, added when the
test first exposed it. It is now unconditional head-fastest and all three head
dims pass: 8 (portable), 64 and 128 (fused). If you prefer the other convention
the fix inverts trivially, but then `build_fused_delta_net` needs a `ggml_cont`
it currently avoids for good reason.

### 2. Two quantizer bugs, also independent of K3

**`ssm_conv1d` is matched as a literal, not a prefix.** The guard is
`name.find("ssm_conv1d.weight")`. K3 has one conv per projection —
`ssm_conv1d_q/_k/_v.weight` — so it never matched and those weights became
eligible. They are 4 columns wide, cannot take a k-quant, and
`change_type_if_necessary` quietly falls them back **from F32 to Q8_0**;
`ggml_ssm_conv` then aborts on `GGML_ASSERT(src2->nb[0] == sizeof(float))` at the
first token. A model that quantizes without error and cannot generate. Matching
the prefix fixes it and costs nothing — quantizing a 4-wide kernel saves 51 MiB
of 800 GiB.

**The `n_attention_wv` assert cannot be satisfied by a hybrid model.** It permits
0, `n_layer`, or `3*n_layer`. K3 interleaves 24 MLA layers among 69 KDA ones, so
its count is 24 and the assert fires before any tensor is written. The count only
feeds the heuristic that spends extra bits on the first and last attention
layers, so an arch exemption costs a slightly different bit allocation rather
than correctness.

### 3. Two quantizer features that made a 17% speedup expressible

Not bugs, but worth offering. Published quants spend their care on experts,
because experts are the file — yet a token reads only the experts it activates
and everything else in full. On this box the always-read remainder is **58.6% of
a GLM token, 68.5% of a DeepSeek-V4 token, and 81% of a Kimi K3 one**, and all
three publishers ship all of it at Q8_0.

Requantising only that part to Q5_K is worth **+16% to +17% TG on all three
models** at unchanged perplexity. It was not expressible before, because there is
no same-type passthrough: asking for a type a tensor already has still
dequantizes and requantizes it, so any `--custom-q` run would spend hours
destroying and rebuilding the experts it was not asked to touch.

- `--keep-pattern <regex,...>` copies matching tensors verbatim. With
  `--keep-pattern '_exps\.'` an 800 GiB model requantizes in five minutes.
- `--keep-f32` leaves tensors that arrive as F32 alone. Not optional in practice:
  it is what stops the `ssm_conv1d` failure above, and GLM-DSA has 79
  `indexer.proj` tensors that choose which tokens attention sees and would have
  degraded silently.

### 4. The per-channel KDA gate, which is what K3 actually needed

K3 sets `use_full_rank_gate`, so `A_log` is `[128]` — one decay per channel
rather than the per-head scalar Qwen3-Next uses. The assert in `ggml.c` is
relaxed from `g->ne[1] == 1` to `g->ne[1] == 1 || g->ne[1] == S_v`, and
`iqk_fused_delta_net` implements it rather than declining and dropping 69 of 93
layers to the scalar path.

Selected by a template parameter, so the per-head instantiation compiles to
exactly the code it did before — every expression unchanged, and `decay` set to
literally `1.0f` on the per-channel path specifically so the output coefficients
did not need rewriting. Worth **+29% prompt processing**, measured A/B with only
the dispatch guard differing.

A scalar decay factors out of the state-times-k and state-times-q sums, which is
why the per-head path can fold it into the output coefficients and never touch
the state until the update. A per-channel decay does not factor out, so it is
applied per column inside the accumulation loop — the state is read and written
exactly as often as before.

`porting/k3/test_delta_net_gate.c` is the correctness bar: a per-channel gate
with all channels equal must reduce to the per-head result, and a gate varying
per (token, channel, head) must match the recurrence written out longhand. It
takes a `HEAD_DIM` knob because `iqk_fused_delta_net` only accepts 64 and 128 —
at 8 it silently tests the portable path instead, which is how the layout bug in
§1 surfaced.

### Also in the branch

`LLAMA_MAX_EXPERTS` 512 → 1024 (K3 has 896 routed experts), the
`LLM_ARCH_KIMI_K3` plumbing, a graph builder for AttnRes / SiTU / latent MoE /
hybrid MLA+KDA, and a chat parser ported from mainline's PR.

### Offer

Happy to open any of this as PRs against `main`, split into reviewable pieces —
the delta-net gate work first, since it stands alone and comes with a test, or
the two quantizer bug fixes, which are a few lines each. I can also run anything
you would like measured on this hardware; that seems to be the scarce part.
