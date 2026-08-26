# Kimi K3 v23 high-multiplier-DRY response-free closure

Status: **complete without a V23 chat completion on 2026-08-26**. This record
closes stage 2 of [`V23_PROTOCOL.md`](V23_PROTOCOL.md). Accepted V1 was the only
production candidate before and after the isolated checks. The V23 behavior
root remained absent throughout this closure.

## Frozen change and engine identity

V23 restores V21's four-token trigger and changes only its DRY multiplier:

```
--dry-multiplier 2.0 --dry-base 1.75 --dry-allowed-length 4 --dry-penalty-last-n -1
```

The model, native chat template, visible system prompt, reasoning prefill,
full-context window, default sequence breakers, request contract, and engine
remain exact. The feature argv contains those four options exactly once and in
that order. It does not pass a sequence-breaker override. The registered CLI
sentinel `-1` is preserved and the engine's effective `/props` value is the
131,072-token context size.

The engine checkout is clean at
`23695c7a444dcfaaf892bebfefb4a4a8394e3c37`. Its 13-path changed-source
manifest has SHA-256
`04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693`.
The exact isolated executable is
`13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a`,
and both transient PIDs reproduced mapped-executable closure SHA-256
`90336d5afaae0c3c0fc2d4ae1f9709fe7f7428e2612be08e4a3c615d32c18886`.

## Rejected first attempt and correction

The first response-free attempt stopped at the control `/props` check after
exactly three successful GET requests. The checker incorrectly compared the
effective `dry_penalty_last_n=131072` record with the unnormalized CLI sentinel
`-1`. It made no template, tokenization, or chat request; wrote no receipt;
never started the feature unit; and restored accepted V1 exactly.

That result is sealed in
[`V23_RESPONSE_FREE_ATTEMPT1.md`](V23_RESPONSE_FREE_ATTEMPT1.md), SHA-256
`4157862fb35c56d7e76c7aa58e811a9d3da2710e72bd0ff58e6ebb718b073983`.
The empty owner-only v1 response root and its 18-file v1 tool tree remain
preserved and ineligible for reuse. The correction retains `-1` in the CLI
contract, checks 131,072 in effective properties, and uses fresh v2 aliases,
units, schemas, response root, and 19-file tool tree.

## Focused test closure

The committed deterministic greedy regression proves multiplier 2.0 changes a
repeated argmax at both `temp == 0` and `temp < 0`, while absent DRY and a zero
multiplier preserve the original token and logits. The reasoning-prefill
regression retains its parser, native-template, generation-prompt,
sanitization, incompatibility, and raw-token assertions.

Fresh normal and ASan/UBSan tests passed locally; fresh normal tests passed on
chuckdancer. Every JUnit file records one run and zero failures, errors,
disabled tests, or skipped tests:

```
876154f3afcbde7bc684e7d1794e4c6234c266693f2338f6a221ee5e2c3a116e  v23-local-asan-ubsan-greedy-dry.xml
d87d0e6b0c7e2ffc5e44418a0398f3754bf4e8abbd94d6dd1ca8c9d23bfb54b6  v23-local-asan-ubsan-reasoning-prefill.xml
c9968f84d021016b0cbc86fc96e8c1f0f4f48ea0987d5445af0d2b42237ab3fb  v23-local-normal-greedy-dry.xml
6613541397f5c162db2e2c112a2f0303db898acd530635a23ece60d5d5c7a2bf  v23-local-normal-reasoning-prefill.xml
eb6912c26083db1963f75ea965c8140ada6808b1c0de0c9ab5f9427d2915e5ac  v23-remote-normal-greedy-dry.xml
745032ef1d353a387fe719ffc6b729cca7f6b599cb7f4e94e9baa81d788084fd  v23-remote-normal-reasoning-prefill.xml
```

The owner-only receipt directory contains exactly those six mode-0600 files.
The V23 suite passed 15/15 locally and remotely against that directory. Both
launchers passed Bash syntax locally and remotely and ShellCheck locally;
ShellCheck is not installed on chuckdancer. Python bytecode generation was
disabled and no `__pycache__` directory remained.

The suite additionally fixes the exact prompt, seed, model, engine, source,
payload, role, template-kwargs, sampler, argv, prefill, normalized-effective
properties, receipt schema, unit, alias, root, request-audit, partition, and
failed-attempt identities. It rejects every missing, duplicated, reordered, or
altered DRY option, explicit breaker mutation, prefill mutation, and
unnormalized effective-property record.

## Response-free control and feature proof

Before stopping accepted V1, the launcher verified every committed input,
model inventory, source path, executable, mapped library, focused test, and
fresh receipt. The exact 19-file v2 tool tree was byte-identical to commit
`454577dfc319ad33c607ac2151de09265ea2bd7d`. Both transient startups had
`NRestarts=0` and no startup diagnostic.

Control PID 3468302 ran as
`kimi-k3-q5attn-abl-v23-v2-control-preflight.service`. Its argv contained
neither DRY nor reasoning prefill. Effective properties were multiplier 0.0,
base 1.75, allowed length 2, last-n 131,072, and the default breakers
`["\n", ":", "\"", "*"]`. Its PID-scoped journal and receipt independently
record exactly four requests:

```
GET /health 200
GET /v1/models 200
GET /props 200
POST /apply-template 200
```

The normalized request-sequence SHA-256 is
`12e63bd9d351908c36b7eb7ddba34014de0883f9c7966fad7d4f263e92ec55cf`.
The native rendered prompt is exactly 1,152 bytes with SHA-256
`70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22`.

Feature PID 3469297 ran as
`kimi-k3-q5attn-abl-v23-v2-feature-preflight.service`. Effective properties
were multiplier 2.0, base 1.75, allowed length 4, last-n 131,072, and the same
four default breakers. The feature template was exactly the native prompt plus
the exact 2,197 seed bytes once: 3,349 bytes with SHA-256
`772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0`.

Tokenizing the combined prompt and tokenizing native prompt plus raw seed
separately produced identical 630-token vectors with SHA-256
`b6b6ad4c56316b672083db45a4612ea6737fad327217033d0b93707b8d37f47f`.
Disabled thinking, assistant response prefill, disabled generation prompt, and
client reasoning-prefill override each produced its exact expected HTTP 500.
Its journal and receipt independently record exactly ten requests: three GETs,
one successful template application, four expected failed template
applications, and two successful tokenizations. The normalized sequence
SHA-256 is
`08da242f8a13fbcd43fe69a8bfc1fc8d8451cfaf8381c9635528125f9cc14c36`.

Neither journal contains `/v1/chat/completions`; no model token was generated.
The write-once owner-only receipts are:

```
808e6c02755922f37e902e2d97bc619828d87f89b1a4f8c95c0713ee284aa006  control.json
402b3830c6412224ba4d5affd995e2c765fe119625cdd855e6f01cf15122a834  preflight.json
```

The phase gate requires the feature receipt hash. The finalized calibration
launcher requires both hashes and has no all-zero receipt lock. The executed
v2 tree remains preserved; finalized behavior tools use a fresh v3 directory.

## Finalized behavior-tool hashes

```
0699d2f7f5f6dd04c61b0eb1dbdca8063575a36440ae8d72aaeb65ed647425bd  V23_PROTOCOL.md
15cb6a5dc2ce42bc5d068b52616029c077fc6e78bac704b8f5e58cfef5dae9d7  V22_RESULTS.md
4157862fb35c56d7e76c7aa58e811a9d3da2710e72bd0ff58e6ebb718b073983  V23_RESPONSE_FREE_ATTEMPT1.md
e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c  v23-reasoning-prefill.txt
04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693  v23-engine-sources.sha256
8bb008b979a746837ce516f194288cdb3d6079f3850737545262d0b9fa35d445  evaluate_reasoning_prefill_api_v23.py
1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a  evaluate_api.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54  prepare_manual_review.py
5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59  gate_v10_calibration.py
d2018227d64478bff279000cc99e1480d24dcc2d9645afc8c882f5248049386e  gate_v23_calibration.py
291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae  verify_v10_calibration_state.py
e3143df627c7135832211ab2586dc00b07678f772da4cc41209206ffd9ac9598  verify_v23_calibration_state.py
57ce6c1caa9f176c7183dfe0fdbb01a56bae265602073513748000f6a91fc54c  preflight_v23_reasoning_prefill.py
dd6f156eaf55bca0777b0c4cf7a855635ed8d9857d44d2010589b76b8ee8fbd9  run_v23_response_free_preflight.sh
25fb2a6e15ff00b3162c46776a91a77fedd91ac70e8577e88bebb035aba0f914  run_v23_calibration_server.sh
f30b5f00130e5a78a695a3e3e993bb475f14f3ddc7a070f60585bf6211dc83e0  test_v23_calibration.py
5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220  v10-calibration-request-prefix.json
44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9  v10-system-prompt-02-semantic-contract.txt
```

## Rollback and unopened behavior

The launcher stopped and collected both transient units, proved port 8081
closed, and restored accepted V1 as `glm-server.service`, PID 3469583,
`NRestarts=0`, active, idle, and healthy. The live executable resolves to
`/home/chuck/ik_llama.cpp-abliteration/build-abliteration/bin/llama-server`
with SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
The selected directory is `/models/Kimi-K3-Q5attn-Abliterated`, alias
`kimi-k3`; only port 8080 is listening.

The behavior root `/models/.abliteration/k3/v23-calibration-run-v1` is absent.
All calibration, confirmation, harmlessness, capability, serving, OpenCode,
formal-throughput, canary, deployment, publication, and push gates remain
unopened. This closure authorizes only the fixed V23 fail-fast calibration.
