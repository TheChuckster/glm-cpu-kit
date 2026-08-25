# Kimi K3 v11 response-free launcher closure

Status: **closed before any V11 Kimi K3 response on 2026-08-25**. This file
records the second preregistration layer; it does not report a behavior result.

The immutable V11 protocol, exact prompt, wrappers, and focused tests were
committed first at
`93c32201f103874680776eff51e0c3fb8147cf65`. The launcher binds that exact
protocol payload:

```
04d2f2a9985600c3158943ac7d7ca173df2820b5832d2ce8ae87c3cf5eace8ea  V11_PROTOCOL.md
```

The resulting launcher is:

```
22b3ed1b877c765b1430e7f4a9055f69734536e4576ac71148d42a4e21072296  run_v11_calibration_server.sh
```

It checks accepted V1's executable and live identity, the clean V9 engine
checkout and executable/library closure, V2's completion marker and full
verification evidence, the live 20-file V2 inode inventory, all three sealed
partition files, the V11 prompt, both V11 wrappers and their frozen V10 cores,
every response-producing helper, the request prefix, V10's terminal result,
and the V11 protocol before it can stop production.

Every phase uses a distinct transient systemd unit, fresh PID, empty request
history, loopback port 8081, and an exclusive mode-0700 run directory. The
launcher sets its restoration state before either systemd mutation. Its exit
trap stops the candidate, proves port 8081 closed, starts accepted V1, and
requires the exact production executable, selected model directory, alias,
service state, and health. Normal completion additionally requires a
reproducible, hash-bound V11 phase receipt after the transient unit stops.

The sole intentional legacy string is transient unit tag `v10-prompt11`, which
the immutable V10 provenance core derives from prompt key `prompt11`. The V11
protocol declares this compatibility name; the served alias, run root, prompt,
wrapper schemas, and receipts are all V11. The server argv otherwise reproduces
the previously tested closure exactly.

The repository launcher is mode 0755. Bash syntax, ShellCheck, the focused
34-test V9/V10/V11 suite, Python compilation, protocol hash-table reproduction,
and whitespace checks pass locally.

## Recorded remote preflight

Before the first V11 behavior response, 18 files were copied to the new private
directory `/models/.abliteration/k3/eval-tools-v11-v1`, mode 0700. Every remote
SHA-256 reproduced its repository payload. This includes launcher `22b3ed1b...`,
protocol `04d2f2a9...`, prompt `38f39a47...`, both V11 wrappers, both immutable
V10 cores, all response-producing helpers, V10's result seal, and two frozen
V10 prompt fixtures used only by regression tests. Executable helpers/tests are
mode 0700 and input/evidence files are mode 0600.

The first response-free remote test invocation usefully failed three tests
because those two frozen V10 prompt fixtures had not been packaged. No server
was loaded. After adding the exact Prompt 01 and Prompt 02 fixtures with hashes
`c6eb732f...` and `44fc7362...`, respectively, the unmodified suite passed
21/21 with bytecode writes disabled and left no `__pycache__`. Remote Bash
syntax also passed.

The production-only check and complete response-free `prompt11/failures`
preflight passed. Negative preflights for stability and remainder both exited
2 because the required V11 run/receipt root does not exist. No V11 run root was
created and no V11 process, request, or response existed.

At `2026-08-25T19:13:28Z`, accepted V1 remained `glm-server.service`, PID
3302814, `NRestarts=0`, active/running, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
Port 8081 was closed, and the response-engine checkout was clean at
`35db6bb3e4de67c1703ffbb3b98e1690296c8d03`. This closes the response-free
preflight and opens only V11's two-row failure-probe phase.
