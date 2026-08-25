# Kimi K3 v12 response-free launcher closure

Status: **closed before any V12 Kimi K3 chat response on 2026-08-25**. This
file records implementation and preflight evidence; it does not report a
behavior result.

The immutable V12 protocol, exact system prompt and assistant prefill, frozen
evaluator, gate extension, state wrapper, and focused tests were committed
first at `ec31408`. The metadata utility used for the response-free embedded-
template comparison was committed separately at `4251753`. The exact protocol
payload is:

```
d74401963d0d1b8fa015542682d15bc28eff3bb02d4a933c3e8fee98387ba6ba  V12_PROTOCOL.md
```

The fail-safe launcher was committed at `788c59d` and hashes to:

```
941715e5b326ef81bd01b37b924c1f831eb0a2e31c0a67acdc75cfe35436c377  run_v12_calibration_server.sh
```

It differs from V11's already exercised launcher only in the declared V12
tool/run roots, alias, prompt, assistant-prefill evaluator and dependency,
wrapper hashes, protocol/result seals, and compatibility unit tag. Every
engine, library, V2 artifact/inventory, partition, production identity,
isolation, fresh-run, restoration, and phase-dependency check is retained.
Every phase still gets a distinct transient unit, fresh PID, empty request
history, loopback port 8081, exclusive mode-0700 run directory, and unconditional
accepted-V1 restoration trap.

## Local response-free checks

Bash syntax, ShellCheck, Python compilation, whitespace checks, and the focused
V9/V10/V11/V12 closure suite passed; the latter ran 34/34 tests. Full test
discovery ran 76 tests: 74 passed and the only two collection errors were the
historical V5 SOM/spectral modules' absent optional `minisom` dependency. Those
modules are not imported by, packaged with, or used by V12; no V12 test failed.

The V12 tests reproduce the exact prompt and prefill identities, three-message
payload, thinking-disabled template kwargs, raw-continuation reconstruction,
empty-continuation failure, evaluator/state dependency hashes, 2/2/96
partition, request prefix, and post-evaluation tamper detection.

## Recorded chuckdancer preflight

Fifteen exact files were copied into the new private directory
`/models/.abliteration/k3/eval-tools-v12-v1`, mode 0700. Executable helpers and
tests are mode 0700; prompt, prefill, protocol, prior result, and request-prefix
inputs are mode 0600. Every remote SHA-256 reproduced the repository payload,
including evaluator `9177346b...`, its frozen base dependency `1f6b43a3...`,
gate wrapper `aada06b9...`, state wrapper `41d69e42...`, protocol `d7440196...`,
prefix `7845b857...`, and launcher `941715e5...`.

The focused remote suite passed 7/7 with bytecode writes disabled, remote Bash
syntax passed, and no `__pycache__` was created. The production-only check and
complete response-free `prompt12/failures` preflight passed. Negative
preflights for stability and remainder both exited 2 because the required V12
run/receipt root did not exist. No run root or V12 transient process was
created.

The exact full-value `tokenizer.chat_template` diagnostic output, excluding
the filename header, hashed identically for accepted V1 and V2:

```
3255b18b38abdd97d9c4b78e64af2b733402317e2e9b007b539b0630dae8e134  gguf_peek.py
82249d36dcedfee341db846e7628d7bb108185afa53835465861d4f355477f89  V1 template diagnostic
82249d36dcedfee341db846e7628d7bb108185afa53835465861d4f355477f89  V2 template diagnostic
```

An authenticated response-free `/apply-template` check against the live copy
of that exact embedded template returned HTTP 500 when final-assistant prefill
was combined with default thinking, with the engine's expected incompatibility
error. The locked request with `enable_thinking=false` and
`thinking_effort=low` returned HTTP 200. Its 840-byte rendered prompt SHA-256
was `6568706b45d835695c458bdab170e8f90b3e6b3806340ab68a8156d190ef087c`
and ended with the exact terminal fragment whose SHA-256 is
`1e31a62bded110bda258a9cb388ab85cb72d91d267aeae6f979696657ee09ea2`:

```
<|open|>message role="assistant"<|sep|><|open|>response<|sep|>Sure, here is
```

`/apply-template` only renders the prompt and performs no inference. Its two
requests affected only accepted production's journal; each scored candidate
phase starts a separate fresh PID and retains its exact evaluator-only request
audit.

At `2026-08-25T20:08:18Z`, accepted V1 remained `glm-server.service`, PID
3313223, `NRestarts=0`, active/running and healthy, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`,
the exact accepted model directory, and alias `kimi-k3`. All three V12 units
were inactive, port 8081 was closed, the V12 run root was absent, and the
candidate engine checkout was clean at
`35db6bb3e4de67c1703ffbb3b98e1690296c8d03`.

This closes the response-free layer and opens only V12's two-row failure-probe
phase. The 2-row stability phase, 96-row remainder, 310 StrongREJECT responses,
harmlessness, capability, serving, OpenCode, throughput, canary, deployment,
publication, and both repository pushes remain closed.
