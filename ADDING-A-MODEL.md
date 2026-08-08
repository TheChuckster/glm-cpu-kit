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

- **Build into a NEW tree**, never over `build`. Point the registry row's
  `engine` field at it. This is the whole reason that field exists: bringing up a
  new architecture means moving the engine forward by weeks of commits, and doing
  that globally re-rolls the dice on every working model.
- **Then converge, once it is proven.** A pinned tree does not move when you
  rebase, so it silently ages: `build-ds4` sat three weeks behind while
  `build-k3` went forward, missing the very fix for DS4 tool calls. When the new
  arch passes the gate and the others still validate on its commit, empty the
  `engine` fields and delete the extra trees. Three trees compiled from one
  source is not isolation.
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

## 8. Speculative decoding: free, and enormous on the right workload

`--spec-type ngram-mod:n_max=16,n_min=2` drafts tokens by matching n-grams
already in the context and lets the model verify a run of them in one pass.
Two properties make it unusually attractive here:

- **It is lossless.** Only tokens the target model would have produced are
  accepted, so output is unchanged. This is not a quality trade.
- **Verification is nearly free on this box.** Generation is bandwidth-bound, so
  checking 16 drafted tokens costs about what generating one costs. Every
  accepted token is close to pure profit.

Measured on GLM-5.2, same server, same config, two workloads:

| | tok/s |
|---|---|
| generic prose (no n-gram overlap) | **12.40** — its exact baseline |
| a code edit ("output this function, rename it") | **23.48, then 30.19** |

That is **+89% to +143% on the workload a coding agent actually runs**, and
*exactly zero cost* when nothing repeats. The control is the point: prose comes
back at the baseline to three digits, so the gain is attributable to speculation
and nothing else.

**It does nothing for a reasoning model, and the reason generalises.** Kimi K3
and DeepSeek-V4 always think before answering, and reasoning prose repeats
nothing in the context, so nothing gets drafted — K3 measured 4.17-4.38 against
a 4.30 baseline, i.e. noise in both directions. It is still worth enabling
(it costs nothing and fires during the answer phase), but the win belongs to
models with thinking off.

Beware a measurement artifact here. `llama-spec-bench -f prompt.txt` feeds the
prompt as a **raw completion**, with no chat template, so the model simply
continues the text — echoing the input, at 93% draft acceptance, for a headline
7.13 vs 4.28. That number is real and completely unrepresentative: through the
server, with the chat template applied, the same model and prompt gain nothing.
Measure speculation through the actual serving path or not at all.

`n_max` has a clear optimum and it is not "bigger is better": 16-20 is the peak
(7.13-7.21 in the raw harness), 32 falls to 5.85, and 64 collapses to 4.26 as
acceptance drops to 12% and the wasted verification outweighs the wins.

## 9. Prove the agentic loop, not just the tool call

A model that returns one `tool_calls` response is not yet usable by an agent.
The turn after it is the one that breaks: the assistant message has to be
replayed **complete**, including `tool_calls` and `reasoning_content`, followed
by a `role: "tool"` result. That shape is what ik #1605 was reported to 400 on,
and for a model that always reasons it is unavoidable rather than optional.

So test both halves. All three models on this box, on their `-q5attn` rebuilds:

| | single call | replay + tool result |
|---|---|---|
| Kimi K3 | yes | yes — "The weather in Oslo is currently 3°C with light rain." |
| DeepSeek-V4 | yes | yes |
| GLM-5.2 | yes | yes |

The replay path returns HTTP 200 on all three, so #1605 is not blocking here.

**Then measure it repeatedly, because it is not deterministic.** One success is
not a working tool path. All three models emit calls **5/5** — but K3 only got
there after the cause of its failures was found, and the cause was **a flag this
kit had turned on**.

### The lesson: A/B your own optimisations against correctness, not just speed

`--spec-type ngram-mod` was added to every model's row because it is worth
+89% to +143% on GLM code edits and costs nothing when it cannot fire.
Speculative decoding is supposed to be **lossless** — only tokens the target
model would have produced are accepted — so it was never suspected.

On Kimi K3 it was not lossless, and chasing that produced the most useful
sequence of wrong answers in this whole document:

| K3 | tool reliability |
|---|---|
| speculation on (as first shipped) | **0/5** |
| speculation on, after the fork fix | **5/5** |
| no speculation | 5/5 |

**The cause was a defect in our own graph builder, not in ik and not in the
quant.** `build_qkv` takes `per_step_ssm` and `per_step_conv` as trailing
optional arguments; the K3 KDA path stopped one argument short, so both defaulted
to `nullptr` and the recurrent state was never snapshotted per draft step. A
rejected draft then had nothing to roll back to and carried the rejected tokens
forward — fluent drift compounding into repetition loops.

### How it was found is the transferable part

The chain of explanations, in order, each one confidently written down:

1. K3 is slow because of the scalar delta-net kernel → **wrong**, kernel gave PP not TG
2. K3's tool calls fail because 2.479 bpw loses structured-output discipline → **wrong**
3. Speculation breaks K3, disable it → **wrong**, it is worth up to +123%
4. `--spec-ckpt-mode auto` is an ik bug on CPU-only builds → **wrong**
5. our builder never passed the per-step checkpoint tensors → **right**

Step 4 is where it turned, and only because the report was tested before it was
sent. Qwen3-Next-80B was downloaded specifically to confirm "recurrent models are
affected on CPU builds" — and came back **5/5 with speculation on the default
mode**. A recurrent model that was *fine* is what made the ik theory untenable
and sent me to `llama-delta-net.cpp`, which had been passing those two tensors
all along.

Every wrong answer blamed something unfixable: the kernel, the quant, the
technique, the upstream. That is the tell. **An explanation that ends "and
therefore nothing can be done" deserves one more test, not a paragraph in the
README.**

### Diff what is installed against what is in the kit

The kit is version-controlled; the box is not. They drift, and the drift is
invisible from either side alone. Checked all four installed artifacts:

| | |
|---|---|
| `/usr/local/bin/glm-model` | identical, 0 diff lines |
| `/usr/local/bin/serve-glm.sh` | behind by three auto-detection fixes |
| `/etc/systemd/system/glm-server.service` | **missing `TimeoutStopSec=600`** |
| `~/serve-glm.sh` | a 24-line stub, not run by anything |

The unit one had teeth. Without that setting, systemd SIGKILLs after 90 seconds,
and releasing 845 GB of mlocked memory takes longer — **58 hard kills in the
previous day**, one per restart, each leaving the unit `failed`. The kit had
carried the fix and its explanation for weeks; the box had never received it.

A fix that exists only in the repository is not deployed.

**And a shared script verified against one model is verified against one model.**
`serve-glm.sh` starts all of them; it was checked with K3 and then left. Restarted
DeepSeek-V4 and GLM-5.2 under it as well — both came up with `--threads 64`
auto-detected, `--mlock` kept, their own `opts` intact (`-rtr` and `--spec-type`
for DS4), tool calls working, and `Result=success` on every teardown including
the 845 GB one. Three model switches, **zero hard kills**, where the previous day
had 58.

### The script you are reading may not be the one that runs

`~/serve-glm.sh` on this box is a 24-line stub from July. The unit runs
`/usr/local/bin/serve-glm.sh`, which is 154 lines and registry-aware. Reading the
stub during an investigation produced a confident theory about flags that were
"in the script but not on the command line" — they were not in the script that
runs.

`grep ExecStart /etc/systemd/system/glm-server.service` first, every time.

### Deploy your own changes before claiming they work

`serve-glm.sh`'s auto-detection was verified by extracting the functions and
running them standalone, which proves the arithmetic and nothing else. The
modified script had never actually started a server.

Installed and restarted: `--threads 64` from `lscpu`, no `--numa` from `numactl`
reporting one node, `--mlock` kept because 845 GB fits under the 1119 GB
threshold — all three matching the hardcoded values they replaced. The unit's
overrides are commented out so the detection is genuinely exercised rather than
masked.

### Build clean before you claim the fork works

Every change to the fork in this project was compiled **incrementally**, which
hides missing includes and stale objects — a tree that builds for you and for
nobody else. Verified from scratch:

```sh
cmake -B build-clean -DGGML_NATIVE=ON -DLLAMA_BUILD_TESTS=ON
cmake --build build-clean -j 48          # ALL targets, not a --target list
ctest --test-dir build-clean -R delta-net-gate
```

Clean build succeeded, delta-net gate 3/3 across head dims 8/64/128, chat peg
parser 34 tests and 205 assertions with no failures. Cheap, and the alternative
is finding out from whoever tries to use the branch.

**Build every target, not a subset.** This originally named four targets, which
is exactly the mistake that later cost real debugging time: rebuilding an
existing tree with `--target llama-server` left `llama-perplexity`,
`llama-quantize`, `llama-sweep-bench` and every test on a build three weeks older
than `libllama`. They still ran — with stale struct offsets, so a `bool` read a
neighbouring byte of an `int32_t -1` and reported `255`. The failure surfaced as
an impossible-looking parameter mismatch, nowhere near the cause. See the runbook
section "Rebuilding one target leaves every other tool ABI-incompatible".

Check a tree in one line — a healthy one reports a single date:

```sh
ls -l build/bin | awk 'NR>1{print $6}' | sort | uniq -c
```

### Keep a control model for any shared code path you touch

`qwen3-next-ref` is registered for exactly this and is not a serving target.
Qwen3-Next shares the delta-net path with K3's KDA layers and is supported
**upstream**, so it answers the question "is this my bug or ik's?" in one run.
It is 46 GB and loads in seconds.

Run it through `validate-model.sh` on a spare port after touching
`ggml_delta_net`, `iqk_fused_delta_net`, or `build_kimi_k3.cpp`'s KDA path. The
one time it was used it saved an incorrect bug report from being sent to a
maintainer who had already said his time was scarce — and pointed at the real
fault in the process.

Its baseline, for diffing against later — every check, **14 seconds** after load,
with speculation on:

```
COHERENCE: PASS      TOOL RELIABILITY: 5/5 PASS
REASONING: PASS      STREAM TOOLCALL:  PASS (7 deltas)
TOOLCALL:  PASS      MULTITURN:        PASS
                     DEGENERATION:     PASS
```

At 43 GB it runs **alongside** a resident K3 without stopping the live server —
`glm-model status` will report "not responding" while the single slot is busy,
which is the slot being occupied and not the server being down.

With it on, K3 degenerates into repetition — 8000 tokens of one paragraph on an
agent prompt — and never emits the call. With it off, `kimi-opencode` chains
Glob and Read and answers correctly in 237 seconds, where it previously ran 1464
seconds and printed nothing.

**Everything previously written here blaming 2.479 bpw for K3's tool failures
was wrong.** The quant is fine; a well-measured throughput optimisation was
silently corrupting output. It went unnoticed for a long time because it was
introduced early, left in every subsequent test including the ones labelled
"pure reference config", and because a lossless technique is not where you look.

It is still enabled for GLM and DeepSeek-V4, both of which measure 5/5 with it
on. K3's row alone omits it. **Speculation is per-model, like every other flag
here, and it needs a correctness A/B and not only a throughput one.**

Two smaller lessons from how it was eventually found:

- **The gate has to be re-run after a config change, not only when adding a
  model.** The single tool-call check already failed with speculation on. Nobody
  ran it, because adding a flag to a registry row does not feel like the kind of
  change that needs revalidating. It is.
- **The degeneration check missed it — twice.** First its prompt was long filler
  with no tools, so it never provoked the failure. Then, with tools added, it
  still passed: the detector looked for verbatim repetition, and that run's
  degeneration was a counting sequence and unrelated prose. It now also asks the
  simplest possible question — **did the model answer at all?** The prompt ends
  "what is 2+2?", so content without a `4` in it is a failure whatever shape the
  noise takes. A check that passes on a configuration you have already proven
  broken is telling you about the check, not the model.

**The relationship that actually holds**, across every configuration tried: the
call appears when the model finishes thinking in **under ~200 characters**, and
does not when it thinks long — whatever made it think long. Every lever below
moves reasoning length around; none changes that relationship.

Things that did NOT fix it, all measured:

| lever | result |
|---|---|
| `thinking_effort=low` | soft hint; same request gave 151 chars once, 9377 another |
| `cache_prompt: false` | no change |
| `temperature` 0 / 0.2 | **worse**, and lengthens reasoning |
| `--reasoning-budget 128` | 3/5, then 0/3 — the forced close *prevents* the call |
| `--reasoning-budget 1024` | 1/3 |
| grammar, `tool_choice=required` | **1/4** |
| grammar, `tool_choice=auto` (lazy) | **0/4** |

Two of those are worth internalising. **A reasoning budget that fires is not
neutral** — interrupting the think section mid-thought reliably produced *no*
call, while every success had the model closing its own think tag. And
**constraining generation to a grammar made it strictly worse**, including the
lazy grammar that only engages after a tool section has already started. That is
the obvious fix, it is what the Kimi K2 parser does, and on K3 it cost the whole
sample. The parser is deliberately left permissive as a result.

The settled config is `--reasoning-budget 1024` (runaway protection, since
unbounded thinking produced a 22,423-character reasoning block and a 24-minute
request) plus `--repeat-penalty 1.0` and `thinking_effort=low`.

### Read the raw tokens before theorising

`--skip-chat-parsing` makes the server hand back everything the model emitted,
unparsed. It should have been the *first* diagnostic and was about the twentieth.
On K3 it ended the argument immediately — a success emits a clean, well-formed
call, and the failures are the model **degenerating**:

```
run 2 (7445 chars): ... 17, 18, 19, 20, 21, 22, ... 172, 173
run 3 (7703 chars): [unrelated SvelteKit Makefile prose] ... 0, 0, 0, 0, 0, 0
```

Counting loops and unrelated text. No parser change addresses that. Everything
measured before this point was chasing a parsing bug that did not exist.

### Diff against the reference implementation, not against your own reasoning

The mainline llama.cpp K3 PR (`pwilkin:kimi-k3-text`) has its own
`common_chat_params_init_kimi_k3`. Reading it found two real defects in ours
that no amount of black-box testing would have:

- Ours ended the top-level rule with `p.rest()`; mainline uses `p.end()`.
  **`p.rest()` swallows anything that fails to match**, so a parse failure and
  "the model emitted nothing" are indistinguishable from the outside. Every
  failure diagnosis was made through that fog.
- Mainline's reasoning uses `until_one_of({THINK_END, RESP_START})` because the
  model *skips its own think closer on short answers*, and its content
  terminators include `TOOLS_START` so a pure tool call — think straight to
  tools, no response section — parses at all.

Its grammar comment explains a result that had already been written off:

> The message closer is part of the trigger rule so that the lazy grammar still
> permits it once tool calls have started — otherwise constrained decoding
> rejects the model's own closing tag.

That is exactly why constraining generation scored 0/4 here. The conclusion
"K3 fights grammars" was wrong; the grammar was built wrong. Ported.

### `tool_choice: required` is unusable on K3, for a mundane reason

Non-lazy grammar means constrained decoding on *every* token against a
**163,840-token vocabulary**. A 200-token completion did not finish in 500
seconds — against 4.21 tok/s unconstrained. The lazy grammar (`tool_choice:
auto`, the default) only engages after the trigger and costs nothing measurable.
Leave it on auto.

### Where that leaves K3

K3 uses the official model-card sampling — temperature 1.0, top_p 0.95, no
repetition penalty. **Chat and agentic use are both reliable**: perplexity 1.33,
tool calls 5/5, the full gate passing, and a real agent loop (Glob then Read,
correct, 234s). It writes 2247 characters of accurate technical prose without a
wobble.

This paragraph previously read "Chat is reliable; agentic use is not", and
attributed that to 2.479 bpw giving up structured-output discipline. **That was
wrong** — the fault was our own KDA path never passing `build_qkv`'s per-step
checkpoint tensors, so speculative decoding corrupted the recurrent state. Left
here because the wrong version was written with more confidence than the right
one, and the difference between them was one more test.

DeepSeek-V4 is still what to reach for when tools matter, on speed: 360 PP and
32 TG against K3's 40 and 4.3.

**The quant ceiling is real and separate.** `UD-Q4_K_XL` is **1508.7 GB** against
1133 GB of RAM and would not fit the 1.3 TB of free disk either. Nothing is
published between it and `UD-Q2_K_XL` (861 GB); everything smaller
(`UD-IQ2_XXS` at 711 GB, the `UD-IQ1_*` and `UD-TQ*` rows) is worse.
Requantising the local file upward recovers nothing — the information is gone at
2 bits — and rebuilding from source means 2.8T parameters of bf16. That bounds
**quality**, not tool calling, and conflating the two is exactly the mistake
above.

So `UD-Q2_K_XL` is the largest K3 this hardware can hold, and its
structured-output unreliability comes with it. The next step up needs 1.5 TB.

**Passing this is still not the same as the harness working.** K3 answers the
full loop over the API and yet a `kimi-opencode run` asking it to read a file
exited 0 after 838s having printed nothing, while the server logged 7067 prompt
and 2619 generated tokens — so the model ran and produced output the harness did
not surface. Server-side correctness and harness integration are separate
claims; do not infer one from the other.

## 10. Per-model flags are per-model: check, do not assume

`-rtr` (run-time repack) is the sharpest example measured here:

| | PP | TG |
|---|---|---|
| DeepSeek-V4 | **+15%** | **+4%** |
| GLM-5.2 | -4% | +0.5% |
| Kimi K3 | **-16%** | 0% |

Same flag, same box, same day: a solid win, a wash, and a serious regression.
It lives in the registry row's `opts`, never in `serve-glm.sh`. Re-measured on
the Q5_K rebuild rather than assumed to carry over — it still holds there
(DeepSeek-V4 331.4 -> 364.6 PP, 27.75 -> 28.56 TG).

Where the three models ended up, against where they started:

| | PP before | PP after | TG before | TG after |
|---|---|---|---|---|
| DeepSeek-V4 | 323.2 | **364.6** | 23.82 | **28.56** |
| GLM-5.2 | 130.4 | 133.7 | 10.68 | **12.43** |
| Kimi K3 | 30.07 | **40.02** | 3.65 | **4.30** |

Tested here and NOT worth adopting, recorded so nobody retries them:

- `--no-mmap`, to get transparent huge pages behind the weights instead of the
  ~200M 4 KB file-backed pages an mmap'd 800 GiB model runs on. TG 28.09 ->
  27.74, and `AnonHugePages` never rose: with `defrag=[madvise]` the kernel will
  not eagerly back a large anonymous region, so the premise never even held.
- **Thread counts above 64.** TG is 3.67 at 32 threads, 3.67 at 64, 3.66 at 96
  and 2.54 at 128. It scales near-linearly to 8 and then hits the bandwidth wall;
  the 128 figure is SMT contention.
- `-muge` (merge up/gate experts) and `-mqkv` (merge Q,K,V) on K3: 39.0-39.8 PP
  and 4.29-4.32 TG against a 39.49 / 4.30 baseline. Both are fusions rather than
  approximations and cost nothing to try, but neither moves this workload.
- **Four-bit non-expert weights** — see the ladder above. Real perplexity cost.
- **Requantizing routed experts to ik's `IQ2_K`.** The premise was that ik's own
  quant types decode faster on CPU than mainline's `IQ*_XS`. Measured on
  DeepSeek-V4 (same model twice, experts at `IQ2_XS` vs `IQ2_K`, ~81 GB each):
  IQ2_XS wins by **35% on PP and 31% on TG**. It is backwards. Publishers'
  `IQ2_XS` experts are already the fast choice.

The pattern across all of them: on a bandwidth-bound workload, only two things
have ever moved the number — reading fewer bytes, and not paying too much CPU per
byte to decode them. Everything that rearranges *how* the work is scheduled has
come back flat.

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
