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
Before the first behavior response, the exact tool directory and deterministic
partition must be transferred to private mode-0700 paths, rehashed remotely,
the focused suite rerun with bytecode writes disabled, and all response-free
launcher preflights exercised. Until that evidence is appended in a later
commit, the V10 behavior ladder remains closed and accepted V1 remains
production.
