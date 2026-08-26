# Kimi K3 v24 response-free attempt 1 audit

Status: **closed before any V24 server process or model response on
2026-08-26**. Accepted V1 remained active throughout. This v1 tool tree and
its unused identifiers are preserved and may not be edited or reused.

The exact stage-2a tool tree came from kit commit
`c9809dad9d2a04e5c441dbef56911ffec46d57e5` and remains owner-only at:

```
/models/.abliteration/k3/eval-tools-v24-v1
```

It contains exactly 18 files. Its response-free launcher has SHA-256
`dd54ed4e920e116dcb68834cc1c37b0ac8c9ebd1437c15f2841c9d2dcba6d3bf`.
The associated response root
`/models/.abliteration/k3/v24-response-free-preflight-v1` was never created.

The first external verification command mistakenly passed the absolute test
path to `python3 -m unittest`; Python treated `/models/` as a module name and
exited before invoking the launcher. The corrected direct invocation then
passed all 15 V24 configuration tests remotely. It created no bytecode.

The subsequent launcher invocation used `--verify-files-only`, which cannot
stop production or start a candidate. It failed at the first statement in
`verify_files`: the exact tool-membership comparison. The observed 18 files
were correct, but the launcher assumed C/ASCII sort order while the remote
locale interleaved uppercase `V23_RESULTS.md` and `V24_PROTOCOL.md` among the
lowercase names. No engine, model, inventory, template, tokenization, or HTTP
check ran after that first failed comparison.

Post-failure checks sealed the absence of side effects:

- `glm-server.service` remained active as PID 3480587 with `NRestarts=0`;
- accepted V1 remained the only listener on port 8080 and port 8081 was closed;
- neither the v1 nor fresh v2 response-free root existed;
- the V24 behavior root did not exist;
- no control, feature, or behavior unit was started;
- no control/preflight receipt and no V24 chat completion existed; and
- the v1 tool tree contained no `__pycache__` directory.

The correction is purely harness-level: force `LC_ALL=C` for tool and receipt
sorting, bind this audit, and use fresh v2 tool, response-root, unit, alias, and
receipt schema identifiers. The registered breaker hypothesis, engine, model,
seed, prompt, sampler bytes, request contract, phase order, and acceptance
criteria remain unchanged.
