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
