# Frontier MoE CPU Inference Kit

Scripts and configs for running Kimi K3, GLM-5.2, and DeepSeek-V4-Flash on CPU,
taken from the working `chuckdancer` deployment. The repository began as the
GLM-5.2 kit; its registry, launcher, validation gate, and harnesses are now
model-family aware.

Start with [`GLM-5.2-CPU-inference-runbook.md`](GLM-5.2-CPU-inference-runbook.md), the full
step-by-step with the reasoning behind each choice. This README is just the file map and the
list of things you have to edit.

Adding another model to a box that is already serving one?
[`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) is the repeatable procedure, generalised
from adding DeepSeek-V4-Flash and Kimi K3 — including why every change must be
additive, and the two times a shared default broke serving anyway.

The reference target is a dual-socket box (for example 2x EPYC 9B45). NUMA handling (runbook
section 4) is the main thing that differs from a single socket: get the model's memory
interleaved across both sockets. `serve-glm.sh` now auto-detects this — `--numa distribute`
when `numactl` reports more than one node — along with the physical core count, because
both are properties of the machine and the previous defaults were the values that happened
to be right on the box the kit was written on.

---

## File map

### `serving/` (the core, minimum to run)
| File | Purpose | Runbook |
|---|---|---|
| `download-model.sh` | fetch GLM-5.2 Q4_K_XL from HF (IPv4, parallel, resumable) | 5 |
| `gen-api-key.sh` | create `~/.glm-api-key` | 6 |
| `serve-glm.sh` | the server launcher (NUMA-aware, thinking-off, anti-repetition) | 6 |
| `glm-server.service` | systemd unit (survives reboot, `LimitMEMLOCK=infinity`) | 7 |
| `glm-variants.conf` | registry of servable models (GLM, Kimi, DeepSeek-V4) with per-model engine + flags | 6a, 6b |
| `glm-model` | list / download / switch / track which model is served | 6a |
| `validate-model.sh` | hard gate for load, coherence, termination, reasoning separation, tools, streaming, replay, and long-prompt degeneration — on a spare port | 6b |
| `smoke-k3-live.py` | bounded K3 regression matrix against an already-loaded direct endpoint, with exact served-model identity | 6c |
| `benchmark-live.sh` | measure PP/TG against the already-loaded model without allocating a second copy | 8 |

**Speculative decoding is on, and it is close to free money for agent work.**
`--spec-type ngram-mod` drafts from n-grams already in the context and verifies
a run of them in one pass — lossless, and near-free on a bandwidth-bound box.
GLM does a code edit at **23-30 tok/s against a 12.4 baseline** (+89% to +143%)
and generic prose at exactly 12.40, i.e. no cost when it cannot fire. K3 can
also benefit on repetitive/code-shaped output, but only with the recurrent
checkpoint fix now in the fork. See [`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) §8.

**Every model here got ~17% faster generation** by requantizing only its
non-expert tensors to Q5_K (GLM 10.68 -> 12.43, DS4 23.82 -> 27.75, K3
3.67 -> 4.30 tok/s), with perplexity unchanged inside its error bar in all three
cases and prompt processing slightly up as well. Registered as the `-q5attn`
rows; the recipe, the full quant-type ladder, and its two traps are
[`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) §7. Q5_K rather than Q6_K is not a
rounding call: Q6_K is bigger, slower AND worse.

`porting/k3/bytes_per_token.py` answers "how fast *should* this model be?" from
the GGUF alone — exact bytes read per generated token, split into always-read and
routed-expert. Token generation is bandwidth-bound, so that number over the box's
~350 GB/s ceiling is the speed limit, and comparing it against measured tok/s says
whether a model is slow or just large.

**Switching models.** `glm-model` picks which registered variant
`glm-server.service` serves:

```sh
glm-model list                     # registered, downloaded, live
glm-model verify kimi-k3-q5attn-abl # local derivative: prove all 19 shards exist
glm-model use kimi-k3-q5attn-abl    # validated, manually abliterated K3
glm-model status                   # what is ACTUALLY loaded right now
glm-model upstream                 # what HF publishes now, and does it fit this box
```

Only one variant is resident at a time — they are 155–800 GB and `--mlock` pins
them, so a switch is a full reload of minutes, not a hot swap.

**Registered today:** `base` (GLM-5.2, 440 GB), its Q5-attention and abliterated
siblings, Kimi K2.6/K2.7, Kimi K3 base/Q5-attention, a validated local K3
Q5-attention abliteration, and DeepSeek-V4-Flash variants. `chuckdancer`
currently selects `kimi-k3-q5attn-abl`; the state is persisted by `glm-model`, while a fresh install
still falls back to `base` until an operator selects and downloads another row.

**DeepSeek-V4-Flash is the fast one.** 284B total but only ~13B active, with
experts shipped natively at fp4, so a lossless MXFP4 conversion is ~155 GB —
about a third of GLM's footprint and roughly a third of its bytes-per-token.
Runbook §6b covers why the usual quant ladder does not apply to it and the
validation that let its once-separate engine tree converge onto the shared build.

**Kimi K3 is the quality-first local deployment, on a forked engine.** Upstream
ik_llama.cpp has no `kimi-k3`
architecture and ikawrakow declined to add one ([ik #2203](https://github.com/ikawrakow/ik_llama.cpp/issues/2203)),
so the port lives on [`TheChuckster/ik_llama.cpp`](https://github.com/TheChuckster/ik_llama.cpp)
branches `main` and `kimi-k3`. On 2026-08-24 the complete 48-commit stack was
rebased onto upstream `ad26e68b`, reconciled with Firedancer's `kimi-k3` and
`main-patches` branches, and published at `41c443ba` on both fork branches. Its
production fix in `b7cf5a4a` stops at K3's first complete message trailer instead of letting
a missing EOG turn a finished answer into an output-limit loop; the follow-up
commit `7e7bdb3d` exercises every partial stop prefix, canonical and missing closers,
incremental response/tool parsing, and both lazy and required tool grammars. A
clean release build, focused sanitizer run, and every K3/parser/delta-net test
pass. The full suite passes 26/29; its three failures are unrelated fixture or
configuration issues documented in the validation record.

The Q5attn source has perplexity **1.3253 +/- 0.031**. Its manually projected
`kimi-k3-q5attn-abl` derivative was indistinguishable in the paired deployment
check: **1.7526 +/- 0.0185 -> 1.7533 +/- 0.0184** on the fixed short-context
protocol. The post-deploy sample measured **42.868 tok/s** on a fresh 897-token
prompt and **4.471 tok/s generation** (mean of three forced 128-token samples), rising
to **7.5-9.6 tok/s** on repetitive or code-shaped output where n-gram
speculation fires (from 30.1 / 3.65 when the port first
ran). Tool calls pass 5/5; a post-deploy probe also returned a correctly typed
two-argument tool call. The fused AVX-512 delta-net kernel now handles K3's
per-channel KDA gate (it used to decline, dropping 69 of 93 layers to the scalar
path) — worth +29% on prompt processing and, measured A/B, *nothing* on
generation. Generation is at the memory wall: K3 reads **71.2 GiB per token** and
gets 281 GB/s doing it, against 350 GB/s for GLM and a 460.8 GB/s theoretical
peak — normal efficiency, just 2.2x more bytes. Runbook §6c and
[`porting/k3/`](porting/k3/) have the full account, including the eleven silent
bugs it took to get there and why attributing the generation speed to that kernel
was wrong.

A deterministic live `hi` regression on 2026-08-23 exposed a termination bug:
K3 produced the right reply, then repeated `<|close|>message<|sep|>` until all
300 test tokens (8,000 under OpenCode) were consumed. The strict parser then
returned raw tagged content, while OpenCode showed only a progress bar.
`d39033a5` registers that completed message trailer as a stop. The identical
request now returns clean separated reasoning/content in **29 tokens**, including
through the LiteLLM streaming route OpenCode uses. `serving/smoke-k3-live.py`
then passed **11/11** direct-server cases and **9/9** through LiteLLM, covering
multiple seeds, streamed chat, typed tools, streamed tools, tool-result replay,
and a 7,835-token agent-shaped prompt. Finally, the exact headless OpenCode path
evaluated 7,313 prompt tokens, generated a clean 37-token greeting, and exited
normally in 183 seconds. Deliberately disconnecting a separate long generation
also logged a server-side cancellation and returned the only slot to idle about
one second later.

**The surprise is which bytes.** K3 is 92.8% routed experts by file size, but
only 16 of 896 are active, so experts are just 19% of what a token reads — the
other 81% is non-expert weights the recipe ships at Q8_0. So a smaller K3 quant
buys almost no speed, while requantising the 7.2% of the file nobody optimises
would be worth ~1.4x. `porting/k3/bytes_per_token.py` computes this for any GGUF
and is worth running before assuming a model is slow.

**All three local agent paths work.** On the same "read this file and tell me
what it does" task, DeepSeek-V4 finished in **41 seconds**, GLM in **67
seconds**, and K3 in **234 seconds**, each invoking the required tools. Use
`kimi-opencode.sh` for the strongest local reasoning model and expect its large
prompt plus always-on reasoning to make it the slowest path.

**Recorded runs for all three production configs pass every gate section** in
`serving/validate-model.sh`:

| | coherence | reasoning | tools | 5-run | streaming | replay | degeneration |
|---|---|---|---|---|---|---|---|
| DeepSeek-V4 (`-rtr`, spec) | ✅ | ✅ | ✅ | 5/5 | ✅ 7 deltas | ✅ | ✅ |
| GLM-5.2 (spec) | ✅ | ✅ | ✅ | 5/5 | ✅ 6 deltas | ✅ | ✅ |
| Kimi K3 Q5-attention (spec, default checkpoint mode) | ✅ | ✅ | ✅ | 5/5 | ✅ 6 deltas | ✅ | ✅ |

The validator now returns nonzero when *any* section fails; the prior version
could print `FAIL` from its Python checks and still exit successfully. Mock
controls prove that the historical K3 marker loop, output-limit exhaustion, and
a stream truncated before `[DONE]` all fail the process gate. For an already
resident K3, use `serving/smoke-k3-live.py` to avoid loading a second 788 GiB
copy merely to exercise the same live API paths.

The old K3 failure under default checkpoint mode was a graph-builder bug, not a
required serving flag: its KDA path failed to pass the per-step SSM/conv state
to `build_qkv`. Rebased fork commit `e3b9f045` wires those checkpoints. Default
and explicit per-step modes now agree, so the registry deliberately carries no
`--spec-ckpt-mode` workaround.

**All three models emit tool calls 5/5.** K3 keeps n-gram speculation enabled
now that rejected drafts restore recurrent state correctly. The live deployment
also passed a fresh structured tool probe after the engine rebase. See
[`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) §8.

**K3 is capped at `UD-Q2_K_XL` here** — the higher-bpw `UD-Q4_K_XL` is 1508.7 GB
against 1133 GB of RAM, with nothing published in between. That bounds its
quality ceiling, but it is *not* what was breaking its tool calls.

**Tool calls work on K3** — it emits them as nested
`<|open|>argument key=...<|sep|>` tags rather than JSON, and needed a parser plus
`--repeat-penalty 1.0` (the global 1.1 penalises structured tag output badly
enough to derail the model mid-call). The current verified fork tip is
`41c443ba` (the termination code itself is `b7cf5a4a`). Runbook §6c.

`kimi-k3-ik` stays `pending` — ubergarm has published no K3 quant, so that row is
still a guess about filenames. Run `glm-model upstream` to check.

**Per-model flags.** Field 9 (`opts`) of a registry row is appended last to the
server command line. It exists because reasoning control is not one flag: GLM
takes `--chat-template-kwargs '{"enable_thinking": false}'`; Kimi K2.x needs
`--reasoning off`; K3 always reasons and uses `--reasoning-format deepseek`
plus `thinking_effort`; DeepSeek-V4 also always reasons. Passing GLM's flag to
Kimi fails *silently* — no error, just the wrong prompt behavior.

**Per-model engine.** Field 8 (`engine`) names the build tree under
`~/ik_llama.cpp` that serves a variant — empty means `build`, the default.
Architectures land in ik continuously, so the commit that can serve a new model
is always much newer than the one already serving the others well; DeepSeek-V4
needed an engine three weeks newer than the one this box had been running GLM
on. Repointing the single global engine to gain one model silently re-rolls the
dice on every other model, so instead a new architecture gets its own build tree
and nothing else moves until it is proven.

**Local derivatives.** Registry field 2 normally names a Hugging Face repo.
Use `local:<provenance-label>` for a reproducibly built local derivative such
as an abliterated projection. `glm-model verify` then checks the registered
shards and writes the normal size-bound completion marker, while `download`
fails closed and `upstream` performs no misleading network lookup.

The GLM abliterated variants are **not** equivalent.
`abliterated` (frz1, Q4_K_M, 455 GB) matches the base model's quant class and
size almost exactly, but the publisher is unknown and the capability cost of
their ablation is unverified. `abliterated-q3` (huihui-ai, UD-Q3_K_M, 343 GB) is
from the established abliteration publisher, but that repo has no Q4-class quant
at all — so it costs a full quantisation step, which on a 745B MoE is likely a
larger quality hit than the ablation itself.

`kimi-k3-q5attn-abl` is the verified abliterated equivalent to the Q5-attention
deployment. It was built locally by projecting a refusal direction from the
published methodology's harmful-minus-harmless activations at layers 56-73,
then requantizing only the 279 selected attention-output tensors. All 2,294
non-target tensors—including all 276 routed-expert tensors—remain byte-identical
to `kimi-k3-q5attn`. Fresh evaluator-only 100 harmful + 100 harmless runs under
the same binary/settings reduced hash-bound manual harmful refusal from **95%
to 82%** (13 paired removals, 0 additions, exact McNemar p=0.000244), held
harmless refusal at **0%**, and produced zero termination failures across all
400 baseline/candidate responses. Perplexity, tool use, replay, streaming,
long-context, graph reuse, OpenCode greeting, and agentic tool canaries passed.
The scored production executable is pinned independently by SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`;
the upstream rebase preserved the patch stack exactly according to `range-diff`.
The published `kimi-k3-abl` Q2 row remains experimental and not downloaded;
`kimi-k3-q5attn` remains the immediate rollback.

Note that `llama-server` does not reject a request naming a different alias than
the loaded model: asking for `glm-5.2-abliterated` while `base` is live returns
the base model with no error. `glm-model status` compares `/v1/models` against
the selected variant, and is the only reliable check.

### `harness/` (how you talk to it — three paths, pick by privacy vs speed)
| Script | Backend | Speed | Use for |
|---|---|---|---|
| `glm-opencode.sh` + `opencode.json` | LOCAL CPU server (direct) | ~12-32 tok/s | **private** / sensitive / audit — **this is the working agent setup** |
| `ds4-opencode.sh` | LOCAL, DeepSeek-V4-Flash via litellm | ~28.6 tok/s | **fastest local agent** — prefer this for real work |
| `kimi-opencode.sh` | LOCAL, Kimi K3 Q5-attention | ~4.49 tok/s | **quality-first local reasoning**; slow prefill on a fresh agent session |
| `kimi-opencode-together.sh` | Together AI, Kimi K3 | provider speed | K3 with `max` reasoning by default; `KIMI_REASONING_EFFORT=high` to reduce cost/latency |
| `qwen38-opencode-together.sh` | Together AI, Qwen3.8 2.4T A95B | provider speed | closest current open-weight reasoning peer; `xhigh` by default |
| `ds4-pro-opencode-together.sh` | Together AI, DeepSeek V4 Pro 0813 | provider speed | lower-cost 1M-context agent alternate; `max` by default |
| `together-opencode.sh` + `together-opencode.json` | Together AI, selectable | provider speed | shared native-OpenCode router; K3/max is the fallback model |
| `glm-opencode-together.sh` | Together AI cloud | ~200-350 tok/s | fast everyday coding |
| `glm-opencode-cloud.sh` | any OpenAI-compatible provider (env) | varies | OpenRouter / DeepInfra / Z.ai / Surplus / etc. |
| `litellm-config.yaml` + `proxy.sh` + `glm.sh` | Claude Code via litellm | - | only if you must use Claude Code (fragile) |

**Local vs cloud = privacy vs speed.** Local is private but ~10 tok/s; cloud is 20-35x faster and cheap,
but your prompts/code go to the provider. Keep sensitive or audit work on the local box; use cloud for
everyday speed. **All cloud scripts read the API key from env or a key file - never hardcoded, never in this repo.**

The frontier Together wrappers intentionally keep K3/max as the default. The
[Qwen3.8 open-weight model card](https://huggingface.co/Qwen/Qwen3.8-2.4T-A95B)
puts it essentially level with [K3](https://www.together.ai/blog/kimi-k3-guide)
on HLE and close on GPQA, but not ahead of K3 across the broader coding/agent
suite; [DeepSeek V4 Pro](https://www.together.ai/models/deepseek-v4-pro-0813)
is a cost/routing alternative rather than an overall K3 replacement. The shared
launcher validates each model's actual effort names
(`xhigh` for Qwen, `max` for K3/DeepSeek), applies the same setting in both the
TUI and `run` modes, and raises OpenCode's otherwise-too-small per-step output
cap. New catalog entries are additive, so older OpenCode snapshots can use the
new endpoints without replacing native provider behavior.

### `monitoring/` (optional but worth it)
| File | Purpose |
|---|---|
| `prometheus-scrape.yml` | scrape jobs to add (node, llama-glm with API key, bmc) |
| `loki-config.yaml` + `loki.service` | log store (get the Loki binary from GitHub releases) |
| `loki-pusher.py` + `loki-pusher.service` | journald to Loki shipper, feeds the live engine-activity panel |
| `build-dashboard.py` | rebuilds the Grafana dashboard via API (`python3 build-dashboard.py <grafana-pw-file>`) |
| `grafana-dashboard.json` | the exported dashboard, import directly in Grafana (may need a datasource remap) |
| `bmc-exporter.py` + `.service` | bare-metal only: Redfish power/thermal (delete on GCP) |

### `cooling/` (bare-metal only — see [`cooling/README.md`](cooling/README.md))
| File | Purpose |
|---|---|
| `src/` + `Cargo.toml` | `smc-fand`: PI fan control, static musl binary, no deps |
| `smc-fand.service` + `.env` | systemd unit and tuning; `ExecStopPost` hands fans back to the BMC on any exit |
| `smc-fand-calibrate.service` | one-shot sweep that measures which fans cool what |
| `smc-fand-watchdog.*` | independent timer that forces BMC control if the daemon stalls |
| `smc-fand-alerts.yml` | Prometheus alerts — saturation is the leading indicator, not emergency |

Two things the stock BMC curve gets wrong on this workload: quiet low-RPM fans
trip its fan-failure threshold and cause a full-speed rev every ~20s, and it
cools the CPU while ~1 TB of DDR5 is what actually limits sustained inference.

Control is organised around **thermal domains** (sensors sharing a limit) rather
than fan zones, with an **authority matrix** deciding which fans serve which
domain. You configure how hot a DIMM may get; you do not configure which fans
cool it. `smc-fand --calibrate` measures the matrix by sweeping each zone and
watching what moves — so fan-to-zone mapping and intake-versus-exhaust never
have to be worked out by hand. Unknown authority means fully coupled, which is
always safe: louder, never hotter.

---

## Setup order

Fastest path: mount fast storage at `/models`, then run `./install.sh`. It does deps, builds
ik_llama.cpp (verifying VNNI), generates the API key, installs the model registry and the
`glm-model` CLI, and installs the systemd service.

It builds **only** the default `build` tree. Every registry row now uses it —
the per-variant `engine` field is still there and still supported, but no row
pins it, because all three model families validate on one commit. If you pin a
row at a separate tree while porting an architecture, `install.sh` lists which
trees are missing rather than letting `glm-model use` fail with a missing-binary
error. The default tree must be built from
[the fork](https://github.com/TheChuckster/ik_llama.cpp), since upstream ik has no
`kimi-k3` architecture. Add
`--download` to also pull the ~440 GB model. Then benchmark NUMA and thread counts and set up
the harness.

Or do it by hand:
1. Provision and RAID0 NVMe into `/models`, install deps (runbook section 1).
2. `numactl --hardware`, understand your NUMA nodes (section 3).
3. Build ik_llama.cpp with `GGML_NATIVE=ON`; verify VNNI via objdump (sections 4 and 5).
4. `serving/download-model.sh`.
5. `serving/gen-api-key.sh`, then install and edit `serving/glm-server.service`, `systemctl enable --now`.
6. Benchmark NUMA and thread counts, keep the winner in `serve-glm.sh` (section 7).
7. Harness: opencode direct (or litellm and Claude Code).
8. Monitoring: node_exporter to Prometheus (add `prometheus-scrape.yml` jobs), then Grafana and Loki.

## Placeholders you must edit
- `serving/glm-server.service`: `REPLACE_WITH_YOUR_USER` (x2).
- `serving/serve-glm.sh`: override `MODEL_DIR`/`IK_LLAMA`/`THREADS` via env if your paths differ.
- `harness/litellm-config.yaml`: `SERVER_IP`, `PASTE_YOUR_GLM_API_KEY_HERE`.
- `monitoring/prometheus-scrape.yml`: `PASTE_YOUR_GLM_API_KEY_HERE`.
- `monitoring/loki-pusher.service` and `bmc-exporter.service`: `REPLACE_WITH_YOUR_USER`.
- `harness/opencode.json`: `SERVER_IP`, export `GLM_API_KEY`, and keep `limit.context` **below** the server `--ctx-size`.
- `cooling/smc-fand.env`: zone-to-fan mapping, sensor names and setpoints are board-specific. `install.sh` does **not** install this — follow [`cooling/README.md`](cooling/README.md), and map the zones before enabling the service.

## GCP-specific swaps
- Delete `bmc-exporter.py` / `.service` and the `bmc` scrape job; a cloud VM has no BMC. Use
  Cloud Monitoring for power and temperature, or skip it.
- Everything else (node_exporter, llama `/metrics`, Loki, Grafana, `build-dashboard.py`) works as-is.
- No Tailscale or BMC jump needed; SSH via GCP directly.

## Main takeaways (see the runbook for detail)
1. Use the `TheChuckster/ik_llama.cpp:kimi-k3` fork: it carries K3 plus the
   validated GLM/DS4 engine base.
2. Choose a tested quant that fits with OS/compute headroom. For K3 here, the
   local Q5-attention rebuild is the best validated speed/quality point; the
   larger base quant is slightly slower with no measured PPL advantage.
3. Keep reasoning, sampling, cache, and repetition settings per model. K3 always
   thinks and its structured tool tags specifically require repeat penalty 1.0.
4. opencode direct beats Claude Code plus litellm: no fragile Anthropic translation.
5. TG is memory-bandwidth-bound: NUMA interleaving is your only real TG lever on CPU.
6. Pair the context limits: harness `limit.context` < server `--ctx-size`. The unit ships
   `CTX=131072`; `opencode.json` ships 60000, which is deliberately conservative rather than
   matched, because prefill is O(n^2) and a cold 120K first token costs hours. Otherwise a
   big context grinds for HOURS (O(n^2) prompt-processing) or errors instead of compacting. Never set 1M.
   See "Context window: the trap" in the runbook.
