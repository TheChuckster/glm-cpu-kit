# debug — capture and replay

Tools for root-causing failures that are too rare to catch by rerunning.

Both were written for the DeepSeek-V4 degeneration loops (~1 in 8 agent
sessions), where every single-request probe came back clean and the actual
trigger turned out to be the server's KV cache state rather than any prompt.

## capture-proxy.py

A verbatim logging proxy for OpenAI-compatible traffic. Forwards byte-for-byte,
relays streaming responses as they arrive so the client still streams, and tees
every request/response pair to disk.

```sh
./capture-proxy.py                                  # :4100 -> 127.0.0.1:4000
CAPTURE_DIR=/tmp/cap PORT=4100 ./capture-proxy.py
```

Point a harness at it by overriding the provider baseURL — for opencode, copy
`~/.glm-opencode-config` and edit `provider.local.options.baseURL`, then run with
`GLM_OPENCODE_XDG` pointing at the copy. Don't edit the real config.

Writes `<dir>/NNNN-request.json` and `<dir>/NNNN-response.txt`.

## replay-sequence.py

Replays a captured request **sequence in order**, which is the point: it
reproduces the server-side cache evolution that a single replayed request cannot.

```sh
./replay-sequence.py 4 17           # replay captures 0004..0017
PATCH='{"cache_prompt":false}' ./replay-sequence.py 4 17    # A/B a setting
```

Scores each response for repetition (max repeated 3-gram) and flags degenerate
ones. This is what turned a 1-in-8 heisenbug into a deterministic 3/3 → 0/2 A/B.

**The lesson worth keeping:** when a failure resists reproduction, capture the
real traffic and replay the *sequence*, not the request. State that lives in the
server between requests is invisible to any probe that sends one.
