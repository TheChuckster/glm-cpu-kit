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
and whitespace checks pass locally. Before the first behavior response, the
exact private V11 tool directory must be transferred and rehashed remotely,
the focused suite rerun without bytecode writes, and production-only, allowed-
phase, and negative phase-order preflights exercised. Until that evidence is
committed, the V11 behavior ladder remains closed and accepted V1 stays in
production.
