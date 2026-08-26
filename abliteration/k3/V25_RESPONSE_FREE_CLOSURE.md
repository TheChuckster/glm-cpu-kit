# Kimi K3 v25 no-newline-DRY response-free closure

Status: **complete without a V25 chat completion on 2026-08-26**. This record
closes stage 2 of [`V25_PROTOCOL.md`](V25_PROTOCOL.md). Accepted V1 was the only
production candidate before and after the isolated checks. The V25 behavior
root remained absent throughout.

## Frozen change and engine identity

V25 keeps V23's numeric DRY tuple and changes only the sequence-breaker set.
The feature argv contains this exact contiguous JSON-equivalent sequence once:

```json
["--dry-multiplier", "2.0", "--dry-base", "1.75",
 "--dry-allowed-length", "4", "--dry-penalty-last-n", "-1",
 "--dry-sequence-breaker", ":\"*"]
```

The final value is one three-byte argv element (`3a 22 2a`). The feature's
effective breakers are exactly colon, double quote, and asterisk; newline is
absent. Control retains the four engine defaults, including newline.

The clean engine checkout is
`ecf7446e02e5a473c8f8316d201836532b707b21`. Relative to V24, it changes only
`tests/test-greedy-dry.cpp`; all runtime and server sources are byte-identical.
Its 13-path source manifest has SHA-256
`69997801099183606e4f24e1597bdf02f550acb4c655691cd89e078f87cacac5`.
The exact isolated executable is
`0da60971041065fd716c8a60f5db04e87f28dba4dc3b30b28db3367e29449b28`,
and both transient PIDs reproduced mapped-executable closure SHA-256
`abea34fb56a93c5936b9e1bb9246c9d3ab78621858b87fb28c4c773db64d3457`.

The other build artifacts are:

```
3eff5f49a244e829da599ee4bd2892de8bd32dda64fb6b9281b554d52d307c00  libmtmd.so
df570684d977932616a6e5bb576ddd9ec0462c99e1025816632d832f059bbec7  libllama.so
a70146840462a3714e32fd3de8df78b2c8925583190e00e726bb04a6c7881466  libggml.so
9b24b25ca9b9fae18934c889da401847b5a826b885ce7d82f2b3ee615054aa85  test-reasoning-prefill
49a633350ff6da0de27d7749be8298416b198b0c86198bf4a6304053bf10fe72  test-greedy-dry
```

Configuration emitted only the preregistered optional-`ccache` notice. The
build emitted only the two preregistered warnings: the unchanged unused local
in `llama-load-tensors.cpp` and unchanged unused parameter in
`build_kimi_k3.cpp`. No new build diagnostic appeared.

## Focused test closure

The deterministic greedy regression passes at zero and negative temperature,
preserves output when DRY is absent or disabled, retains the colon distinction,
and now proves the exact newline breaker distinction. The reasoning-prefill
regression retains its parser, native-template, generation-prompt,
sanitization, incompatibility, and raw-token assertions.

During uncommitted fixture development, tokenizing a standalone LF in the
generic SPM test vocabulary produced the normal leading SPM whitespace token
plus LF, rather than the assumed single token. That test failed before the
eligible commit existed. The committed fixture selects the final token and
also proves that it decodes exactly to LF. The corrected direct and CTest runs
then passed in both normal and ASan/UBSan builds. LeakSanitizer itself cannot
run under the local execution environment's ptrace wrapper, so the sanitizer
receipts use `detect_leaks=0`; address and undefined-behavior instrumentation
remain enabled and reported no finding. A stale owner-only GNU Make jobserver
FIFO was moved aside, after proving no process held it, and the sanitizer build
was rerun without that environment warning.

Fresh normal and ASan/UBSan tests passed locally; fresh normal tests passed on
chuckdancer. Every JUnit file records one run and zero failures, errors,
disabled tests, or skipped tests:

```
132201dd79a9e06959d52dfd0cb69aa99ddcb8ef6747fae7b67ce638e2e1a32f  v25-local-asan-ubsan-greedy-dry.xml
0343dc4bcb6e5e435a685cf94276ee87d390118d8db3ee34f7a1f6626ed3054c  v25-local-asan-ubsan-reasoning-prefill.xml
37472f96058498ee42fd29d2fba7ce3ad8b61c26af200d53cf1cdb81a4c45cd1  v25-local-normal-greedy-dry.xml
388d86c55dd6f7f09b7dbcf6d0a0b49ce6791f9a438b2d4d822e4ee170fd4c26  v25-local-normal-reasoning-prefill.xml
b68f3829765b24bd5cfb26531a7b15e1b22f7af2f74cd952229e2594fe497f4c  v25-remote-normal-greedy-dry.xml
12c452c51aed11c5379195bfa61c7786e54bbce94f9e4c689aa4eda8de12d15f  v25-remote-normal-reasoning-prefill.xml
```

The owner-only receipt directory contains exactly those six mode-0600 files.
The V25 suite passed 15/15 locally and remotely. Both launchers passed Bash
syntax locally and remotely and ShellCheck locally; ShellCheck is not installed
on chuckdancer. Python bytecode generation was disabled and the tool tree has
no `__pycache__` or `.pyc` artifact.

The executed v1 tool tree contains exactly 18 files from kit commit
`d7e0300`. Its normalized filename/file-hash stream has SHA-256
`7c0f6a67201879cedee44bece208ffbf1445e5d04816709bea2da39a3fd6bc0f`,
and its launcher has SHA-256
`abccdee120fb3a6f1b7032ff4ff2923aaa5c80383183e54af48f4475e8cf2dbc`.

## Response-free control and feature proof

Before stopping accepted V1, the launcher verified every committed input,
model inventory, partition, engine source, executable, mapped library, focused
test, JUnit receipt, unused V25 path, and production invariant.

Control PID 3504629 ran as
`kimi-k3-q5attn-abl-v25-v1-control-preflight.service` with `NRestarts=0` and
no startup diagnostic. Its argv contained neither DRY nor reasoning prefill.
Effective properties were multiplier 0.0, base 1.75, allowed length 2,
last-n 131,072, and default breakers `["\n", ":", "\"", "*"]`. Its receipt
has SHA-256
`99b9df575b04c0b0be84d804e4aee2f141e2952f7f3c4700b9bc677ff9531462`.
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

Feature PID 3505576 ran as
`kimi-k3-q5attn-abl-v25-v1-feature-preflight.service` with `NRestarts=0` and
no startup diagnostic. Captured argv contains breaker UTF-8 bytes
`[58, 34, 42]`; both argv and `/props` reproduce multiplier 2.0, base 1.75,
allowed length 4, full-context last-n, and exactly the three registered
no-newline breakers. Its receipt has SHA-256
`2bd8440cc64b1c5f6e22f5fd169892e72a2982b564a5031be9c429132f204ff8`.

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

The response-free receipt and runtime-closure sentinels are now replaced. The
finalized 18-file v2 behavior tree has normalized SHA-256
`ffafcd9b9c75a0b75b19aa9b9a0b9199fd551499cc0d8121d37ed610bec7b18a`.
Its critical final hashes are:

```
1ad5c374e3998923b5c5195f24bd9bc224c70bf1f09ade34fc6bb09addedc557  gate_v25_calibration.py
3784a4055a6af4309ddd659e99efa61d734077785555c505e87a3c6b8e409e17  run_v25_calibration_server.sh
80399e4a0fe2413e795524e7034a5ad0694551825193fb34196bee5c92228d6c  test_v25_calibration.py
2c76803ff318cd472b72896c43a5efdb4f9696f91d6a3c3b0432842e79350631  preflight_v25_reasoning_prefill.py
abccdee120fb3a6f1b7032ff4ff2923aaa5c80383183e54af48f4475e8cf2dbc  run_v25_response_free_preflight.sh
```

The launcher stopped and collected both isolated processes, proved port 8081
closed, and restored accepted V1 as `glm-server.service`, PID 3505888,
`NRestarts=0`, active, idle, and healthy. The selected directory remains
`/models/Kimi-K3-Q5attn-Abliterated`, serving alias `kimi-k3`, with executable
SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
Only port 8080 is open.

No V25 behavior directory, evaluation, manual decision, phase receipt,
registry change, deployment, publication, benchmark, or GitHub push exists.
Behavior remains closed until this finalized v2 tree is committed and
independently reproduced, after which only the failure phase may open under a
fresh PID.
