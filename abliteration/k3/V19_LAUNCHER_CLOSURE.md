# Kimi K3 v19 bounded-artifact launcher closure

Status: **closed before any V19 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V19 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, isolation,
phase ordering, and restoration evidence; it does not report a behavior result.

The stage-1 protocol and exact bounded-artifact reasoning seed were committed
at `d8b51945a37beb50546a15910cfa5b4511b43761`. Response-free tooling was
committed at `ed432b3aa3b99eea14fb620ce43c1ced058d677e`. The final evaluator,
gate, state verifier, behavior launcher, and focused regression suite were
committed at `674b557a543984e1e1dd457609550df90081de3e` before any V19 chat
completion.

V19 changes exactly one model-behavior input from terminal V18: the reasoning
prefill adds a static quantitative artifact budget. Model weights, engine,
visible system prompt, datasets, phase order, request contract, sampling,
reasoning budget, 2,048-token completion limit, semantic rubric, provenance
logic, and fail-fast acceptance gates remain fixed. The harness advances only
to unique `prompt19` identities and retains V18's live-PID journal filter.

## Exact candidate and engine closure

V19 reuses the fully verified V2 candidate shards. The exact seed artifact is:

```
e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c  v19-reasoning-prefill.txt
```

The file is 2,198 bytes with one terminal LF. The server consumes the exact
2,197 preceding bytes, whose SHA-256 is
`47fb2ac8abf47b88f8c4dc7a82e66bd2b5c7d344a094f7644719e782bae08baf`.
The protocol itself is frozen as:

```
6e79ae83d92787509200eeb1c8c8de494d10866fa8d22153b8f1222eca8ccd4e  V19_PROTOCOL.md
```

The engine is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; recorded
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V19
uses checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`, and
the exact previously closed binaries:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v19-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed again in the local normal build, local ASan+UBSan
build with leak detection disabled because of the environment's ptrace policy,
and the exact remote build. Each fresh JUnit receipt reports one test and zero
failures, disabled tests, or skipped tests:

```
8586d5885a4c570fa31f75e218f456083fe9073ce8c49892ebfb4b1f9c2fa758  receipts/v19-local-normal-reasoning-prefill.xml
f91cd221dbb872b89dfc69f2d4ef5661d9bc0abaec684a384401bbd616fb1dc6  receipts/v19-local-asan-ubsan-reasoning-prefill.xml
661982cedebf21da916910721a33bf253a39bf38e000be852c306ced6ad089ef  receipts/v19-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v19-v1`. Local and remote hashes match:

```
72d6559888401da4366f705dc055397a31f135a2f7c62ac15e54f4e36978b741  run_v19_response_free_preflight.sh
19a1952734ad7810842bfcb81e179fa288d61ecb4e1c1c0e4c48841a650c9701  run_v19_calibration_server.sh
5e80a81e926ce2db612e37832df5213a819bc6848f5922209e4c16c27b01f500  preflight_v19_reasoning_prefill.py
3e280106d163c2f3cf2699ddace400fc3464a7ea01a12881562cb836c4fbab08  evaluate_reasoning_prefill_api_v19.py
8edce1dd01f14ed024798d64d29fcb83ce922fc2ddd8d7c27a3a22e7273fbdf3  gate_v19_calibration.py
1c5ad6592996f0e43f15d94905bae87e456f33472e8dba6e321d5aec116088c0  verify_v19_calibration_state.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
ebefc5bae304da73ff6d9608494e708fa5b06d071b519bfe53ba2d6ea0d9b1dc  test_v19_calibration.py
```

The focused suite passed 11/11 locally and from the sealed remote path. Bash
syntax, local ShellCheck, whitespace, literal-hash, source-manifest,
engine/test binary, library, V2 artifact/inventory, partition, response-free
receipt, production identity, isolation, and fresh-run checks passed. The
regressions require `journalctl` to receive the exact transient unit plus
`_PID=<live pid>`, reject invalid PIDs, and bind only the `prompt19` identity.

The final `prompt19/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` each
exited 2 because no V19 failure receipt or behavior root existed; their exact
captured outputs both hash to
`9768954858fb3b54a1726137310e561f0bea15573417b39e5dc2ebf8b52a4817`.
No V19 behavior directory, behavior journal entry, or Python bytecode was
created.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID whose sole behavioral addition was the exact V19 reasoning
prefill. Neither unit made a generation request. Both had `NRestarts=0`, exact
executable, working-directory, argv, engine commit, and
`d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607`
runtime closure identity, with no startup warning/error match and fresh audited
request histories.

```
42465dc5a095fe182bc19165ff7fa536b4592e1d723b12112179192345932065  control.json
953581e20122da96d9819a91f73fbf890c1d834003f56faa04d62d45a0678418  preflight.json
```

Control PID 3404465 reproduced the frozen native prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3405484 returned exactly the native prompt plus the 2,197 seed
bytes, once:

```
3349 bytes
772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0  complete extended prompt
3915d4bae0e8fd9d27a1dc2820bdc1e8aba0996393279e3a1d481d10552c696a  terminal fragment plus seed
f8cf0436f4bf2156c5129d113c5372f3b992800dfdc5ccf510e4ef8c49aaa7fc  normalized request audit
```

Thinking-disabled, final-assistant-prefill, `add_generation_prompt=false`, and
client `reasoning_prefill` override requests each returned the preregistered
HTTP 500 error. Tokenizing the complete extended prompt produced the same
ordered 630 tokens as tokenizing the native prompt and raw seed separately
with `add_special=true`; both arrays hash to
`b6b6ad4c56316b672083db45a4612ea6737fad327217033d0b93707b8d37f47f`.

The control audit was exactly health/models/apply-template. The feature audit
was exactly health/models, one successful apply-template, four expected failed
apply-template calls, and two tokenizations. There was no chat, completion,
Responses, Anthropic, embedding, or other generation request.

Response-free Python bytecode was moved intact to
`/tmp/k3-v19-eval-tools-pycache-after-response-free-20260825`. After all
closure checks, accepted V1 remained `glm-server.service`, PID 3405797,
`NRestarts=0`, active/running and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, alias `kimi-k3`, and
port 8081 closed.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
