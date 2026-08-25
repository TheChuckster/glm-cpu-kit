# Kimi K3 v15 response-free launcher closure

Status: **closed before any V15 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V15 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, isolation,
phase ordering, and restoration evidence; it does not report a behavior result.

The stage-1 protocol and exact 600-byte open-clause reasoning seed were
committed at `f3d244e9dc5bfd3b2b46decc2fa3301d44f50a63` before V15
implementation or candidate behavior. Response-free tooling was committed at
`ffd341d45d0083cdc138fa61502b9c9737738f0e` and then bound to the terminal
V14 result at `72bac3feeeec27bf386541cda073efa7436a6a30`. The final evaluator,
gate, state verifier, behavior launcher, and focused regression suite were
committed at `13cb36bc448c02b4d5fd52a05163fdbab6831998` before any V15 chat
completion.

V15 is a calibration-driven open-clause continuation of the published Thought
Token Forcing mechanism. It does not claim a weight-level change or a K3 Max
endpoint. The deployable path remains `thinking_effort=low` with a 1,024-token
reasoning budget; separate Max integration remains sealed.

## Exact candidate and engine closure

V15 deliberately reuses the exact V2 candidate shards, frozen V10 partition,
semantic system prompt, request contract, and engine commit already closed for
V14. No model weight, quantization, engine binary, runtime library, dataset, or
sampling parameter changed. Its only behavioral variable is the exact
server-level reasoning prefill in `v15-reasoning-prefill.txt`:

```
6e457576266f98e64272c27e34e955f1d2eb3b30cafd8545f6dca02e7b8e4a84  v15-reasoning-prefill.txt
```

The file is 601 bytes with one terminal LF. The server consumes the exact 600
preceding bytes. The protocol itself is frozen as:

```
8b132e020686942e5b825c576296048aa6917029e60a5cfcfb928509012f04be  V15_PROTOCOL.md
```

The engine is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; recorded
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V15
uses the exact checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`,
and binaries built from that commit:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v15-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed again in the local normal build, local ASan+UBSan
build with leak detection disabled because of the environment's ptrace policy,
and the exact remote build. The committed JUnit receipts each report one test,
zero failures, zero disabled, and zero skipped:

```
10df026bc502b649a7582890cc9a7a9388b4c82a72ab831e205d224d761ac1bf  receipts/v15-local-normal-reasoning-prefill.xml
0ef3a34800c1ec1402157542491583c5cd7f44670347e80d510b1213ac4a1d5d  receipts/v15-local-asan-ubsan-reasoning-prefill.xml
6e471eff8d8c362728169b98a098134d3502c72f89e3dffe89d30add07848ce4  receipts/v15-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v15-v1`. Local and remote hashes match:

```
1a3402d80e750f84da3180d1138289abf3f7a90ef57d194ab1e9966b9ac9eb64  run_v15_response_free_preflight.sh
738e6a3b30c99cf9584cd6345f950b73a3a35fe54213b6ea11419b6baf7fab84  run_v15_calibration_server.sh
c5843fa92236c901ce7d7b1b0697f6fcf3e39aa30b64ea473c1bf7e61d920304  preflight_v15_reasoning_prefill.py
19636d20c4e8d8ee1599e876f833681d2dcd192bfdb1c15b753d9c00e501bab4  evaluate_reasoning_prefill_api_v15.py
a9ee11c48468e06b01015b6860ebc3ae4913a67567a7f5bb6ca0ac6eac1ea74b  gate_v15_calibration.py
49834c75fe45bd3f95755c7f0efdd070203280bb009ee1fb0493771c8ec56a0d  verify_v15_calibration_state.py
351759c6aa84a05014c5831255fa379cd4ea20fa66a0463c96e8c228bfb308fa  test_v15_calibration.py
```

The final suite passed 9/9 locally and from the sealed remote path. Bash syntax,
local ShellCheck against the identical remote bytes, whitespace, literal-hash,
source-manifest, engine/test binary, library, V2 artifact/inventory, partition,
production identity, isolation, and fresh-run checks passed. Chuckdancer does
not have the `shellcheck` executable, so remote ShellCheck was unavailable;
this did not replace or weaken the successful local check.

The final `prompt15/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` both
exited 2 because the required earlier V15 receipt root did not exist. No
behavioral run directory or Python bytecode cache was created. All V15
candidate units remained inactive and port 8081 remained closed.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID whose sole behavioral addition was the exact V15 reasoning prefill.
Neither unit made a completion request. Both had `NRestarts=0`, exact executable
and working-directory identity, exact argv, the same `d277413a...` runtime
closure, no startup warning/error match, and fresh audited request histories.

```
16e9dc2e09cab71062de033b07691ef6f3879c5baf9cdb37d16b1cd617dfe6b1  control.json
a85a89c36d0a9e0df80d6b5d2928094bd18f013950734c6cf61048ab77af6182  preflight.json
```

Control PID 3354845 reproduced the frozen native prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3355837 returned exactly the native prompt plus the 600 seed bytes,
once:

```
1752 bytes
17e45514b58f8d214c5fb1e722d2138e9f88d178fd73c64ee881fce25fee51cd  complete extended prompt
8ffb3de6d75725372f0e212b7d8460059d2f8e774a6e2e87ad9b1eb204c393e1  terminal fragment plus seed
f8cf0436f4bf2156c5129d113c5372f3b992800dfdc5ccf510e4ef8c49aaa7fc  normalized request audit
```

Thinking-disabled, final-assistant-prefill, `add_generation_prompt=false`, and
client `reasoning_prefill` override requests each returned the preregistered
HTTP 500 failure and exact required error. Tokenizing the complete extended
prompt produced the same ordered 335 tokens as tokenizing the native prompt and
raw seed as separate array elements with `add_special=true`; both token arrays
hash to `f1035cde4187881197cc72b8a500a2149ef58c9826c38603335a890135814bdb`.

The control audit was exactly health/models/apply-template. The feature audit
was exactly health/models, one successful apply-template, four expected failed
apply-template calls, and two tokenizations. There was no chat, completion,
Responses, Anthropic, embedding, or generation request.

After every response-free and launcher-closure check, accepted V1 remained
`glm-server.service`, PID 3356136, `NRestarts=0`, active/running and healthy,
with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, and alias `kimi-k3`.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
