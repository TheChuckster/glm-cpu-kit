# Kimi K3 v13 response-free launcher closure

Status: **closed before any V13 Kimi K3 chat response on 2026-08-25**.
This record authorizes only the two-row V13 failure-probe phase. It reports
implementation, build, response-free template/tokenization, isolation, and
restoration evidence; it does not report a behavior result.

The stage-1 protocol was committed at `0e961b1` before the implementation or
candidate behavior. Response-free tooling was committed at `9e38767`; a file-
only run then caught a malformed 62-character transcription of one historical
V2 verification hash. No server was stopped or loaded by that failed check.
The exact hash was corrected and a regression test requiring every launcher
hash literal to be 64 lowercase hexadecimal characters was committed at
`e746823`. The response-free run used that corrected launcher. The final gate,
behavioral launcher, and archived test receipts were committed at
`721a978d56b7f7851888fb9e4d72c2a1b33beb13`.

## Engine and upstream closure

The fork branch was rebased cleanly from its prior K3 head `35db6bb3` onto
current `ik/main` `08b500b958a3f1102e6500e5c425e65517d6fb7e`. All 55 preexisting
fork commits were unchanged under `git range-diff`. Current
`firedancer/main` `21819724b3c25d76fe56b64405020b0c98d89923` is an ancestor of the
result. The only upstream tree change beneath the preexisting fork patches is
the three-line HC_POST CPU chunk fix in `ggml/src/ggml.c`.

The V13 implementation is the clean commit
`98de9a7f69ef3d387b676ad4a3ee14946ac88f94`. It adds the process-level
`--reasoning-prefill` option, applies it to both rendered `prompt` and parser /
sampler `generation_prompt`, rejects API override and incompatible request or
template shapes, caps the seed at 16 KiB, and leaves the no-option path a
no-op. The ten-file source manifest is:

```
0800c4cbe4bf5d169c4b57d53cc1b68b4534861c9555c870a4eaf5ec97d97c13  v13-engine-sources.sha256
```

The complete Git bundle was locally verified before transfer:

```
7eac00e14d6574c720c36669e256df79603a221e91527b1345da99fb77d13460  ik-llama-v13-98de9a7f.bundle
```

Chuckdancer was cloned from that bundle into the new exact checkout
`/home/chuck/ik_llama-v13-98de9a7f`, then configured and built from scratch in
`build-v13` using Release, shared libraries, CURL off, GCC 13, native CPU, and
OpenMP. The checkout remained clean at the exact commit.

```
b0a26936cfe28302dbfd9d51ffaddaceba9771e3ac15878a1994f7bab57a44a6  llama-server
cd822a7c1ad834dc95786ac268fd59e954f3bf0d12a3cd41a5d14eced93c5e2f  test-reasoning-prefill
1fdc7c3fe29d6a3fdba22d74e5101e317cdacfc72ad7a70bf088786c48b5f276  libmtmd.so
9dc6d78d4232bc919c1493e00f6ec3c198608a419c5485f1887cc29c0df4fdbf  libllama.so
bfcb4e24f698a78de0f18ce03d1109f80042124959c1cca2b7affd470e9b3abc  libggml.so
d277413a0257024b9d7b9b5efb5b930bf089d4c070d26cd09c46244625e06607  mapped executable closure
```

The mapped closure fingerprint was preregistered from the same path/hash set
whose algorithm reproduces V9's prior live fingerprint, then independently
reproduced by both live V13 preflight PIDs.

## Normal and sanitizer evidence

The focused C++ test proves valid dual-string injection, absent-option no-op,
every specified fail-closed class, and full/partial PEG reconstruction of the
forced seed into `reasoning_content` without contaminating visible content.
Normal and ASan+UBSan builds and focused tests passed locally; the fresh remote
build and focused test passed on chuckdancer. The exact JUnit receipts are:

```
0bc01686f717e78856822b78f673ccd919d7fa7d804c4fbabd7c8b216b5012a1  v13-local-normal-reasoning-prefill.xml
b8ed93659e08200ceebdaa93c1e4718141ca55b1d26b26649fd4cf8b8a07dde3  v13-local-asan-ubsan-reasoning-prefill.xml
85c64768a11ebbec1a1780d3da837ce2a34221c507df0c009510d0bb34b407f4  v13-remote-normal-reasoning-prefill.xml
```

The local parser/Jinja/PEG/JSON focused suite passed 6/6. A broad historical
chat-template target still reaches its preexisting ChatGLM4 newline assertion.
The remaining broad CTest failures are the stale BERT tokenizer fixture and an
eval-callback test requiring absent `stories260K.gguf` plus a CURL-enabled
build; neither imports or exercises the V13 path. LeakSanitizer with
`detect_leaks=1` cannot initialize under this environment's ptrace policy;
ASan+UBSan with `detect_leaks=0`, halt-on-error, and abort-on-error passed.

## Frozen final tooling

The owner-only remote directory is
`/models/.abliteration/k3/eval-tools-v13-v1`. Local and remote hashes match:

```
febf5ff0b0751932fecf29989e9b91bf96f978e856cc3707747a3cd94f993f3a  run_v13_response_free_preflight.sh
c514af2cc13169ced8a1124b2237878db08d2d03aced68b60451ce7b5997c0f1  run_v13_calibration_server.sh
2a132fcd2392b2b8ec77ed763fd3cd0eff86fbcc5f305e4fbb32c3577532f897  preflight_v13_reasoning_prefill.py
108e67a9059ff86fb6e37aa0047b764c09c69ba11987ed9c2994d81470db3b58  evaluate_reasoning_prefill_api.py
c8fe1eab1d03eb922daf221299c772cf84ec0dcf36f07f9a22edfe71923a8277  gate_v13_calibration.py
ceb7f2c8b3f5b990d4686da4d6dbdbb6188596bf5a0d0e1dab1fe435dabaecb1  verify_v13_calibration_state.py
37b3edd108875e829c983b46d870bb68f178b6d6f41d1d11f15ba26fc6ccd2e3  test_v13_calibration.py
```

The focused final suite passed 9/9 locally and from the sealed remote path.
Bash syntax, ShellCheck, whitespace, source-manifest, engine/test binary,
library, V2 artifact/inventory, partition, production identity, isolation, and
fresh-run checks passed. The final `prompt13/failures` no-response preflight
passed while accepted V1 remained active. Negative preflights for `stability`
and `remainder` both exited 2 because the required earlier V13 receipt root did
not exist. No behavioral run directory was created.

The first remote Python test command was invoked by module name from the wrong
working directory and therefore produced `ModuleNotFoundError`; rerunning the
same sealed test by its exact path passed. This was a harness invocation error,
not an engine or candidate test failure. Direct preflight helper execution also
created one generated `__pycache__`; it was moved intact to the exact temporary
quarantine `/tmp/k3-v13-eval-tools-pycache-after-preflight-20260825`. The final
behavior launcher exports `PYTHONDONTWRITEBYTECODE=1`, and the sealed tools
directory was rechecked empty of bytecode.

## Recorded response-free chuckdancer proof

The launcher first loaded an exact no-option control PID, then a separate
feature PID with the sole additional arguments
`--reasoning-prefill 'I know that.'`. Neither unit made a completion request.
Both had `NRestarts=0`, exact executable and working-directory identity, exact
argv, the same `d277413a...` runtime closure, no startup warning/error match,
and fresh audited request histories.

```
feec21c998f1845c75b75bf401384b432deb03edea01b29c75d035167a3c0f5f  control.json
1b93736d8341ef04aacfdfd3a546945c07d19a821021b256d66e964f91c1ac36  preflight.json
```

Control PID 3328733 reproduced the previously frozen prompt exactly:

```
1152 bytes
70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22  complete prompt
28c554ed113067c06e907127add0266f39a34d17ca195baba5786c916ee6e350  terminal think fragment
```

Feature PID 3329788 returned exactly the control prompt plus the twelve seed
bytes, once:

```
1164 bytes
aa5dd4313ea8579d7f032e5a02bb39c4efd10c7757d9d02125c34a26c2f98ec2  complete extended prompt
fec6854a667b667357ec502c5d7d51fdef1a3ab404365447b37e2de229a4f644  terminal think fragment plus seed
```

Thinking-disabled, final-assistant-prefill, `add_generation_prompt=false`,
and client `reasoning_prefill` override requests each returned the preregistered
HTTP 500 failure and exact required error. Tokenizing the complete extended
prompt produced the same ordered 223 tokens as tokenizing the native prompt and
raw seed as separate array elements with `add_special=true`; both token arrays
hash to `e7ab92fd5dcb983344be695dd71fde40371e496a1a5dc66521b24dc995b0a60e`.
This closes the possible string-boundary divergence from the published raw-token
append method.

The control request audit was exactly health/models/apply-template. The feature
audit was exactly health/models, one successful apply-template, four expected
failed apply-template calls, and two tokenizations. There was no chat,
completion, Responses, Anthropic, embedding, or generation request.

The unconditional trap restored accepted V1. At completion it was
`glm-server.service`, PID 3330100, `NRestarts=0`, active/running and healthy,
with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
the accepted model directory, and alias `kimi-k3`. Both transient V13 preflight
units were inactive, port 8081 was closed, and the behavioral run root remained
absent.

This closes the response-free layer and opens only rows 000 and 002. Stability,
the remaining 96 canonical rows, all 310 StrongREJECT rows, harmlessness,
capability, serving, OpenCode/integration, throughput, canary, production
promotion, Hugging Face publication, and both GitHub pushes remain sealed.
