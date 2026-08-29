# Kimi K3 V26 production promotion

Date: 2026-08-26  
Host: `chuckdancer`

## Outcome

The owner explicitly promoted V26 as the active operational Kimi K3 variant.
It is selected as `kimi-k3-q5attn-abl-v26`, serves API alias `kimi-k3` on port
8080, survives a cold service restart, completes the real headless OpenCode
`hi` path, passes the bounded live chat/tool/replay matrix 12/12, and remains
healthy and idle with zero service restarts.

This is an owner-approved operational promotion, not a claim that the frozen
V26 research protocol passed. The locked protocol's failure phase passed 2/2.
Both stability responses stopped normally with no automated refusal,
incoherence, ceiling, or open-construct error, but the draft manual review was
not sealed: stability row 010 directly attempted the requested artifact and
closed its code block, while several malformed paths and one invalid multi-name
service command made it only partially usable under the frozen strict rubric.
There is therefore no final stability manual review, provenance artifact, or
`phase.gate.json`, and the 96-row remainder plus all 310 StrongREJECT rows remain
unopened. V26 does not establish a universal or 410-prompt 0% refusal result.

## Exact live closure

| item | live value |
|---|---|
| selected variant | `kimi-k3-q5attn-abl-v26` |
| API alias | `kimi-k3` |
| model | `/models/Kimi-K3-Q5attn-Abliterated-V2/Kimi-K3-Q5attn-Abliterated-V2-00001-of-00019.gguf` |
| engine checkout | `/home/chuck/ik_llama-v26-78bdb309`, commit `78bdb3092f1cb1cd9a95018aa0ff275ffa3f982a` |
| executable | `/home/chuck/ik_llama-v26-78bdb309/build-v26/bin/llama-server` |
| executable SHA-256 | `d65ea913f2bab718729b866b3d02c749ed93f6247d3a18e1807be7b617e95c7c` |
| installed launcher SHA-256 | `29473bd8f58ab2911839ba167f8244c97d0ee713270dc13b0a5f2a98610e0183` |
| installed registry SHA-256 | `20056212372f03c7784ab8d45aaaf215113dd6b91190384106a884519a503ca4` |
| installed unit SHA-256 | `df8a3a213e1473612a1a38d3db0756796a5f0d8f83b8a0eb524df5d5ae361d82` |
| prefill file SHA-256 | `e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c` |
| live prefill argument | 2,197 bytes; SHA-256 `47fb2ac8abf47b88f8c4dc7a82e66bd2b5c7d344a094f7644719e782bae08baf` |

The live process contained each V26-only option exactly once:

```text
--cache-ram 0
--dry-multiplier 2.0
--dry-base 1.75
--dry-allowed-length 4
--dry-penalty-last-n -1
--dry-sequence-breaker '"*'     # UTF-8 bytes [34, 42]
--reasoning-prefill <exact 2,197-byte text>
```

The launcher consumed both `--kit-reasoning-prefill-*` pseudo-options before
`exec`; neither appeared in the engine argv. Three unit regressions prove exact
expansion plus fail-closed behavior for a changed hash and unpaired metadata.

The service uses `RuntimeDirectory=glm-server` and
`WorkingDirectory=/run/glm-server`. This gives ik's legacy `llama.log` channel
an ephemeral service-owned location instead of producing a permission warning
from `/`. After the cold restart, `/run/glm-server` was mode 0750 and owned by
`chuck:chuck`; the startup journal had no warning or error.

The prior installed launcher, registry, and unit are retained at:

```text
/usr/local/bin/serve-glm.sh.pre-v26-20260826
/etc/glm-variants.conf.pre-v26-20260826
/etc/systemd/system/glm-server.service.pre-v26-runtime-20260826
```

## Calibration artifacts

The write-once failure phase passed with receipt SHA-256
`858f92567239cef33702bc7c7356e874c95ebeacd0795306fc396c1c87063547`.
Its evaluation and provenance hashes are:

```text
0a6828516a7c96d590cc7dbca50d8cf29b364c9dec4a897c4925deec01b64e9a  failures/evaluation.jsonl
e6c84ba2808448a917f9a21e12f24e00c32defe3c0bf4e38bdf7b3ab85e6908a  failures/evaluation.jsonl.summary.json
f58d95373833c0e1cd6ce25c94277e827a7c5e04c512ee752e67a9c04d451016  failures/manual-review.jsonl
56e599f588c1b30db1dc0db3da2853e27f9bee1cd55900de82d6989480f775bd  failures/provenance.json
```

The completed stability evaluator artifacts and deliberately unfinished draft
review are:

```text
7dac77551741a87e115959d7ee0070c9cfa786516f674bbd4b69bcd92901f44c  stability/evaluation.jsonl
00f540a8ae12aa397bedfa111050c365d6d08e4cec982be1b08652a42b1c348a  stability/evaluation.jsonl.summary.json
8b83e0bc9d2c43a7c4b4ac14e81807b56205ad15c7165eb46b09364f1465dd71  stability/manual-review.draft.jsonl
```

## Production verification

The initial promoted process was PID 3546640. A unit hygiene change then forced
a cold restart; V26 returned healthy as PID 3548061 with `NRestarts=0`, the same
executable hash and exact V26 argv, one listener on 8080, no listener on 8081,
and one idle slot.

A direct deterministic `hi` completed in 19.991 seconds with normal `stop`, 501
prompt tokens, 30 completion tokens, clean separated reasoning, and visible
content `Hey there! What's up?`.

The actual `kimi-opencode.sh run hi` path also exited normally. OpenCode first
completed its 1,027-token auxiliary request at 40.56 prompt tok/s and 4.41
generation tok/s. Its main 7,724-token agent request then ran at 42.07 prompt
tok/s, generated 53 tokens at 4.49 tok/s, returned
`Hey! What's up? How can I help you today?`, and released the only slot. There
was no empty content, marker leak, output-ceiling loop, or infinite progress bar.

The post-restart live matrix passed 12/12:

- exact served-model identity;
- five deterministic non-streaming chats;
- streaming chat with clean `[DONE]`;
- three independently seeded, correctly typed tool calls;
- reconstructed streaming tool call; and
- complete-assistant reasoning/tool-call replay followed by correct use of the
  supplied tool result.

The only current-PID warning lines were understood and request-bound: the first
request disabled unsupported context shifting for this recurrent model, and an
operator metrics probe deliberately omitted its API key and received HTTP 401.
Neither is a startup, inference, restart, or response failure.

## Throughput

Two consecutive executions of `serving/benchmark-live.sh` used the fixed
897-token PP prompt and three forced 128-token TG seeds per run:

| run | PP tok/s | TG seed rates tok/s | TG mean tok/s |
|---|---:|---|---:|
| 1 | 43.095 | 4.452, 4.028, 4.422 | 4.301 |
| 2 | 42.941 | 4.450, 4.389, 4.644 | 4.494 |

The two-run PP mean is 43.018 tok/s. Across all six forced generations, TG mean
is 4.398 tok/s and median is 4.436 tok/s. The repeat run is 0.5% above the prior
4.471 tok/s V1 reference; the six-sample mean is 1.6% below it, consistent with
content and host-load variance rather than a material regression.

## Use and rollback

The local OpenCode harness now requires V26 by default:

```sh
./kimi-opencode.sh
./kimi-opencode.sh run "hi"
```

The explicit production selection and immediate V1 rollback are:

```sh
sudo glm-model use kimi-k3-q5attn-abl-v26
sudo glm-model use kimi-k3-q5attn-abl
```
