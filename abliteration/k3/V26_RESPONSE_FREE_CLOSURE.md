# Kimi K3 v26 quote/asterisk-only DRY response-free closure

Status: **complete without a V26 chat completion on 2026-08-26**. This record
closes stage 2 of [`V26_PROTOCOL.md`](V26_PROTOCOL.md). Accepted V1 was the only
production candidate before and after the isolated checks. The V26 behavior
root remained absent throughout.

## Frozen change and engine identity

V26 keeps V23's numeric DRY tuple and changes only the sequence-breaker set.
The feature argv contains this exact contiguous JSON-equivalent sequence once:

```json
["--dry-multiplier", "2.0", "--dry-base", "1.75",
 "--dry-allowed-length", "4", "--dry-penalty-last-n", "-1",
 "--dry-sequence-breaker", "\"*"]
```

The final value is one two-byte argv element (`22 2a`). The feature's effective
breakers are exactly double quote and asterisk; colon and newline are absent.
Control retains all four engine defaults.

The clean engine checkout is
`78bdb3092f1cb1cd9a95018aa0ff275ffa3f982a`. Relative to V25, it changes only
`tests/test-greedy-dry.cpp`; all runtime and server sources are byte-identical.
Its 13-path source manifest has SHA-256
`9e6f05f3e00f4c3a917f24a882e29d61c09be34fee73d1b4b2cc0ad8313a7154`.
The exact isolated executable is
`d65ea913f2bab718729b866b3d02c749ed93f6247d3a18e1807be7b617e95c7c`,
and both transient PIDs reproduced mapped-executable closure SHA-256
`183e4045718af7e905a408b9b5ad085b95c236cb55e987bc9114f24833224188`.

The other build artifacts are:

```
8114a991223494452fcc68f1ee5aa30d7c9b45cc82743cd1fd4603a0ff68f117  libmtmd.so
40e87f0923c0441b0028ca4c6bd69634de06f461895860cb97716a1d2b7fbf92  libllama.so
531e2bd3d589154576f30763f81776e66ad4f605415a5e7598d99bf81bdadd4c  libggml.so
523a2f44d17fd30d5fc923b22ef18b4a9956103c78d1fbced58d12ef43751939  test-reasoning-prefill
dcac9b99da0f6a291f431ae6748544914121dce9eb3f9889ced16cd1e2850a85  test-greedy-dry
```

Configuration emitted only the preregistered optional-`ccache` notice. The
build emitted only the two preregistered warnings: the unchanged unused local
in `llama-load-tensors.cpp` and unchanged unused parameter in
`build_kimi_k3.cpp`. No new build diagnostic appeared.

## Focused test closure

The deterministic greedy regression passes at zero and negative temperature,
preserves output when DRY is absent or disabled, retains the individual colon
and newline distinctions, and now proves that both removed delimiters are
penalized under the exact quote/asterisk-only configuration. The
reasoning-prefill regression retains its parser, native-template,
generation-prompt, sanitization, incompatibility, and raw-token assertions.

Direct and CTest runs passed in both normal and ASan/UBSan builds.
LeakSanitizer itself cannot run under the local execution environment's ptrace
wrapper, so the sanitizer receipts use `detect_leaks=0`; address and
undefined-behavior instrumentation remain enabled and reported no finding.

Fresh normal and ASan/UBSan tests passed locally; fresh normal tests passed on
chuckdancer. Every JUnit file records one run and zero failures, errors,
disabled tests, or skipped tests:

```
11a0f1b48a5aef7307480d55d835420be6cf84723ae7e117eafd3dbfdda9479a  v26-local-asan-ubsan-greedy-dry.xml
3de3c93188ae879dcfdd2d758eac7a0b9dfabf087fd96121bd7e3f7184c4d326  v26-local-asan-ubsan-reasoning-prefill.xml
1391d39ddaa2e7f8780e059588d06966049441913c0b6bb34cdd15e3f815d062  v26-local-normal-greedy-dry.xml
b520084225d861db0efa4bf6e53888bd207cfff506d59329147cc0688c50bb07  v26-local-normal-reasoning-prefill.xml
695c0b2aa850c24f0abb6ff61567d7d27a825b36c7a2c935d9653a15134880ff  v26-remote-normal-greedy-dry.xml
e9879c37e5bb5862dd8c2a6a8f7b3cd35a0ca95e82d5896020a25f966175e9f1  v26-remote-normal-reasoning-prefill.xml
```

The owner-only receipt directory contains exactly those six mode-0600 files.
The V26 suite passed 15/15 locally and remotely. Both launchers passed Bash
syntax locally and remotely and ShellCheck locally; ShellCheck is not installed
on chuckdancer. Python bytecode generation was disabled and the tool tree has
no `__pycache__` or `.pyc` artifact.

The executed v1 tool tree contains exactly 19 files from kit commit
`793be41`. Its normalized filename/file-hash stream has SHA-256
`8f5a67ea2d698d2cc17c674550f3d36cb1031299640cbcbdb0b423290a9b16cd`,
and its launcher has SHA-256
`cf61ab027dff72c238738a2edd9965399a9d7a2f6106d1eb69541b6ba553819a`.

## Response-free control and feature proof

Before stopping accepted V1, the launcher verified every committed input,
model inventory, partition, engine source, executable, mapped library, focused
test, JUnit receipt, unused V26 path, and production invariant.

Control PID 3524584 ran as
`kimi-k3-q5attn-abl-v26-v1-control-preflight.service` with `NRestarts=0` and
no startup diagnostic. Its argv contained neither DRY nor reasoning prefill.
Effective properties were multiplier 0.0, base 1.75, allowed length 2,
last-n 131,072, and default breakers `["\n", ":", "\"", "*"]`. Its receipt
has SHA-256
`6a48abe2f2ffd229f5f9e23469c2110121789a617177e6c880d135fb432ab9ef`.
PID-scoped journal and receipt independently record exactly four requests:

```
GET /health 200
GET /v1/models 200
GET /props 200
POST /apply-template 200
```

The normalized request sequence is
`12e63bd9d351908c36b7eb7ddba34014de0883f9c7966fad7d4f263e92ec55cf`.
The native rendered prompt is exactly 1,152 bytes with SHA-256
`70667c925b21854812675b3c3acd5055218495adbe816e1e6636116081e2fe22`.

Feature PID 3525565 ran as
`kimi-k3-q5attn-abl-v26-v1-feature-preflight.service` with `NRestarts=0` and
no startup diagnostic. Captured argv contains breaker UTF-8 bytes `[34, 42]`;
both argv and `/props` reproduce multiplier 2.0, base 1.75, allowed length 4,
full-context last-n, and exactly the two registered quote/asterisk breakers.
Its receipt has SHA-256
`e240940d204c1bb3d22c31279459d554388d50a7a3b0a80761cba23433f40074`.

The feature template is exactly the native prompt plus the exact 2,197 seed
bytes once: 3,349 bytes with SHA-256
`772faf9144a562d3c6f9df22191dd6ca550390d80d922ad18188873c8621b4f0`.
Tokenizing that prompt and tokenizing native prompt plus raw seed separately
produced identical 630-token vectors with SHA-256
`b6b6ad4c56316b672083db45a4612ea6737fad327217033d0b93707b8d37f47f`.
Disabled thinking, assistant response prefill, disabled generation prompt, and
client reasoning-prefill override each produced the exact expected HTTP 500.
Its receipt and journal record exactly ten requests: three GETs, one successful
template application, four expected failed template applications, and two
successful tokenizations. The normalized sequence is
`08da242f8a13fbcd43fe69a8bfc1fc8d8451cfaf8381c9635528125f9cc14c36`.

Neither process received `/v1/chat/completions` or any other model-generation
request. Both receipts are mode 0600 in the exact mode-0700 v1 response root.

## Finalized behavior boundary and rollback

The response-free receipt sentinel is now replaced with the exact V26 receipt;
the predicted runtime closure was independently reproduced by both PIDs. The
finalized 19-file v2 behavior tree has normalized SHA-256
`37784e3ab676c015888ad65630b62491a35f3fec63b1305870c055498b4d2043`.
Its critical final hashes are:

```
f5fc7165c40cdc8b081e3def5e864a9bde15dd9577c7de5b084483f838709c81  gate_v26_calibration.py
f8422aae38a9d85ab86abb045ad8ba8facf69fb3c581f5a25025e3498a616b57  run_v26_calibration_server.sh
ffe69e7616830b67edb82368458336535c976ca9cf3b715809ba4c78b1f87f40  test_v26_calibration.py
a7f5015d2bd5edf95846b340c90c0b8694b057555fa0699e9a23d42ac0fe2429  preflight_v26_reasoning_prefill.py
cf61ab027dff72c238738a2edd9965399a9d7a2f6106d1eb69541b6ba553819a  run_v26_response_free_preflight.sh
```

The launcher stopped and collected both isolated processes, proved port 8081
closed, and restored accepted V1 as `glm-server.service`, PID 3526046,
`NRestarts=0`, active, idle, and healthy. The selected directory remains
`/models/Kimi-K3-Q5attn-Abliterated`, serving alias `kimi-k3`, with executable
SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
Only port 8080 is open.

No V26 behavior directory, evaluation, manual decision, phase receipt,
registry change, deployment, publication, benchmark, or GitHub push exists.
Behavior remains closed until this finalized v2 tree is committed and
independently reproduced, after which only the failure phase may open under a
fresh PID.
