# Adding a model to this box

The repeatable procedure, generalised from adding DeepSeek-V4-Flash (a new
architecture ik already supported) and Kimi K3 (one it did not, requiring a
fork). Follow it in order; each step is cheap and rules something out before the
expensive step after it.

**The governing constraint: every change is ADDITIVE.** GLM-5.2 and
DeepSeek-V4-Flash are working, served models. Nothing here modifies how they
load, what engine they run on, or what flags they get. The registry's `engine`
and `opts` fields exist precisely so a new model can be wrong without any of
that mattering — and adding a model has broken serving twice, both times through
a shared default rather than a per-model change.

---

## 1. Does it fit, and is the quant worth having?

```sh
glm-model upstream <variant>     # what HF publishes now, and does it fit this box
```

Judge by the size of the **quant file**, not the parameter count, and check it
against RAM before downloading half a terabyte. Two traps worth internalising:

- **If the weights are already low-precision, the quant ladder does not apply.**
  DeepSeek-V4 ships its experts at fp4 and Kimi K3 at mxfp4. Quantising *above*
  native recovers nothing (DS4's Q8 is 7 GB larger than Q4 and buys precision the
  weights never had); quantising *below* destroys what QAT put there. The real
  choice is how the ~5% of tensors that are *not* experts get treated.
- **A publisher's quant targets their own engine.** The antirez DS4 quant looked
  the best on paper and segfaults on ik. ubergarm is the publisher who targets ik.

## 2. Add a registry row — additively

`serving/glm-variants.conf`, 9 pipe-separated fields:

```
name|repo|subdir|prefix|shards|alias|dir|engine|opts
```

- `shards` — a count, or `1` for a single unsharded `<prefix>.gguf` (several
  publishers do not shard files under ~200 GB), or `?` to resolve from HF.
- `engine` — build tree under `~/ik_llama.cpp`. **Empty means `build`**, which is
  what GLM and Kimi K2 use. A new architecture gets its own tree; see step 4.
- `opts` — appended last, so it overrides any default in `serve-glm.sh`. This is
  where a model's peculiarities go, and it is the only safe place for them.

Then `glm-model download <name>` (resumable, byte-exact size verification) and
`glm-model verify <name>`.

## 3. Prove it loads and answers, on a spare port

```sh
./serving/validate-model.sh <label> <first-shard.gguf> [extra flags]
```

Checks load, coherence, reasoning staying out of `content` **on the streaming
path**, and a tool-call round trip — on port 8081, never touching the live
server. Every check in it exists because something passed a weaker test first:

- loading is not enough (a quant that runs in its author's engine can segfault here)
- answering is not enough (a broken quantisation kernel gives fluent nonsense)
- non-streaming is not enough (harnesses stream, and reasoning handling differs there)

## 4. If ik has no architecture for it

Then it needs a fork, and `porting/k3/GRAPH-BUILDER-SPEC.md` is the worked
example end to end. The parts that generalise:

- **Build into a NEW tree** (`build-k3`, `build-ds4`), never over `build`. Point
  the registry row's `engine` field at it. This is the whole reason that field
  exists: bringing up a new architecture means moving the engine forward by weeks
  of commits, and doing that globally re-rolls the dice on every working model.
- **Reuse must be justified op-by-op, not by shape compatibility.** Substituting
  "the existing helper that does the same thing" silently changed a sigmoid to a
  SiLU across 69 layers. Every substitution that type-checked and ran was wrong in
  a different way.
- **Verify components against something that can fail** — a numerical oracle, a
  self-consistency property, a line-by-line diff against a reference. Then expect
  that not to be enough: on K3 every component verified and the model was still
  15x off, because the fault was in *hparam loading*, upstream of all of them.
- **When every part checks out and the whole is wrong, stop verifying parts.**
  Diff a working implementation's *execution* against yours —
  `llama-eval-callback` on both, one prompt, layer by layer. That found the real
  bug in one pass after four techniques had exhausted themselves.

## 5. Serve it

```sh
sudo glm-model use <name>        # switches + restarts, waits for readiness
glm-model status                 # the ONLY reliable check of what is loaded
```

`llama-server` does not reject a request naming a different alias than the loaded
model — it answers with whatever is resident. `glm-model status` compares
`/v1/models` against the selection; nothing else does.

**Serving is a distinct test, not a formality.** Both times a model broke the
service, the cause was a shared default in `serve-glm.sh` that no CLI test had
exercised — most recently `--cache-type-v q8_0` asserting against a
`value_length` the model reports but never uses. Fix those in the row's `opts`,
never by changing the shared default.

## 6. Route it to the harnesses

`harness/litellm-config.yaml` and `harness/opencode.json`, plus the live copies
in `~/Projects_new/ai` and `~/.glm-opencode-config`. If the model has caveats
that will bite an agent — slow, or leaking template markers — put them in the
comment beside the route. A route that hides them gets used by mistake.

---

## 7. Requantize the part nobody optimises (worth ~10% TG, on every model)

Published quants spend their care on experts, because experts are the file. But
a token only reads the experts it activates, and everything else in full — so on
this box the always-read remainder is **58.6% of a GLM token, 68.5% of a
DeepSeek-V4 token, 81% of a Kimi K3 one**, and all three publishers ship it at
Q8_0. Run `porting/k3/bytes_per_token.py` on any GGUF to see the split.

Requantizing just that part to Q6_K, with every routed expert copied through
untouched:

```sh
llama-quantize --allow-requantize --keep-f32 --keep-split \
    --keep-pattern "_exps\." IN.gguf OUT.gguf Q5_K 64
```

Measured, each against its own baseline, same flags, back to back:

| | TG before | TG after | PPL before | PPL after |
|---|---|---|---|---|
| GLM-5.2 | 10.68 | **12.43** (+16.4%) | 1.3750 +/-0.037 | 1.3953 +/-0.038 |
| DeepSeek-V4 | 23.82 | **27.75** (+16.5%) | 2.4020 +/-0.114 | 2.3884 +/-0.112 |
| Kimi K3 | 3.67 | **4.30** (+17.2%) | 1.3240 +/-0.031 | 1.3253 +/-0.030 |

Every perplexity move is inside its error bar — DeepSeek-V4's is nominally
*better* than its Q8_0 original. Prompt processing improves slightly too. Minutes
of work per model: the experts are memcpy'd rather than requantized, so an
800 GiB model takes about five minutes.

### Q5_K, specifically — the type matters more than the bit count suggests

The whole ladder, on DeepSeek-V4, same recipe with only the target type varying:

| non-expert type | PP @512 | TG @512 | PPL (baseline 2.4020) |
|---|---|---|---|
| Q8_0 (untouched) | 323.2 | 23.82 | 2.4020 +/-0.114 |
| Q6_K | 320.3 | 25.89 | 2.4312 +/-0.117 |
| Q6_0 | 321.4 | 26.35 | - |
| **Q5_K** | 331.4 | **27.75** | **2.3884** +/-0.112 |
| IQ5_K | **339.9** | 27.53 | 2.4376 +/-0.115 |
| IQ4_K | 346.0 | 29.36 | 2.5545 +/-0.126 |
| Q4_K | 333.0 | 29.45 | 2.6438 +/-0.135 |

Two things fall out of this that are not obvious from bits-per-weight:

- **Q6_K is the wrong stop.** It is *bigger* than Q5_K and *slower*, and its
  perplexity is worse. Fewer bytes is not the only axis — dequantization cost per
  byte matters, and Q6_K's superblock format costs more per byte than Q8_0 does,
  which is why Q6_K also cost a little prompt processing while Q5_K gains some.
- **Five bits is free, four is not.** The jump to IQ4_K/Q4_K buys ~6% more TG for
  a perplexity shift of +0.15 to +0.24 — a systematic move, unlike everything at
  5 bits and above, which scatters inside the error bars. Stop at Q5_K.

IQ5_K is the alternative if prompt processing matters more than generation: +2.6%
PP over Q5_K for slightly worse TG and perplexity.

Three things to know before running it:

- **`--keep-f32` is not optional.** A tensor left at F32 in an already-quantized
  model is F32 on purpose. K3's `ssm_conv1d_*` are 4 columns wide, cannot take a
  k-quant, and the fallback path turns them into Q8_0 — `ggml_ssm_conv` then
  aborts at the first token. GLM's 79 `indexer.proj` tensors choose which tokens
  DSA attention sees, and quantizing those would not have crashed anything; it
  would just have gotten quietly worse. Both are tiny: all 79 of GLM's save
  47 MiB against a 32.58 GiB token.
- **Check the dry run for F32 conversions** even so:
  `--dry-run ... | grep "type =    f32, converting"` should print nothing.
- **You only get part of the arithmetic.** K3's byte count predicted 1.26x and it
  returned 1.17x. Dequantization is not free, so bytes saved is an upper bound,
  not a forecast. Predict with the byte count to decide whether it is worth
  trying; then measure to find out what you got.

Register the result as a NEW row rather than replacing anything — the originals
stay downloaded and one `glm-model use` away. A locally-built variant needs its
`.complete` marker written by hand, since there is no upstream to size-check
against: `stat -c %s *.gguf | paste -sd+ | bc > .complete`.

## 8. Per-model flags are per-model: check, do not assume

`-rtr` (run-time repack) is the sharpest example measured here:

| | PP | TG |
|---|---|---|
| DeepSeek-V4 | **+15%** | **+4%** |
| GLM-5.2 | -4% | +0.5% |
| Kimi K3 | **-16%** | 0% |

Same flag, same box, same day: a solid win, a wash, and a serious regression.
It lives in the registry row's `opts`, never in `serve-glm.sh`.

Two things tested here and NOT worth adopting, recorded so they are not
retried: `--no-mmap` to get transparent huge pages behind the weights (TG 28.09
-> 27.74, and `AnonHugePages` never rose — with `defrag=[madvise]` the kernel
will not eagerly back a 145 GB anonymous region), and raising thread count above
64 (TG is flat from 32 to 96 threads and falls off a cliff at 128).

---

## The failure mode this box punishes

Every serious bug found across both ports was **silent**: no crash, no assert,
full speed, plausible output. A wrong activation, a broadcast where a shape error
was expected, a residual added twice, a default hparam left unread. None of them
produced an error message, and several survived multiple rounds of review because
the code read correctly.

Budget for that. Prefer checks that produce a *number* — perplexity against a
published reference is the single most useful one, and it is what finally
distinguished "this quant is bad" from "this port is wrong".

**The same applies to performance, and it is easier to get wrong.** K3 generated
at 3.6 tok/s while 69 of its 93 layers ran a scalar fallback, so the fallback got
written down as the cause — in the README, in the runbook, and in a warning the
harness script printed to users. It was never measured. Teaching the fused kernel
K3's gate turned out to be worth +29% on prompt processing and *nothing* on
generation, because the delta-net recurrence is sequential over tokens: a
512-token batch runs 512 steps of it, a generated token runs one.

An A/B is cheap — build the two variants, keep everything else identical, run
`llama-sweep-bench` twice. Twenty minutes. Do it before writing a cause down,
because a plausible attribution propagates into documentation and then gets
believed.
