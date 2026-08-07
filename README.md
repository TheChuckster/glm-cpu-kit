# GLM-5.2 CPU Inference Kit

Scripts and configs for running GLM-5.2 (753B MoE) inference on CPU, taken from a working
single-socket build. Copy this folder to the target machine and follow the runbook.

Start with [`GLM-5.2-CPU-inference-runbook.md`](GLM-5.2-CPU-inference-runbook.md), the full
step-by-step with the reasoning behind each choice. This README is just the file map and the
list of things you have to edit.

Adding another model to a box that is already serving one?
[`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) is the repeatable procedure, generalised
from adding DeepSeek-V4-Flash and Kimi K3 — including why every change must be
additive, and the two times a shared default broke serving anyway.

The reference target is a dual-socket box (for example 2x EPYC 9B45). NUMA handling (runbook
section 4) is the main thing that differs from a single socket: get the model's memory
interleaved across both sockets.

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
| `validate-model.sh` | prove a new quant loads, stays coherent, keeps reasoning out of `content`, and can call a tool — on a spare port | 6b |

**Speculative decoding is on, and it is close to free money for agent work.**
`--spec-type ngram-mod` drafts from n-grams already in the context and verifies
a run of them in one pass — lossless, and near-free on a bandwidth-bound box.
GLM does a code edit at **23-30 tok/s against a 12.4 baseline** (+89% to +143%)
and generic prose at exactly 12.40, i.e. no cost when it cannot fire. It does
nothing for K3 or DeepSeek-V4, which always reason, and reasoning prose repeats
nothing. See [`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) §8.

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
glm-model download kimi-k2.7-code  # ~584 GB, resumable, size-verified
glm-model use kimi-k2.7-code       # switch + restart, waits for readiness
glm-model status                   # what is ACTUALLY loaded right now
glm-model upstream                 # what HF publishes now, and does it fit this box
```

Only one variant is resident at a time — they are 155–800 GB and `--mlock` pins
them, so a switch is a full reload of minutes, not a hot swap.

**Registered today:** `base` (GLM-5.2, 440 GB), two GLM abliterated variants,
two Kimi at genuine 4-bit — `kimi-k2.7-code` (unsloth UD-Q4_K_XL, 584 GB) and
`kimi-k2.6` (ubergarm Q4_X, 4.549 bpw, built to match Moonshot's official int4)
— and three **DeepSeek-V4-Flash-0731** quants at ~155 GB. Kimi K2.x is ~1T total
but only ~32B active vs GLM's ~40B, and token generation is
memory-bandwidth-bound, so **Kimi generates faster here** for ~145 GB more RAM.

**DeepSeek-V4-Flash is the fast one.** 284B total but only ~13B active, with
experts shipped natively at fp4, so a lossless MXFP4 conversion is ~155 GB —
about a third of GLM's footprint and roughly a third of its bytes-per-token.
Runbook §6b covers why the usual quant ladder does not apply to it, why it needs
its own engine build, and what is still open upstream for tool calling.

**Kimi K3 works, on a forked engine.** ik_llama.cpp has no `kimi-k3`
architecture and ikawrakow declined to add one ([ik #2203](https://github.com/ikawrakow/ik_llama.cpp/issues/2203)),
so the port lives on [`TheChuckster/ik_llama.cpp`](https://github.com/TheChuckster/ik_llama.cpp)
branch `kimi-k3`, built into `build-k3` and selected by the registry's `engine`
field. Perplexity 1.33 against a 1.55 reference; **40 tok/s prompt processing**
and **4.3 tok/s generation, rising to 7.5-9.6 on repetitive or code-shaped
output** where n-gram speculation fires (from 30.1 / 3.65 when the port first
ran). Tool calls 5/5, and it drives a real agent loop. The fused AVX-512 delta-net kernel now handles K3's
per-channel KDA gate (it used to decline, dropping 69 of 93 layers to the scalar
path) — worth +29% on prompt processing and, measured A/B, *nothing* on
generation. Generation is at the memory wall: K3 reads **71.2 GiB per token** and
gets 281 GB/s doing it, against 350 GB/s for GLM and a 460.8 GB/s theoretical
peak — normal efficiency, just 2.2x more bytes. Runbook §6c and
[`porting/k3/`](porting/k3/) have the full account, including the eleven silent
bugs it took to get there and why attributing the generation speed to that kernel
was wrong.

**The surprise is which bytes.** K3 is 92.8% routed experts by file size, but
only 16 of 896 are active, so experts are just 19% of what a token reads — the
other 81% is non-expert weights the recipe ships at Q8_0. So a smaller K3 quant
buys almost no speed, while requantising the 7.2% of the file nobody optimises
would be worth ~1.4x. `porting/k3/bytes_per_token.py` computes this for any GGUF
and is worth running before assuming a model is slow.

**The working local agents are `glm-opencode.sh` with `deepseek-v4-flash-0731`
or `glm-5.2`.** Both verified end to end on a "read this file and tell me what it
does" task: DeepSeek-V4 invoked the file tool and answered correctly in **41
seconds**; GLM chained two tools (Glob then Read) in **67 seconds**. Tool calls stream properly
(7 incremental deltas, `finish_reason: tool_calls`). The same task on K3 ran 838
seconds and printed nothing.

**All three production configs pass the full gate** (`serving/validate-model.sh`),
which is the reproducible version of that claim rather than a remembered one:

| | coherence | reasoning | tools | 5-run | streaming | replay | degeneration |
|---|---|---|---|---|---|---|---|
| DeepSeek-V4 (`-rtr`, spec) | ✅ | ✅ | ✅ | 5/5 | ✅ 7 deltas | ✅ | ✅ |
| GLM-5.2 (spec) | ✅ | ✅ | ✅ | 5/5 | ✅ 6 deltas | ✅ | ✅ |
| Kimi K3 (spec + `--spec-ckpt-mode cpu`) | ✅ | ✅ | ✅ | 5/5 | ✅ 6 deltas | ✅ | ✅ |
| *K3 with spec, default ckpt mode (rejected)* | ✅ | ✅ | ❌ | **0/5** | ❌ 0 deltas | ✅ | ✅ |

The last row is why the gate exists — and the row above it is one flag away.
`--spec-ckpt-mode auto` resolves to `gpu-fallback` on a CPU-only build and never
restores the recurrent state of K3's 69 KDA layers when a draft is rejected. With
`cpu` it is correct **and 46-68% faster** than no speculation. Any model with
recurrent or hybrid attention needs that flag before speculation is safe.

**All three models emit tool calls 5/5.** K3 only got there after
`--spec-type ngram-mod` was removed from its row: speculative decoding is
supposed to be lossless, and on K3 it is not — 0/5 with it on, 5/5 with it off,
same build and sampling. It stays enabled for GLM and DeepSeek-V4, which measure
5/5 with it. See [`ADDING-A-MODEL.md`](ADDING-A-MODEL.md) §8.

**K3 is capped at `UD-Q2_K_XL` here** — the higher-bpw `UD-Q4_K_XL` is 1508.7 GB
against 1133 GB of RAM, with nothing published in between. That bounds its
quality ceiling, but it is *not* what was breaking its tool calls.

**Tool calls work on K3** as of fork `c10d1e2` — it emits them as nested
`<|open|>argument key=...<|sep|>` tags rather than JSON, and needed a parser plus
`--repeat-penalty 1.0` (the global 1.1 penalises structured tag output badly
enough to derail the model mid-call). Runbook §6c.

`kimi-k3-ik` stays `pending` — ubergarm has published no K3 quant, so that row is
still a guess about filenames. Run `glm-model upstream` to check.

**Per-model flags.** Field 9 (`opts`) of a registry row is appended last to the
server command line. It exists because "turn thinking off" is not one flag:
GLM takes `--chat-template-kwargs '{"enable_thinking": false}'`, Kimi's template
has no such variable and needs `--reasoning off`, and DeepSeek-V4 has neither —
it always reasons, and takes `--reasoning-format deepseek` (or
`--reasoning-budget 0`). Passing GLM's flag to Kimi fails *silently* — no error,
just thinking output that breaks the harness.

**Per-model engine.** Field 8 (`engine`) names the build tree under
`~/ik_llama.cpp` that serves a variant — empty means `build`, the default.
Architectures land in ik continuously, so the commit that can serve a new model
is always much newer than the one already serving the others well; DeepSeek-V4
needed an engine three weeks newer than the one this box had been running GLM
on. Repointing the single global engine to gain one model silently re-rolls the
dice on every other model, so instead a new architecture gets its own build tree
and nothing else moves until it is proven.

Two abliterated variants are registered and they are **not** equivalent.
`abliterated` (frz1, Q4_K_M, 455 GB) matches the base model's quant class and
size almost exactly, but the publisher is unknown and the capability cost of
their ablation is unverified. `abliterated-q3` (huihui-ai, UD-Q3_K_M, 343 GB) is
from the established abliteration publisher, but that repo has no Q4-class quant
at all — so it costs a full quantisation step, which on a 745B MoE is likely a
larger quality hit than the ablation itself.

Note that `llama-server` does not reject a request naming a different alias than
the loaded model: asking for `glm-5.2-abliterated` while `base` is live returns
the base model with no error. `glm-model status` compares `/v1/models` against
the selected variant, and is the only reliable check.

### `harness/` (how you talk to it — three paths, pick by privacy vs speed)
| Script | Backend | Speed | Use for |
|---|---|---|---|
| `glm-opencode.sh` + `opencode.json` | LOCAL CPU server (direct) | ~12-32 tok/s | **private** / sensitive / audit — **this is the working agent setup** |
| `kimi-opencode.sh` | LOCAL, Kimi K3 on the forked engine | ~3.7 tok/s | K3 specifically — see the caveats in the script |
| `glm-opencode-together.sh` | Together AI cloud | ~200-350 tok/s | fast everyday coding |
| `glm-opencode-cloud.sh` | any OpenAI-compatible provider (env) | varies | OpenRouter / DeepInfra / Z.ai / Surplus / etc. |
| `litellm-config.yaml` + `proxy.sh` + `glm.sh` | Claude Code via litellm | - | only if you must use Claude Code (fragile) |

**Local vs cloud = privacy vs speed.** Local is private but ~10 tok/s; cloud is 20-35x faster and cheap,
but your prompts/code go to the provider. Keep sensitive or audit work on the local box; use cloud for
everyday speed. **All cloud scripts read the API key from env or a key file - never hardcoded, never in this repo.**

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

It builds **only** the default `build` tree, from upstream ik. Registry rows with a
non-empty `engine` field (`build-ds4`, `build-k3`) need their trees built separately —
that separation is the point, so a new architecture cannot disturb a working model — and
`install.sh` now lists which ones are missing rather than letting `glm-model use` fail with
a missing-binary error. Kimi K3 additionally needs
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
1. Use ik_llama.cpp, not mainline: about 7x MoE prompt-processing throughput.
2. Use Q4_K_XL, not Q8: near-lossless, half the bandwidth per token.
3. Turn thinking off and add a repeat penalty, or harnesses break (400s) and loop.
4. opencode direct beats Claude Code plus litellm: no fragile Anthropic translation.
5. TG is memory-bandwidth-bound: NUMA interleaving is your only real TG lever on CPU.
6. Pair the context limits: harness `limit.context` < server `--ctx-size` (kit: 60K < 64K). Otherwise a
   big context grinds for HOURS (O(n^2) prompt-processing) or errors instead of compacting. Never set 1M.
   See "Context window: the trap" in the runbook.
