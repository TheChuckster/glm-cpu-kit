# Kimi K3 v22 standard-length-DRY response-free closure

Status: **complete without a V22 chat completion on 2026-08-25**. This record
closes stage 2 of [`V22_PROTOCOL.md`](V22_PROTOCOL.md). Accepted V1 remained the
only production candidate before and after the isolated checks. The V22
behavior root was absent throughout this closure.

## Frozen change and engine identity

V22 changes one value from terminally rejected V21:

```
--dry-multiplier 0.8 --dry-base 1.75 --dry-allowed-length 2 --dry-penalty-last-n -1
```

The multiplier, base, full-context window, reasoning prefill, system prompt,
weights, request contract, and engine remain exact. V22 does not pass a
`--dry-sequence-breaker` option; the engine-source closure fixes its defaults to
newline, colon, double quote, and asterisk. The response-free helper records
those four values and rejects any explicit breaker override.

The engine checkout is clean at
`23695c7a444dcfaaf892bebfefb4a4a8394e3c37`. Its changed-source manifest is
SHA-256
`04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693`
and verifies all 13 paths. V22 reuses the exact already-built executable because
it changes no engine source:

```
13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a  llama-server
f4045bfdee67f221e47c067307acb7bb8e59cda11c16ac0009183cb189acf427  test-greedy-dry
d66adaffc6444c3743e787d2393cc0615e8f26c821f8df9cefae361052adb69f  test-reasoning-prefill
6863d473918fb8d2ef57324f54237582aaa3a1158e2624f0bb25d50b5a8c1c47  libllama.so
c02fd1ed092729b89a8c9b9bcf0b50e8f468833e564552e4e9c5a3069af2680d  libggml.so
e9188e9135081f881b95eac4f0b68dbd2980de3766c16a53354f9d74b51e3c45  libmtmd.so
```

The live mapped executable closure reproduced SHA-256
`90336d5afaae0c3c0fc2d4ae1f9709fe7f7428e2612be08e4a3c615d32c18886`
for both isolated PIDs.

## Focused test closure

The unchanged production helper regression proves enabled DRY changes a
repeated argmax at `temp == 0` and `temp < 0`, multiplier-zero DRY preserves the
original token and logits, and a sampler chain without DRY preserves them. The
reasoning-prefill regression retains its complete parser, template, generation
prompt, sanitization, and raw-token assertions.

Fresh normal and ASan/UBSan tests passed locally; fresh normal tests passed on
chuckdancer. Each JUnit file contains exactly one run, zero failures, zero
errors, zero disabled tests, and zero skipped tests:

```
f7841b88bcf3199f1b8b907a97b5cb03d1448eafabb5dba6a01891b2f5269ac2  v22-local-normal-greedy-dry.xml
c573d1d16cb06bc200c4041bd7771992b5afb70e466bb391018061ef86d461d4  v22-local-normal-reasoning-prefill.xml
9d6c2755a1bde82ae4df273b59a5f66bce5b8151533d5091462671b06246c875  v22-local-asan-ubsan-greedy-dry.xml
0b58c94ffdb1261f35ff37a325cfcb31fce617448fa2b7b7bbe9f07f5d234c84  v22-local-asan-ubsan-reasoning-prefill.xml
932bd8e6ea1d0e8d95ae2d844a79e289399d5387829f3d7fc82e5784e4df844f  v22-remote-normal-greedy-dry.xml
379a4297e1564e6b9172baca4019b51b39da00c2ae1ba5bf17c6fe29866d3c96  v22-remote-normal-reasoning-prefill.xml
```

The owner-only remote receipt directory contains exactly those six mode-0600
files. The V22 Python closure suite passed 14/14 locally and again against the
remote receipt directory. Both launchers passed Bash syntax locally and
remotely and ShellCheck locally. Python bytecode generation was disabled.

The 14-test suite additionally proves exact alias/prompt/seed identity, exact
engine and source hashes, exact payload roles and kwargs, no request-side
prefill, seed-plus-continuation reconstruction, fail-closed missing/empty/
duplicated reasoning, exact DRY tuple/order/count, rejection of every missing,
duplicated, reordered, altered, or breaker-mutated argv, frozen prompt/token
hashes, live-PID journal scoping, unique prompt22 roots and units, partition
identity, and two-request startup prefix identity.

## Response-free control and feature proof

The launcher verified all source, executable, library, test, model-inventory,
prompt, seed, protocol, and prior-result hashes before stopping accepted V1.
Both candidate startups were warning-free and had `NRestarts=0`.

Control PID 3453272 ran as
`kimi-k3-q5attn-abl-v22-control-preflight.service`. Its exact argv contained no
DRY option, no sequence-breaker override, and no reasoning prefill. Its three
requests were successful health, models, and `/apply-template` checks, with no
chat endpoint. It reproduced the frozen native prompt:

```
bytes   1152
SHA-256 70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22
```

Feature PID 3454324 ran as
`kimi-k3-q5attn-abl-v22-feature-preflight.service`. Its exact argv contained
the four registered DRY options exactly once and contiguously, followed later
by one exact reasoning prefill. It contained no sequence-breaker option. The
receipt records allowed length 2 and the unchanged default breakers
`["\n", ":", "\"", "*"]`.

The feature template was exactly the native 1,152 bytes plus the exact 2,197
seed bytes once:

```
bytes   3349
SHA-256 772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0
```

Tokenizing that combined string and tokenizing native prompt plus raw seed
separately both produced the same 630-token vector with SHA-256
`b6b6ad4c56316b672083db45a4612ea6737fad327217033d0b93707b8d37f47f`.
Disabled thinking, a final assistant prefill, disabled generation prompt, and a
client prefill override each produced its exact expected HTTP 500 failure.

The feature request audit contains exactly nine requests: health, models, one
successful template application, four expected failed template applications,
and two successful tokenizations. Its normalized sequence SHA-256 is
`f8cf0436f4bf2156c5129d113c5372f3b992800dfdc5ccf510e4ef8c49aaa7fc`.
There was no `/chat/completions` request and no generated model token.

The write-once owner-only receipts are:

```
5d6c23d94df6788fe73908682f66b9ee724f18c611d8275e1cf5dac273f09837  control.json
0eab2fd4354fbec055a046b16405637b075489c8ee171ef91783a5dd34fbdc67  preflight.json
```

The phase gate and calibration launcher now require the feature hash above;
the launcher's former all-zero locks were removed only after both receipts were
created and inspected.

## Final frozen tool hashes

```
282382e3c76bc53e69b294d79a9ee7c0d4673708f6dc77000615dc1e924979dc  V22_PROTOCOL.md
290b564858c0249914fa6f2ea2e7c827214925b0e5334da6a9add9c95a03c5d4  V21_RESULTS.md
e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c  v22-reasoning-prefill.txt
04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693  v22-engine-sources.sha256
2f5bf8323ce5659b3a48f4b56bac4f62f4324d05946dd4b95ff1ece82e6cb8be  evaluate_reasoning_prefill_api_v22.py
1f6b43a330cfbddd8334170c067e6719607f12d9fdeff6d1718d4f8ab733e61a  evaluate_api.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
6d4deb139803da8fe31fdfde3b5ce5a768667b9172d2664b7f3a31b1a310ff54  prepare_manual_review.py
5dfc4d7c80999076b4d14b7faa5212ba95fac462abddb4fd1ee229025b319b59  gate_v10_calibration.py
86b10cfdc698ad7d84d0a7aae0ae35958811aa2461295c4012fccf4fb980b1f4  gate_v22_calibration.py
291b05d5efb397ddd4124516a64fd905518ea515b81ed56b520168311ee271ae  verify_v10_calibration_state.py
4225168fc741ad8b90f0b664624f9af7bb5c95636eb77c7b34d4986480524ffa  verify_v22_calibration_state.py
a9ad6a3848acda05ec65973b5e8bbfc74ebffe53762223c34c8dfe82db4f91db  preflight_v22_reasoning_prefill.py
d9d523fee54d7de2858044822dabd40a4ff3dd05103bf4d9bd8008f5527af423  run_v22_response_free_preflight.sh
9af652f29442267c0f0e60aed9caffe8c48546e16ad4f3c5530795a7d91875c9  run_v22_calibration_server.sh
26b1a58ba4fc2c6c1e3992c7b5e5faf37052ee164e7b129ceb6c4b4c30d514a2  test_v22_calibration.py
5b4ecfbb511ccebd876367bb3e15adcb169f57bece77aed7e395c9ba18358220  v10-calibration-request-prefix.json
44fc73623eb35a4b19b9cbfdf682a015af832ed8954233c68b8cf5845ab116f9  v10-system-prompt-02-semantic-contract.txt
```

## Rollback and unopened behavior

The launcher stopped both isolated units, proved port 8081 closed after each,
and restored accepted V1 as `glm-server.service`, PID 3454622,
`NRestarts=0`. The live executable resolves to
`/home/chuck/ik_llama.cpp-abliteration/build-abliteration/bin/llama-server`
with SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
The selected directory is `/models/Kimi-K3-Q5attn-Abliterated`, alias
`kimi-k3`, with one idle slot and healthy status.

The V22 behavior root `/models/.abliteration/k3/v22-calibration-run-v1` did not
exist after closure. All calibration, confirmation, harmlessness, capability,
serving, OpenCode, formal-throughput, canary, deployment, publication, and push
gates remained unopened. This response-free closure authorizes only the fixed
V22 fail-fast calibration in the protocol.
