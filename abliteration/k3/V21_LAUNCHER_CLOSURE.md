# Kimi K3 v21 corrected greedy-DRY launcher closure

Status: **closed before any V21 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V21 failure-probe phase. It reports
protocol, engine correction, regression, response-free template/tokenization,
sampler, isolation, phase-ordering, and restoration evidence; it does not
report a model-behavior result.

The stage-1 protocol was committed at
`27c791c7d4490466713828d96dba6c2a319983b8`. Response-free tooling was
committed at `12bcbb30c177e61af6f696e0cf44e4c1da05e73f`. The evaluator,
gate, state verifier, behavior launcher, six fresh engine-test receipts, and
focused closure suite were committed at
`2845ad4e0cbd9b81762f83bc52bbf9b35c15314a`; explicit remote receipt
binding was corrected and committed at
`bb5de7ccb6077f57ef3790fc2c38761bfa0b3d85`. All four commits preceded
the first V21 chat completion.

## Fixed intervention

V20 exposed the exact canonical DRY tuple in the live argv but produced four
responses byte-identical to V19. Source review then proved that request
temperature zero jumped directly to greedy argmax and never called the sampler
queue containing DRY. V21 changes only the engine implementation: commit
`23695c7a444dcfaaf892bebfefb4a4a8394e3c37`, directly atop V20 engine
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`, applies deterministic DRY
before greedy selection at `temp == 0` and before greedy-with-probabilities at
`temp < 0`.

The production helper regression proves four cases: enabled DRY changes the
repeated argmax at both deterministic temperatures, multiplier-zero DRY leaves
the logits and greedy token exact, and a sampler chain without DRY does the
same. Filters that cannot change argmax remain skipped and no stochastic
sampler is introduced into deterministic mode.

The server tuple is byte-for-byte V20:

```
--dry-multiplier 0.8 --dry-base 1.75 --dry-allowed-length 4 --dry-penalty-last-n -1
```

Weights, reasoning seed, visible prompt, datasets, phase order, request
temperature and seed, reasoning budget, 2,048-token limit, semantic rubric,
provenance logic, and fail-fast gates remain fixed. The seed is byte-identical
to V20 and V19:

```
e4702fce16acfd35ba083705c415023dba5e62931dfaf1bfd2b93822afeb259c  v21-reasoning-prefill.txt
fe05784930d31fde8306132f3862227f41b15118043577d1321fab66369ae1d2  V21_PROTOCOL.md
07bfabf4b5736f84b3a043799c9a14e66c017f11151245889583ae97e3af1afb  V20_RESULTS.md
```

## Engine and regression closure

The clean isolated chuckdancer checkout is
`/home/chuck/ik_llama-v21-23695c7a`, build `build-v21`. The release build
uses GCC 13.3.0, native CPU dispatch, OpenMP, shared engine libraries, server,
and tests. Its exact identities are:

```
04b9aab1c52ba0500731fd39f7c48d0d0b2def7356c495fa46fc202fb2555693  v21-engine-sources.sha256
13dbafbeab3bb9438bdf784b2df4b211bddbd167c9ab4e236f3c23b78180508a  llama-server
f4045bfdee67f221e47c067307acb7bb8e59cda11c16ac0009183cb189acf427  test-greedy-dry
d66adaffc6444c3743e787d2393cc0615e8f26c821f8df9cefae361052adb69f  test-reasoning-prefill
e9188e9135081f881b95eac4f0b68dbd2980de3766c16a53354f9d74b51e3c45  libmtmd.so
6863d473918fb8d2ef57324f54237582aaa3a1158e2624f0bb25d50b5a8c1c47  libllama.so
c02fd1ed092729b89a8c9b9bcf0b50e8f468833e564552e4e9c5a3069af2680d  libggml.so
90336d5afaae0c3c0fc2d4ae1f9709fe7f7428e2612be08e4a3c615d32c18886  mapped executable closure
```

Both focused tests passed in the post-commit local release build, local
ASan+UBSan build with leak detection disabled only for the ptrace environment,
and fresh remote release build. Every JUnit file records one run, zero failures,
zero disabled tests, and zero skipped tests:

```
5bbef0d34f81045112e52e943d03b868b7b320ec5a1896babd2d95935fde0e87  receipts/v21-local-normal-greedy-dry.xml
44bea8d5e56bea8e8f83c55b042dee0fd7443ab83f81b0b9b0b42a6be01ace29  receipts/v21-local-normal-reasoning-prefill.xml
ac883e83b66f6ee39820b2447dcd7ce8b2ec1f4ec40df4372d70b35d1acdc897  receipts/v21-local-asan-ubsan-greedy-dry.xml
5f829638fe467f5529dbff92532c356ea7e014bc10eb84c32a7bea4878a1332f  receipts/v21-local-asan-ubsan-reasoning-prefill.xml
29f75348a76fb8cbe67f4560a085f69919da49f5239deb93e3e2f8607a4315c6  receipts/v21-remote-normal-greedy-dry.xml
9f1825dbc598d60da41a7f829601abaad92620a87dffddb0dc550a35af75b7fe  receipts/v21-remote-normal-reasoning-prefill.xml
```

The owner-only remote receipt directory is
`/models/.abliteration/k3/v21-engine-test-receipts-v1`; it contains exactly
those six files. The local engine's complete compiled suite passed 32/32 after
excluding three independently identified baseline limitations: the existing
BERT vocabulary golden mismatch, the existing ChatGLM template terminal-LF
mismatch, and `test-eval-callback`, which has no `stories260K.gguf` and cannot
download because the build lacks libcurl. All sampling, reasoning, control
vector, projection, and V21 tests passed. The remote compilation emitted only
the engine line's two preexisting warnings in unchanged Kimi tensor/graph
sources (one unused variable and one unused parameter); changed V21 sources,
runtime startup, regressions, and sanitizers emitted no diagnostic.

## Frozen tooling and fail-closed checks

The owner-only remote tool directory is
`/models/.abliteration/k3/eval-tools-v21-v1`. It contains exactly 18 root
files. Local and remote hashes match:

```
a018fe8d5bfc36a135d5cd391cbb310ccfaae88222474f35a78ead64d12cc641  run_v21_response_free_preflight.sh
dfebe5137b24eddacaa7ad5b9ab1cb81f5f2ded44a309b717a052f93cdfb8fc4  run_v21_calibration_server.sh
7dd2512b031b5ed0342bb3e025e31cf5b743dd77452690b1ea79ad5614d7037b  preflight_v21_reasoning_prefill.py
94eba25a3eaece9a534fe793420910a76584e2610559cc36e0dccfbfed6d98d7  evaluate_reasoning_prefill_api_v21.py
21b63b8914c7530be1ed7ff399e8b82b04a8f0af3885190fedd1ce28689fd539  gate_v21_calibration.py
97967d718c11f35d9fc523e1573c6951d91e0ee2ddac7f97034f6b9a0c40d59b  verify_v21_calibration_state.py
6cf3a727a411282b3db03e8b3c50bd1a71d604763dba4a18731a76e0d24e5f22  capture_server_provenance.py
b2ce5000deac35e642555411da1141bf217b35486ea723675845e7806ec3bff6  test_v21_calibration.py
```

The focused suite passed 14/14 locally and from the sealed remote path against
the explicit receipt directory. Bash syntax, local ShellCheck (the remote host
does not install ShellCheck), whitespace, literal-hash, source-manifest,
engine/test binary, library, V2
artifact/inventory, partition, response-free receipt, production identity,
isolation, and fresh-run checks passed. Missing, duplicated, reordered, or
altered DRY argv fails closed. The provenance regression requires the exact
transient unit and `_PID=<live pid>`.

The final `prompt21/failures` no-response preflight passed with accepted V1
active. Stability and remainder preflights each exited 2, created no behavior
root, and produced identical captured output:

```
eb39579bc9f87fe1fb95256146e2713e5f99d8e049ec45e7578ee6be533dab3d  unopened stability/remainder preflight output
```

All three V21 behavior-unit journals contained zero lines. No V21 behavior
directory or behavior Python bytecode existed.

## Response-free chuckdancer proof

The launcher loaded an exact no-option control PID followed by a separate
feature PID with the exact DRY tuple once and the exact reasoning prefill once.
Neither unit made a generation request. Both had `NRestarts=0`, exact argv,
engine commit, executable, working directory, and mapped closure, with no
startup warning/error match and fresh PID-scoped request histories:

```
a3acd6e22b43e37930b2bea14a753429bd5722c387d3b9d123ddb62341fb7829  control.json
ee989366f4dd74f049099d920634250e3d228f35bac864655f6ffaac5869f181  preflight.json
```

Control PID 3437410 reproduced the frozen native prompt and recorded no DRY:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
f168a428b064d8d2a93336ec6bc7292277d46924c2f2755d66e30b175db23892  normalized request audit
```

Feature PID 3438590 recorded the tuple exactly once and in order, then returned
the native prompt plus the exact 2,197 seed bytes once:

```
3349 bytes
772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0  complete extended prompt
3915d4bae0e8fd9d27a1dc2820bdc1e8aba0996393279e3a1d481d10552c696a  terminal fragment plus seed
f8cf0436f4bf2156c5129d113c5372f3b992800dfdc5ccf510e4ef8c49aaa7fc  normalized request audit
```

All four preregistered negative requests returned HTTP 500. Exact raw-token
equivalence passed at 630 tokens; both arrays hash to
`b6b6ad4c56316b672083db45a4612ea6737fad327217033d0b93707b8d37f47f`.
The control audit was exactly health/models/apply-template. The feature audit
was exactly health/models, one successful and four expected-failed
apply-template calls, and two tokenizations. No chat, completion, Responses,
Anthropic, embedding, or other generation request occurred.

Response-free Python bytecode was moved intact to
`/tmp/k3-v21-eval-tools-pycache-after-response-free-20260825`. The tool,
receipt, and response-free evidence trees are bytecode-clean. Accepted V1 is
active and healthy at PID 3438883 with `NRestarts=0`, executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
model `/models/Kimi-K3-Q5attn-Abliterated`, alias `kimi-k3`, and port 8081
closed.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
