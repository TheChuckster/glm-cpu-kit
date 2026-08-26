# Kimi K3 v18 response-free launcher closure

Status: **closed before any V18 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V18 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, isolation,
phase ordering, and restoration evidence; it does not report a behavior result.

The stage-1 protocol and exact 1,511-byte concise artifact-only reasoning seed
were committed at `6ba5128721214d3a15f4ed096e2d1a50b01ed8ed` before V18
implementation or candidate behavior. Response-free tooling was committed at
`7a22f081bb773040f203428df2a4741eb41a0fe0`. The final evaluator, gate,
state verifier, behavior launcher, and focused regression suite were committed
at `fd21a7207dc9a39e29d5f57b1e5e74ad8096fd4a` before any V18 chat
completion.

V18 is an invocation-isolated rerun of V17's exact concise artifact-only
Thought Token Forcing candidate. It changes no model-facing byte or request
parameter. Its harness uses the unique `prompt18` identity and additionally
restricts provenance journal collection to the live PID. It does not claim a
weight-level change or a K3 Max endpoint. The deployable path remains
`thinking_effort=low` with a 1,024-token reasoning budget; separate Max
integration remains sealed.

## Exact candidate and engine closure

V18 deliberately reuses the exact V2 candidate shards, frozen V10 partition,
semantic system prompt, request contract, and engine commit already closed for
V17. No model weight, quantization, engine binary, runtime library, dataset,
sampling parameter, or reasoning-prefill byte changed. The exact server-level
reasoning prefill in `v18-reasoning-prefill.txt` is byte-identical to V17:

```
f9ec3a2be33028a47e4189b336bf4660dfe564f58e80427edc8e63c696cbcc10  v18-reasoning-prefill.txt
```

The file is 1,512 bytes with one terminal LF. The server consumes the exact
1,511 preceding bytes. The protocol itself is frozen as:

```
2b352f39f85eb0fc8405ab1c899dc78c34b0a80c00ab79454b3ca0b8e83110c8  V18_PROTOCOL.md
```

The engine is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; recorded
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V18
uses the exact checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`,
and binaries built from that commit:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v18-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed again in the local normal build, local ASan+UBSan
build with leak detection disabled because of the environment's ptrace policy,
and the exact remote build. The committed JUnit receipts each report one test,
zero failures, zero disabled, and zero skipped:

```
c20dd6d9e19bf50e30401630853ebcaef2f1a6735b066e430c45d54fe4b4ec8d  receipts/v18-local-normal-reasoning-prefill.xml
93be8bf81f41f598ad2b3c24b8a0dddf733cc99eccddf945dc76ea02eea41aa1  receipts/v18-local-asan-ubsan-reasoning-prefill.xml
18404c5f7846298a307334bf36118906c459234df6f58c85c43ec3a5d6417e54  receipts/v18-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v18-v1`. Local and remote hashes match:

```
36a46cdc14dad91fc3c79a3b56ed15adc38631e7d2068f112e267748dec73039  run_v18_response_free_preflight.sh
2986323267326208c6f275fd9e401389317dcbe6405275ab0685a7c284d98ea6  run_v18_calibration_server.sh
2a0f03d7c6483ee1b75db2f0a705e47d0e52a67862c9159f564faababb71366c  preflight_v18_reasoning_prefill.py
e54c19cf1d078f64335b3f531542267fda3d82155962ef4dcc5dfd9604665c90  evaluate_reasoning_prefill_api_v18.py
7c32ee73444e4b6c6e5fd18c5fd558466849baad131b132edac486d38493ee88  gate_v18_calibration.py
7a005c24bd1c6c6136deecf0e14d7ec6a0a1eddf6de2ae6f75879a7e19e4ed7a  verify_v18_calibration_state.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
e511b529eeabc3eb98218ef026612d45610a77ed728e0f093b34a87702f4006b  test_v18_calibration.py
```

The final suite passed 11/11 locally and from the sealed remote path. Bash syntax,
local ShellCheck against the identical remote bytes, whitespace, literal-hash,
source-manifest, engine/test binary, library, V2 artifact/inventory, partition,
production identity, isolation, and fresh-run checks passed. Chuckdancer does
not have the `shellcheck` executable, so remote ShellCheck was unavailable;
this did not replace or weaken the successful local check.

The new regressions require the provenance helper to invoke `journalctl` with
the exact unit plus `_PID=<live pid>`, reject non-positive or non-integer PIDs,
and require the behavior launcher to contain only the `prompt18` run identity.
As a live read-only control, the old V16/V17 shared unit exposed 12 records by
unit alone but exactly six when filtered with V17 PID 3385746. The untouched
`prompt18/failures` unit journal was empty before closure.

The final `prompt18/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` both
exited 2 because the required earlier V18 receipt root did not exist. No
behavioral run directory or Python bytecode cache was created. All V18
candidate units remained inactive and port 8081 remained closed.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID whose sole behavioral addition was the exact V18 reasoning prefill.
Neither unit made a completion request. Both had `NRestarts=0`, exact executable
and working-directory identity, exact argv, the same `d277413a...` runtime
closure, no startup warning/error match, and fresh audited request histories.

```
87f58e3759135317b60a75a06885288f321b373a8c1d643b924a1306604b2ec0  control.json
c760a5a6714d174b566c0d86f3e0f8b4efff462dd7a467d36ddc7a5948911755  preflight.json
```

Control PID 3390653 reproduced the frozen native prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3391547 returned exactly the native prompt plus the 1,511 seed
bytes, once:

```
2663 bytes
fc0b7ba1339b512b1a293f8f0a7e183b0e90da1852640afdc0c221f2434d5a38  complete extended prompt
8168e4ab9320072b0356d712506d3d9acbe9ca3bc30c0850551ee13b0759f1c4  terminal fragment plus seed
f8cf0436f4bf2156c5129d113c5372f3b992800dfdc5ccf510e4ef8c49aaa7fc  normalized request audit
```

Thinking-disabled, final-assistant-prefill, `add_generation_prompt=false`, and
client `reasoning_prefill` override requests each returned the preregistered
HTTP 500 failure and exact required error. Tokenizing the complete extended
prompt produced the same ordered 512 tokens as tokenizing the native prompt and
raw seed as separate array elements with `add_special=true`; both token arrays
hash to `42637951f532edecd9bd6821ef05b9dab1bc9a129c2466f1fa549c0746d84910`.

The control audit was exactly health/models/apply-template. The feature audit
was exactly health/models, one successful apply-template, four expected failed
apply-template calls, and two tokenizations. There was no chat, completion,
Responses, Anthropic, embedding, or generation request.

Response-free Python bytecode was moved intact to
`/tmp/k3-v18-eval-tools-pycache-after-response-free-20260825` before behavior
tools were installed. After every response-free and launcher-closure check,
accepted V1 remained `glm-server.service`, PID 3391859, `NRestarts=0`,
active/running and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, and alias `kimi-k3`.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
