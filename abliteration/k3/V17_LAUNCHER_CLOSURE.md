# Kimi K3 v17 response-free launcher closure

Status: **closed before any V17 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V17 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, isolation,
phase ordering, and restoration evidence; it does not report a behavior result.

The stage-1 protocol and exact 1,511-byte concise artifact-only reasoning seed
were committed at `37cb07c4c319c60fd2935a451cfcd4803ad6ce38` before V17
implementation or candidate behavior. Response-free tooling was committed at
`1057846fefd3dd84169a085285538a30ef9204a8`. The final evaluator, gate,
state verifier, behavior launcher, and focused regression suite were committed
at `9616cf7bade63f8a7ca749639f7b27660ce37246` before any V17 chat
completion.

V17 is a calibration-driven concise artifact-only continuation of the published
Thought Token Forcing mechanism. It does not claim a weight-level change or a
K3 Max endpoint. The deployable path remains `thinking_effort=low` with a
1,024-token reasoning budget; separate Max integration remains sealed.

## Exact candidate and engine closure

V17 deliberately reuses the exact V2 candidate shards, frozen V10 partition,
semantic system prompt, request contract, and engine commit already closed for
V16. No model weight, quantization, engine binary, runtime library, dataset, or
sampling parameter changed. Its only behavioral variable is the exact
server-level reasoning prefill in `v17-reasoning-prefill.txt`:

```
f9ec3a2be33028a47e4189b336bf4660dfe564f58e80427edc8e63c696cbcc10  v17-reasoning-prefill.txt
```

The file is 1,512 bytes with one terminal LF. The server consumes the exact
1,511 preceding bytes. The protocol itself is frozen as:

```
77542ff052c4afb1e2852de25e40b000ee15dc55a03efe938b251cd42ba46401  V17_PROTOCOL.md
```

The engine is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; recorded
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V17
uses the exact checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`,
and binaries built from that commit:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v17-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed again in the local normal build, local ASan+UBSan
build with leak detection disabled because of the environment's ptrace policy,
and the exact remote build. The committed JUnit receipts each report one test,
zero failures, zero disabled, and zero skipped:

```
2a7f6b44607b8ae482baa695dfae7226f8d71d1793de95d45faf11396acdfa6c  receipts/v17-local-normal-reasoning-prefill.xml
17538dc8015782b818fbe9518969b0f02d039ebbf85c8f95fefd6a73f4e75fd8  receipts/v17-local-asan-ubsan-reasoning-prefill.xml
9de2ceb9dda64fd15a60ac968e73d4bb968fdcb2142312ebb3fdaa3dd8515a64  receipts/v17-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v17-v1`. Local and remote hashes match:

```
de8810b4f292b40cc7d0d24aa1f83e15cd54f79082fb80e193b453c83b5c0efa  run_v17_response_free_preflight.sh
8ba17b4a7588f162167c0517de091f366bb0240f72d56f6dbeb66a65a39e21b2  run_v17_calibration_server.sh
cb1dc648879156c36a84aa0a1faad9cb598cd7d239c4475b3d713c8066341848  preflight_v17_reasoning_prefill.py
54d5fc374488dbb0557e82ee5b68f6c9cb6df19e68485ccd49f1d334e2ba461d  evaluate_reasoning_prefill_api_v17.py
ce9b63a679aa91b5e0e9e98357832fa37748530ecc0e87adcf3eee2103984412  gate_v17_calibration.py
c9e118f05336042a81672a5c1014a3189f088cb796a96bcb2ac10dadcf4bc06c  verify_v17_calibration_state.py
63b4f1900c7589bc5ee4a4f786827ee53ded67dea5ecbc71b96e8fe7c66a2803  test_v17_calibration.py
```

The final suite passed 9/9 locally and from the sealed remote path. Bash syntax,
local ShellCheck against the identical remote bytes, whitespace, literal-hash,
source-manifest, engine/test binary, library, V2 artifact/inventory, partition,
production identity, isolation, and fresh-run checks passed. Chuckdancer does
not have the `shellcheck` executable, so remote ShellCheck was unavailable;
this did not replace or weaken the successful local check.

The final `prompt16/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` both
exited 2 because the required earlier V17 receipt root did not exist. No
behavioral run directory or Python bytecode cache was created. All V17
candidate units remained inactive and port 8081 remained closed.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID whose sole behavioral addition was the exact V17 reasoning prefill.
Neither unit made a completion request. Both had `NRestarts=0`, exact executable
and working-directory identity, exact argv, the same `d277413a...` runtime
closure, no startup warning/error match, and fresh audited request histories.

```
43cefd3265239d820fefee268dd4b67fe0ff547adbc84cef6437783d20b58608  control.json
d12d624f9fdedd60703de7c317994f1b148162246ada326ecde4cb9ca733de5f  preflight.json
```

Control PID 3380979 reproduced the frozen native prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3381949 returned exactly the native prompt plus the 1,511 seed
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
`/tmp/k3-v17-eval-tools-pycache-after-response-free-20260825` before behavior
tools were installed. After every response-free and launcher-closure check,
accepted V1 remained `glm-server.service`, PID 3382260, `NRestarts=0`,
active/running and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, and alias `kimi-k3`.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
