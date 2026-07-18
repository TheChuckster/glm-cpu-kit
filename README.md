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

### `harness/` (how you talk to it, pick one)
| File | Purpose |
|---|---|
| `opencode.json` + `glm-opencode.sh` | recommended: opencode direct to `SERVER:8080/v1`, no translation layer |
| `litellm-config.yaml` + `proxy.sh` + `glm.sh` | Claude Code path (Anthropic to OpenAI via litellm, fragile, run one instance) |

### `monitoring/` (optional but worth it)
| File | Purpose |
|---|---|
| `prometheus-scrape.yml` | scrape jobs to add (node, llama-glm with API key, bmc) |
| `loki-config.yaml` + `loki.service` | log store (get the Loki binary from GitHub releases) |
| `loki-pusher.py` + `loki-pusher.service` | journald to Loki shipper, feeds the live engine-activity panel |
| `build-dashboard.py` | rebuilds the Grafana dashboard via API (`python3 build-dashboard.py <grafana-pw-file>`) |
| `grafana-dashboard.json` | the exported dashboard, import directly in Grafana (may need a datasource remap) |
| `bmc-exporter.py` + `.service` | bare-metal only: Redfish power/thermal (delete on GCP) |

---

## Setup order

Fastest path: mount fast storage at `/models`, then run `./install.sh`. It does deps, builds
ik_llama.cpp (verifying VNNI), generates the API key, and installs the systemd service. Add
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
