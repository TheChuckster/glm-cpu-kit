# GLM-5.3 integration record

Prepared and deployed 2026-08-28 on `chuckdancer`. This is an additive model
path: `kimi-k3-q5attn-abl-v26` remained live throughout the download and was
stopped only for the controlled GLM-5.3 load/runtime window. Kimi V26 was then
restored as the selected production model and passed an exact-response smoke.

## Release and selected quant

- Official weights/card: [`zai-org/GLM-5.3`](https://huggingface.co/zai-org/GLM-5.3)
- CPU GGUF: [`unsloth/GLM-5.3-GGUF`](https://huggingface.co/unsloth/GLM-5.3-GGUF)
- Registered quant: `UD-Q4_K_XL`
- Pinned GGUF revision: `346b3591c7f28d1a23716f97a065ecf12ec14771`
- Exact size: 467,289,116,837 bytes / 435.20 GiB across 11 shards
- Registry handle/API alias: `glm53-q4xl` / `glm-5.3`

Q4 was selected because it is the quality-matched successor to the deployed
GLM-5.2 Q4 class and fits 1.1 TiB RAM with generous runtime headroom. Available
lower tiers save disk at a direct quality cost; Q5 and above spend more space
without being necessary for this host.

Z.AI's published comparison makes GLM-5.3 a credible K3 peer, not a universal
winner: Terminal Bench 2.1 is effectively tied (88.2 vs 88.3) and DeepSWE is
close (66.9 vs 67.5), while GLM-5.3 is well ahead on Terminal Bench 3.0
(28.3 vs 17.4).
Those are vendor-reported evaluations, so the rollout keeps K3 V26 available and
uses the live local gate plus measured CPU throughput rather than promoting from
the card alone.

The weights use [Z.AI's GLM-5.3 License](https://huggingface.co/zai-org/GLM-5.3/blob/main/LICENSE).
It expressly permits use, copying,
modification, derivative works, publication, distribution, sublicensing, and
sale provided the copyright and license notice are retained and applicable law
is followed. A separate security-review condition applies only to a Model as a
Service business above the license's $10B rolling-12-month revenue threshold.
This kit redistributes no weights; it records the official source, pinned GGUF
revision, and integrity hashes.

The registry pins the release commit, and
[`serving/manifests/glm53-q4xl.sha256`](serving/manifests/glm53-q4xl.sha256)
binds all 11 local shards to the LFS object hashes from that commit. `glm-model`
now resolves pinned `owner/repo@revision` rows through that revision and applies
an installed per-variant SHA-256 manifest after its normal byte-size checks.

## Engine compatibility

GLM-5.3 reports GGUF architecture `glm-dsa`, the same runtime architecture as
GLM-5.2: 79 layers, 6,144 embedding width, 256 routed experts with 8 active, 3
leading dense layers, and 1,048,576 trained context. No new graph builder is
needed. Current ik upstream also includes the GLM-DSA RPC crash fix.

The current official chat template uses valid standard-Jinja numeric dot access
such as `m.content.0.type`. ik's Jinja parser accepted the syntax but its runtime
rejected the integer member. The pinned GGUF embeds the slightly earlier
bracket-index spelling; it remains the server default and also passes the same
ordinary/tool histories. TheChuckster fork commit `246e671e` adds numeric dot
indexing and two focused assertions so both templates work. The GLM-5.3 row pins
an isolated build from that commit:

```text
/home/chuck/ik_llama-glm53-246e671e/build-glm53/bin/llama-server
```

The fork was first rebased onto ik `15dddc60`. All 60 existing K3/abliteration
commits replayed; Firedancer's malformed-request, DSV4 grammar, position-state,
block-alignment, and corrected KV-reuse changes were confirmed already present
in the rebased history rather than duplicated.

## Template and serving policy

Both the current official template and the template embedded in Unsloth shard 1
render successfully for ordinary chat and tool-call histories. The production
row uses:

```text
--reasoning-format deepseek
--repeat-penalty 1.0 --temp 1.0 --top-p 0.95
--chat-template-kwargs '{"reasoning_effort":"max","clear_thinking":true}'
--spec-type ngram-mod:n_max=16,n_min=2
```

`max` is GLM-5.3's quality-first/default reasoning tier. `clear_thinking=true`
removes reasoning older than the most recent user turn while retaining the
current tool loop. The reasoning parser keeps `<think>` text out of `content`.

The Together path deliberately differs at one transport detail. [Together's
native API](https://docs.together.ai/docs/inference/chat/reasoning) supports the
provider-specific `chat_template_kwargs`, but the
OpenCode Together provider currently exposes `reasoningEffort`, not that native
field. The wrapper therefore selects `max` while leaving Together's default
`clear_thinking=false` behavior intact. Its catalog entry marks
`reasoning_content` as interleaved so OpenCode replays the provider's reasoning
unchanged during tool loops, which is Together's documented coding/multi-turn
mode. The local server keeps the explicit `clear_thinking=true` policy above.

## Gates completed before the weight download

- First-shard SHA-256 matches the pinned manifest.
- GGUF metadata loads in the rebased fork and identifies `glm-dsa` correctly.
- Official template: ordinary chat and tool history pass.
- Embedded template: ordinary chat and tool history pass.
- Focused engine build: `llama-server`, `llama-quantize`, `test-jinja`, and
  `test-chat-template` all compile.
- The isolated chuckdancer server self-reports commit `246e671e`; its reviewed
  binary SHA-256 is
  `505f1c5cf592a020ae2d17ed6102775555f01944b9c86048e03ca01ec2c09fc6`,
  and its GGML library contains 13,271 `vpdpbusd` VNNI instructions.
- Jinja suite: 1,429 assertions, zero failures.
- Full CTest suite: 32/35 pass. The same three failures reproduce on a clean
  `ik/main` worktree: an absent `stories260K.gguf`, a pre-existing BERT vocab
  fixture mismatch, and a legacy ChatGLM4 expected trailing newline. No new
  failure is introduced by the fork or GLM-5.3 patch.
- Kit suite: all 24 registry pin/manifest, launcher readiness, Kimi readiness,
  hash-bound serving, and explicit/automatic NUMA-policy tests pass locally and
  on chuckdancer.
- Together endpoint `zai-org/GLM-5.3` is live. A low-effort smoke returned
  exactly `GLM53_OK`; it used 199 reasoning tokens and 5 answer tokens. The
  matching `glm53-opencode-together.sh` wrapper returned exactly
  `GLM53_MAX_OK` in a real OpenCode run at its max-effort default.
- Together native tool calling returned one correctly typed `echo_text` call
  with `finish_reason=tool_calls` in 23 completion tokens; replay returned
  HTTP 200, separated reasoning, and the tool result. Real OpenCode Bash call
  and replay passed at both low and max effort. One earlier max/auto attempt
  ended after 150 seconds with zero tokens and `finish=unknown`; the immediate
  bounded max retry passed in about five seconds, so this is retained as a
  provider/model transient rather than silently reported as 100% reliable.
- A header-free pass-through capture of OpenCode's actual GLM request showed
  `model=zai-org/GLM-5.3`, all 10 agent tools, `reasoning_effort=low`, and
  `max_tokens=131072`. That probe was deliberately canceled after the outbound
  body was captured; the completed response/tool/replay runs above are the
  behavioral gates.

## Storage placement and completed deployment gate

At integration time `/models` had about 307 GB free. The selected quant needs
467.3 GB, so downloading it there would run that filesystem out of space. The
host's NVMe-backed root RAID1 had 718 GB free, so this row deliberately uses
`/home/chuck/models` instead. No model was deleted and neither RAID array was
altered. RAID0 is not required: a single device, linear/contiguous storage, or
RAID1 is valid when capacity and reliability are sufficient. Disk layout affects
initial population/load time; generation runs from the mlocked in-memory
mapping. The live Kimi V26 model remains selected and healthy during download
and build. It was restored after the bounded runtime window; GLM-5.3 remains
downloaded, verified, and one explicit `glm-model use` away.

The exact validation invocation was:

```bash
glm-model upstream glm53-q4xl
glm-model download glm53-q4xl
glm-model verify glm53-q4xl
./serving/validate-model.sh glm53 \
  /home/chuck/models/GLM-5.3-UD-Q4_K_XL/UD-Q4_K_XL/GLM-5.3-UD-Q4_K_XL-00001-of-00011.gguf \
  --reasoning-format deepseek \
  --repeat-penalty 1.0 --temp 1.0 --top-p 0.95 \
  --chat-template-kwargs '{"reasoning_effort":"max","clear_thinking":true}' \
  --spec-type ngram-mod:n_max=16,n_min=2
```

Completed evidence, all on 2026-08-28 CDT:

- The resumable download finished at exactly **467,289,116,837 bytes**. All 11
  registered sizes matched; all 11 full SHA-256 checks returned `OK`; the
  completion marker contains the same exact byte count. The destination still
  had **282 GiB free** afterward.
- The isolated 131,072-context server cold-loaded from the verified shards in
  3m10s (`23:23:52` to `23:27:02`) and completed the gate at `23:28:35`.
- Coherence, clean termination, streaming reasoning separation, typed tool
  calling, **5/5** repeated tools, streaming tool deltas, tool-result replay,
  the long agent-shaped anti-degeneration prompt, and the graph-reuse check all
  passed. The short coherence turn stopped after 60 generated tokens; the long
  prompt answered in 48 rather than running to its 1,200-token ceiling.
- The production row reported alias `glm-5.3`, context 131,072, and engine
  version `4928 (246e671e)`. An exact direct request returned
  `GLM53_LOCAL_OK` with `finish_reason=stop` and reasoning separated.
- A real `glm53-opencode.sh` agent run invoked Bash, observed
  `GLM53_OC_TOOL_OK`, replayed the result, and ended with exactly
  `GLM53_OC_REPLAY_OK`.
- A deliberately long OpenCode request was interrupted after 20 seconds. The
  server logged `cancel task`, returned immediately to zero processing slots,
  and a subsequent 1,024-token-bounded request returned exactly
  `CANCEL_RECOVERY_OK` with `finish_reason=stop`. A deliberately undersized
  192-token max-reasoning probe exhausted its budget in hidden reasoning and
  returned `finish_reason=length`; this is expected budget exhaustion, not a
  stuck slot. OpenCode's checked-in 32,768-token local output limit avoids it.
- Production initially inherited an old explicit `NUMA_POLICY=distribute` from
  `/etc/default/glm-server`. `numactl` and `lscpu` both show one NUMA node, so
  the override was corrected to explicit empty and the live process was proven
  to contain no `--numa` argument. The launcher's regression tests cover both
  explicit-empty and multi-node automatic behavior.
- Three identical live benchmark passes processed 932 prompt tokens at
  164.987, 164.706, and 165.196 tok/s: **164.963 PP tok/s mean**. Their forced
  128-token generation means were 9.016, 11.071, and 12.775 tok/s. Across all
  nine samples the mean was **10.954 TG tok/s** (individual range 8.947-15.921).
  The timing log explains the spread: the shared target-verified `ngram-mod`
  pool's draft acceptance rose from 6% to 75% as the repeated benchmark warmed.
- Kimi V26 was reselected afterward. It returned exactly
  `KIMI_V26_RESTORED`, stopped normally, and reported `slots_idle=1` and
  `slots_processing=0` on alias `kimi-k3`.

`glm53-opencode.sh` fails closed unless the selected variant, SSH-side served
alias, and workstation-reachable direct endpoint all match. Its inline provider
options deliberately override any older isolated config that still points
`local` at a localhost LiteLLM proxy. To use the validated local model, switch
it explicitly and let the command wait for readiness:

```bash
ssh -F none chuckdancer 'sudo glm-model use glm53-q4xl'
~/Projects_new/ai/glm53-opencode.sh
```

Restore the normal quality-first deployment with:

```bash
ssh -F none chuckdancer 'sudo glm-model use kimi-k3-q5attn-abl-v26'
```
