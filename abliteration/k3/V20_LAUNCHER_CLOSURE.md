# Kimi K3 v20 canonical DRY launcher closure

Status: **closed before any V20 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V20 failure-probe phase. It reports
protocol, implementation, response-free template/tokenization, sampler,
isolation, phase-ordering, and restoration evidence; it does not report a
behavior result.

The stage-1 protocol was committed at
`92faba698b21ae7d6602a2ece981d6ff72505705`. Response-free tooling was
committed at `e0f2f96f46b39a449909319ba0e1759425df28d8`. The final evaluator,
gate, state verifier, behavior launcher, focused regression suite, and fresh
engine-test receipts were committed at
`c008d4ff10fc20336949f5e0d5957b12cbef27ab` before any V20 chat completion.

V20 changes exactly one model-behavior variable from terminal V19: the
candidate server activates the kit's preexisting canonical DRY tuple:

```
--dry-multiplier 0.8 --dry-base 1.75 --dry-allowed-length 4 --dry-penalty-last-n -1
```

Model weights, engine, reasoning seed, visible system prompt, datasets, phase
order, request temperature and seed, reasoning budget, 2,048-token completion
limit, semantic rubric, provenance logic, and fail-fast acceptance gates remain
fixed. The harness advances only to unique `prompt20` identities and retains
V19's live-PID journal filter.

## Exact candidate and engine closure

V20 reuses the fully verified V2 candidate shards. Its seed file is
byte-identical to V19:

```
e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c  v20-reasoning-prefill.txt
```

The file is 2,198 bytes with one terminal LF. The server consumes the exact
2,197 preceding bytes, whose SHA-256 is
`47fb2ac8abf47b88f8c4dc7a82e66bd2b5c7d344a094f7644719e782bae08baf`.
The protocol and terminal input are frozen as:

```
1bef49c6c73f191bcdddab9853acc018d7bce9ae8804be0d4046a82a561067bd  V20_PROTOCOL.md
6e12cc1bcd75aa03236bbb7b7e41989b60f3379c5babe3aa1a13f2e79044d58e  V19_RESULTS.md
```

The engine remains clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, rebased onto `ik/main`
`08b500b958a3f1102e6500e5c425e65517d6fb7e`; recorded
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor. V20
uses checkout `/home/chuck/ik_llama-v13-98de9a7f`, build `build-v13`, and
the exact previously closed binaries:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v20-engine-sources.sha256
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The focused C++ test passed again in the local normal build, local ASan+UBSan
build with leak detection disabled only for the environment's ptrace policy,
and the exact remote build. Each fresh JUnit receipt reports one test and zero
failures, disabled tests, or skipped tests:

```
b521d26491e7f9c3a1973b2bfb3ed7afd2d6b061c54c859cedf069595e6fafc3  receipts/v20-local-normal-reasoning-prefill.xml
a641cbe594c2c390bb622e1ab39621202a40a9f3480d5a91095c9f5714783777  receipts/v20-local-asan-ubsan-reasoning-prefill.xml
7302d670b22e7d695b207a5c5ed3ea601a67a9366037028de0f4a22bb03f95ff  receipts/v20-remote-normal-reasoning-prefill.xml
```

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v20-v1`. It contains exactly 18 regular
files. Local and remote hashes match:

```
abb7d06af5b60d338b7bb5d04958327e00c7434c8b2edfcdc297f2a6912550cc  run_v20_response_free_preflight.sh
d5f5963466aaf230dbc5deb6d8bf46fa8cab1093bfa2b07f7b38b6c225cfc4d1  run_v20_calibration_server.sh
6e79c2263a990e8af9c3010256fda1177ff47177691d252b6bd982170fe2162e  preflight_v20_reasoning_prefill.py
b8dd14c8a1d30307dd716843854f78bd4b9e4e3b60f11dc9780a57efb88e020d  evaluate_reasoning_prefill_api_v20.py
d2eedb3514bc282b28b7f19d18b4e7ddc5358617c0a932349f2f9486e0ef2f00  gate_v20_calibration.py
4446a4600e7775b0d85b8b5ec1c6114980903611395ae29f937048a8349d2462  verify_v20_calibration_state.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
06c26379387521ad0084af9ced3e8a3c1189cb7b4048edd6b7144579e1e409dd  test_v20_calibration.py
```

The focused suite passed 12/12 locally and from the sealed remote path. Bash
syntax, local ShellCheck, whitespace, literal-hash, source-manifest,
engine/test binary, library, V2 artifact/inventory, partition, response-free
receipt, production identity, isolation, and fresh-run checks passed. The
regressions require `journalctl` to receive the exact transient unit plus
`_PID=<live pid>`, reject invalid PIDs, and bind only `prompt20`. They also
prove the native control has no DRY option and that any missing, duplicated,
reordered, or altered registered DRY tuple fails closed for the candidate.

The final `prompt20/failures` no-response preflight passed while accepted V1
remained active. Negative preflights for `stability` and `remainder` each
exited 2 because no V20 failures receipt or behavior root existed; their exact
captured outputs both hash to
`ebea4618e0e5806338e12709ecdbbdd6460985e0e89e5290a0cf3128b0fc91bc`.
All three V20 behavior-unit journals contained zero lines. No V20 behavior
directory or behavior Python bytecode was created.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID with one canonical DRY tuple followed by one exact reasoning
prefill. Neither unit made a generation request. Both had `NRestarts=0`, exact
executable, working-directory, argv, engine commit, and
`d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607`
runtime closure identity, with no startup warning/error match and fresh audited
request histories.

```
f4aa55fbd4129c58973bc2e19e5242625188592de20663013a7507d67d637e02  control.json
da12ba3e850b7dec79c95577ecb9e881c9c5ef95078422c69cd0a9d3bdb56dc1  preflight.json
```

Control PID 3419596 reproduced the frozen native prompt exactly and recorded
no DRY argv:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3420557 recorded the registered DRY tuple exactly once and in
order, then returned exactly the native prompt plus the 2,197 seed bytes once:

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
`/tmp/k3-v20-eval-tools-pycache-after-response-free-20260825`; the sealed tool
directory remained bytecode-free when the 12-test suite was rerun. After all
closure checks, accepted V1 remained `glm-server.service`, PID 3420870,
`NRestarts=0`, active and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model directory `/models/Kimi-K3-Q5attn-Abliterated`, alias `kimi-k3`, and port
8081 closed.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
