# Kimi K3 v24 no-colon-DRY response-free closure

Status: **complete without a V24 chat completion on 2026-08-26**. This record
closes stage 2 of [`V24_PROTOCOL.md`](V24_PROTOCOL.md). Accepted V1 was the only
production candidate before and after the isolated checks. The V24 behavior
root remained absent throughout.

## Frozen change and engine identity

V24 keeps V23's numeric DRY tuple and changes only the sequence-breaker set.
The feature argv contains this exact contiguous JSON-equivalent sequence once:

```json
["--dry-multiplier", "2.0", "--dry-base", "1.75",
 "--dry-allowed-length", "4", "--dry-penalty-last-n", "-1",
 "--dry-sequence-breaker", "\n\"*"]
```

The final value is one three-byte argv element (`0a 22 2a`). The feature's
effective breakers are exactly newline, double quote, and asterisk; colon is
absent. Control retains the four engine defaults including colon.

The clean engine checkout is
`30822f72f79cbe4f0fad9a5a6406850891dc2dc1`. Relative to the V23 runtime
commit, it changes only `tests/test-greedy-dry.cpp`; all runtime and server
sources are byte-identical. Its 13-path source manifest has SHA-256
`5c974d266768b10d3435fc212828b6349c6d5440af4f0888adf6a8eea73c3d34`.
The exact isolated executable is
`ce8044c0956fdb193c881eb8ad5d370625d2db85e1a623f18b751d229ffb6932`,
and both transient PIDs reproduced mapped-executable closure SHA-256
`478685839019bce9afcadbe097cbbbe99adeb448ecdb3ec5fb258ca3dd4187fa`.

## Preserved file-only v1 failure

The first v1 verification never started a candidate and never created a
response root. Its exact audit is
[`V24_RESPONSE_FREE_ATTEMPT1.md`](V24_RESPONSE_FREE_ATTEMPT1.md), SHA-256
`0e24403d1d552ca31b6e8f3519a2fd7805975f16fdced2e80372c1824c0b66fa`.
The correction forces C-locale filename ordering and uses fresh v2 tools,
aliases, units, receipt schemas, and response root. The untouched v1 tree
remains preserved.

## Focused test closure

The deterministic greedy regression passes at zero and negative temperature,
preserves output when DRY is absent or disabled, and now proves the exact colon
breaker distinction. The reasoning-prefill regression retains its parser,
native-template, generation-prompt, sanitization, incompatibility, and
raw-token assertions.

Fresh normal and ASan/UBSan tests passed locally; fresh normal tests passed on
chuckdancer. Every JUnit file records one run and zero failures, errors,
disabled tests, or skipped tests:

```
0862caa895844f06c76256b7f41398dcba097ec560866aa01f0368bef3641aa5  v24-local-asan-ubsan-greedy-dry.xml
a9b61a0279a6ad4f16208d1c0f950558b6fc50edd7607415615e15c106fff18d  v24-local-asan-ubsan-reasoning-prefill.xml
49e7e680cfca07b262b50047686be88a459c7e66aef1152eb80c67720828e316  v24-local-normal-greedy-dry.xml
de12231d36017850883cdc99ca253e949d90b3cf0b9bf322858c964946eccf9a  v24-local-normal-reasoning-prefill.xml
877c465c1d405a306907d24f6a1b061a96400db2c1836f438f8d447a39acdb27  v24-remote-normal-greedy-dry.xml
b2490cbdfcd263171e8c4db3f968706b218aafffdc9789bf7beb133517def1ce  v24-remote-normal-reasoning-prefill.xml
```

The owner-only receipt directory contains exactly those six mode-0600 files.
The V24 suite passed 15/15 locally and remotely. Both launchers passed Bash
syntax locally and remotely and ShellCheck locally; ShellCheck is not installed
on chuckdancer. Python bytecode generation was disabled and neither tool tree
contains `__pycache__`.

The executed v2 tool tree contains exactly 19 files from kit commit
`cf1cea60832af854f32a87277c2f9caef024ba8a`. Its normalized filename/file-hash
stream has SHA-256
`76fba3c6f2615d50278a73b89f8aec5c727d1a4f6119f7c0b83eaaba449df086`,
and its launcher has SHA-256
`f265b00bf7a6fe0c753a53b267bf3cd4cef7612aeaba42d5621ba176864b0bf1`.

## Response-free control and feature proof

Before stopping accepted V1, the launcher verified every committed input,
model inventory, partition, engine source, executable, mapped library, focused
test, JUnit receipt, unused V24 path, and production invariant.

Control PID 3487479 ran as
`kimi-k3-q5attn-abl-v24-v2-control-preflight.service` with `NRestarts=0` and
no startup diagnostic. Its argv contained neither DRY nor reasoning prefill.
Effective properties were multiplier 0.0, base 1.75, allowed length 2,
last-n 131,072, and default breakers `["\n", ":", "\"", "*"]`. Its receipt
has SHA-256
`f358a94dc34a519478d3f7558f4875f49fd06d0022415f7610fe2ebd4563faa4`.
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

Feature PID 3488465 ran as
`kimi-k3-q5attn-abl-v24-v2-feature-preflight.service` with `NRestarts=0` and
no startup diagnostic. Both captured argv and `/props` reproduce multiplier
2.0, base 1.75, allowed length 4, full-context last-n, and exactly the three
registered no-colon breakers. Its receipt has SHA-256
`6fe188193de4fe59e1806062926725b83b1fe8b4bb27522d121f1559cfaeb6d1`.

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
request. Both receipts are mode 0600 in the exact mode-0700 v2 response root.

## Rollback and behavior boundary

The launcher stopped and collected both isolated processes, proved port 8081
closed, and restored accepted V1 as `glm-server.service`, PID 3488771,
`NRestarts=0`, active, idle, and healthy. The selected directory remains
`/models/Kimi-K3-Q5attn-Abliterated`, serving alias `kimi-k3`, with executable
SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
Only port 8080 is open.

No V24 behavior directory, evaluation, manual decision, phase receipt,
registry change, deployment, publication, benchmark, or GitHub push exists.
Behavior remains closed until the receipt and runtime-closure sentinels are
replaced, the finalized v3 tool tree is committed and independently reproduced,
and the failure phase receives a fresh PID.
