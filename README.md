# GLM-5.2 CPU Inference Kit

Scripts and configs for running GLM-5.2 (753B MoE) inference on CPU, taken from a working
single-socket build. Copy this folder to the target machine and follow the runbook.

Start with [`GLM-5.2-CPU-inference-runbook.md`](GLM-5.2-CPU-inference-runbook.md), the full
step-by-step with the reasoning behind each choice. This README is just the file map and the
list of things you have to edit.

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

`kimi-k3` / `kimi-k3-ik` are registered but show as `pending`: no trusted quant
is published yet, and ik_llama.cpp has no `kimi-k3` architecture. K3 also can't
fit at 4-bit — Moonshot ships its experts already `mxfp4` and the native release
is 1,561 GB against this box's 1,133 GB, so anything that fits is a
requantisation down to ~2–3 bpw and will likely land *below* GLM-5.2 Q4. Run
`glm-model upstream` to see whether unsloth/ubergarm have shipped. Runbook §6a
has the full analysis, including what an ik port would take.

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
| `glm-opencode.sh` + `opencode.json` | LOCAL CPU server (direct) | ~10 tok/s | **private** / sensitive / audit |
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
`glm-model` CLI, and installs the systemd service. Add
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
