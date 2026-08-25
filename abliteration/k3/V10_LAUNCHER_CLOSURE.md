# Kimi K3 v10 response-free launcher closure

Status: **closed before any V10 Kimi K3 response on 2026-08-25**. This file
records the second preregistration layer; it does not report a behavior result.

The immutable V10 protocol and core tools were committed first at
`c37161e00467fa29321b5de70043a9ebae67eec4`. The launcher binds that exact
protocol payload:

```
3ce445bd8c6fd2ac0514fa035c5fbd20cfbaf72dcd30fbac2b56451dd9bc8370  V10_PROTOCOL.md
```

All fail-closed hash sentinels were then replaced with the committed core-tool
hashes. The resulting launcher is:

```
de19962194f49ebaaf5f4d66da6a220820d5aefe485e48681cf200fac7b0f488  run_v10_calibration_server.sh
```

It checks the accepted V1 executable and live identity, the clean V9 engine
checkout and executable/library closure, V2's completion marker and full
verification evidence, the live 20-file V2 inode inventory, all three partition
files, the selected prompt, every response-producing helper, the request
prefix, and the protocol before it can stop production. Prompt and phase order
are independently reproduced by `gate_v10_calibration.py`.

Every phase uses a distinct transient systemd unit, fresh PID, empty request
history, loopback port 8081, and an exclusive mode-0700 run directory. The
launcher sets its restoration state before either systemd mutation. Its exit
trap stops the candidate, proves port 8081 closed, starts accepted V1, and
requires the exact production executable, selected model directory, alias,
service state, and health. Normal completion additionally requires a
reproducible, hash-bound phase receipt after the transient unit stops.

The repository copies of the new executable helpers and tests are mode 0755.
## Recorded remote preflight

Before the first behavior response, all 15 files above were copied to the new
private directory `/models/.abliteration/k3/eval-tools-v10-v1`, mode 0700. Every
remote SHA-256 reproduced the repository payload, including launcher
`de199621...`, protocol `3ce445bd...`, and the three prompt hashes. Executable
helpers/tests are mode 0700 and input/evidence files are mode 0600.

The committed partitioner then read the sealed canonical source directly on
chuckdancer and created the new mode-0700 directory
`/models/.abliteration/k3/v10-calibration-partition-v1`. Its 2-, 2-, and 96-row
files and manifest reproduced all four preregistered hashes exactly. The remote
focused suite passed 17/17 with bytecode writes disabled and left no
`__pycache__`; remote shell syntax also passed.

The production-only launcher check and the complete response-free
`prompt01/failures` preflight passed. Negative preflights for prompt01 stability
and prompt02 failures both failed closed because their required receipts do not
exist. No run root was created and no V10 process or chat response existed.

At `2026-08-25T17:33:35Z`, accepted V1 remained `glm-server.service`, PID
3277273, `NRestarts=0`, active/running, with executable SHA-256
`a677e4c2decf66acae9eb91bc76ff1054f1cf261d2614f294e5c4f39f9615ab6`.
Port 8081 was closed. The response engine checkout was clean at
`35db6bb3e4de67c1703ffbb3b98e1690296c8d03`. This closes the response-free
launcher preflight and opens only Prompt 01's two-row failure-probe phase.
