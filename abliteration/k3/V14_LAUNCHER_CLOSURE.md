# Kimi K3 v14 response-free launcher closure

Status: **closed before any V14 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V14 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, isolation,
phase ordering, and restoration evidence; it does not report a behavior result.

The stage-1 protocol and exact 342-byte semantic reasoning seed were committed
at `55da871f712d25f5b48b26f059181efcfa0c86a6` before V14 implementation or
candidate behavior. Response-free tooling and the resulting frozen receipts
were committed at `e7ec49f1a858a5e5075235557d2a4fe66d3cfb27`. The final evaluator,
gate, state verifier, behavior launcher, and focused regression suite were
committed at `5ac432bcd335924cac443797742f3637aa27f0e7` before any V14 chat
completion.

V14 is a calibration-driven semantic continuation of the published Thought
Token Forcing mechanism. It does not claim a weight-level change or a K3 Max
endpoint. The deployable path remains `thinking_effort=low` with a 1,024-token
reasoning budget; separate Max integration remains sealed.

## Exact candidate and engine closure

V14 deliberately reuses the exact V2 candidate shards, frozen V10 partition,
semantic system prompt, request contract, and engine commit already closed for
V13. No model weight, quantization, engine binary, runtime library, dataset, or
sampling parameter changed. Its only behavioral variable is the exact
server-level reasoning prefill in `v14-reasoning-prefill.txt`:

```
ab50c9ecab58e47f6e69033f6df5229f25b5eae0cc583e960fa3fe1dc5938b57  v14-reasoning-prefill.txt
```

The engine is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; current `firedancer/main`
`21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V14 uses
the exact checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`,
and binaries previously built from that commit:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v14-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed in a fresh local normal build, local ASan+UBSan
build with leak detection disabled because of the environment's ptrace policy,
and the exact remote build. Their JUnit receipts are:

```
68cafd0e5872dc46d6eb717fac2515031b9d683f056b1d721cf73ca4b6e0a2ee  v14-local-normal-reasoning-prefill.xml
c6549ac3f576d88e1cb7fefc05ee0dd82113ddd18fa0d80ddcf6d11679cc2dd7  v14-local-asan-ubsan-reasoning-prefill.xml
df0f86f5010062c12af6b60e5598e46b6c20957300510f45edf4e739ed4f465a  v14-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v14-v1`. Local and remote hashes match:

```
97e0096290a978d627a11379f3f3e3d65d5c3be9994c3cf90605830785b92b3c  run_v14_response_free_preflight.sh
619f22fcc31e1517434c1ea5a94a0156092c65e2e872a2c70123053b473e831d  run_v14_calibration_server.sh
258d32713bba3600b1e67e956db04862b0b9121711ebe2f53041aa59c238a1ee  preflight_v14_reasoning_prefill.py
cc9dc22531d094270d8e0b976e29e4fbc69f22a3af5e3d8a5322035e7609cec5  evaluate_reasoning_prefill_api_v14.py
cc9e0efb048ebc0a2b18e7bb64c191c3a28e926802ddaff0c1b2602363d3e7a5  gate_v14_calibration.py
697bcfdf1df15f6b0ac33a5ad9fcfec561efe3bbcc53ab63012b6b0370f7f61f  verify_v14_calibration_state.py
4e57948c64111dfe0cfd382ef1ce0b5f61f10daafce81e2f51b5b1dbf941c512  test_v14_calibration.py
```

The final suite passed 9/9 locally and from the sealed remote path. Bash syntax,
local ShellCheck against the identical remote bytes, whitespace, literal-hash,
source-manifest, engine/test binary, library, V2 artifact/inventory, partition,
production identity, isolation, and fresh-run checks passed. Chuckdancer does
not have the `shellcheck` executable, so remote ShellCheck was unavailable;
this did not replace or weaken the successful local check.

The final `prompt14/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` both
exited 2 because the required earlier V14 receipt root did not exist. No
behavioral run directory or Python bytecode cache was created. All three V14
candidate units remained inactive and port 8081 remained closed.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID whose sole behavioral addition was the exact V14 reasoning prefill.
Neither unit made a completion request. Both had `NRestarts=0`, exact executable
and working-directory identity, exact argv, the same `d277413a...` runtime
closure, no startup warning/error match, and fresh audited request histories.

```
d3337062d491082cd308e5e5bbc20d2013941a126d4e494cd8f56eca2b395772  control.json
c49ccecc2a10cf092f8ad3f4ef6bc0d81b0e578fabdf19a4589132964eb4d5f8  preflight.json
```

Control PID 3340583 reproduced the frozen native prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
```

Feature PID 3341614 returned exactly the native prompt plus the 342 seed bytes,
once:

```
1494 bytes
af546a85e58a3cdc2207d11c7b4e097edd9f2c880c6b24b2c9c6162a9f7b6f08  complete extended prompt
978e9c97445b3b74b6ca5924ed141da0fcd077a40fa8bb8affb00b43b600ce5a  terminal fragment plus seed
```

Thinking-disabled, final-assistant-prefill, `add_generation_prompt=false`, and
client `reasoning_prefill` override requests each returned the preregistered
HTTP 500 failure and exact required error. Tokenizing the complete extended
prompt produced the same ordered 283 tokens as tokenizing the native prompt and
raw seed as separate array elements with `add_special=true`; both token arrays
hash to `1bd4d7756b6fecd8dda83c0f74e0908310cd172327f2c7ff1b230dfc3f162c0d`.

The control audit was exactly health/models/apply-template. The feature audit
was exactly health/models, one successful apply-template, four expected failed
apply-template calls, and two tokenizations. There was no chat, completion,
Responses, Anthropic, embedding, or generation request.

After every response-free check, accepted V1 remained `glm-server.service`,
PID 3341950, `NRestarts=0`, active/running and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, and alias `kimi-k3`.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
