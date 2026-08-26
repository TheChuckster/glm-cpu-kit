# Kimi K3 v23 response-free attempt 1: normalized-sentinel rejection

Status: **rejected without a model response on 2026-08-26**. This record is
append-only. The failed root and v1 remote tool directory remain preserved;
neither is eligible for reuse.

Stage-2 commit `1576b74` launched the first V23 native control from the exact
byte-compared 18-file tool directory
`/models/.abliteration/k3/eval-tools-v23-v1`. The committed response-free
launcher had SHA-256
`0617b0895ee2938f7b550885edcc2ad950ac1dff6be8f970cbb69bc979c7dbc8`,
and the preflight helper had SHA-256
`b824ff247b3d45fbe4e9267366278990a8ce8609ec57099b08f3a05deabad950`.

Control unit `kimi-k3-q5attn-abl-v23-control-preflight.service`, PID 3464707,
loaded successfully with zero restarts and the exact frozen executable. The
new effective-settings check then rejected this `/props` record:

```
multiplier=0.0 base=1.75 allowed_length=2 penalty_last_n=131072
sequence_breakers=["\n", ":", "\"", "*"]
```

The control argv correctly omitted DRY, whose registered sentinel default is
`dry_penalty_last_n=-1`. The engine normalizes that sentinel to the effective
context size when it constructs the sampler and exposes the resulting 131,072
through `/props`. The checker incorrectly required the unnormalized `-1` in
the effective record. This was a harness-representation error, not a sampler,
parser, model, or rollback error.

PID-scoped journal review reproduced exactly three successful requests, in
order:

```
GET /health
GET /v1/models
GET /props
```

No `/apply-template`, `/tokenize`, or chat-completion request occurred. The
feature unit never started. No control or feature receipt was written. The
owner-only failed root
`/models/.abliteration/k3/v23-response-free-preflight-v1` exists with zero
entries and will remain unused.

The exit trap stopped the control, proved the spare port closed, and restored
accepted V1 as `glm-server.service`, PID 3465727, `NRestarts=0`, active, idle,
and healthy. The live executable, accepted model directory, and `kimi-k3`
alias all reproduced their frozen identities.

The correction must preserve the CLI contract as `-1` while separately
requiring effective `/props` value 131,072 for both control and feature. It
must use a new v2 tool directory, response-free root, control and feature
units, aliases, and receipt schemas. No V23 behavior may open until that
corrected response-free closure is committed and independently reproduced.
