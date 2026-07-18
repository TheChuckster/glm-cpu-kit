#!/usr/bin/env bash
# Start litellm proxy on :4000 - exposes Anthropic-compat /v1/messages
# (and OpenAI-compat /v1/chat/completions) routed to the local llama-swap
# stack. Use this for Claude Code (claurst fish function).
set -euo pipefail
cd "$(dirname "$0")"
exec ./.venv-proxy/bin/litellm \
  --config litellm-config.yaml \
  --host 0.0.0.0 \
  --port 4000 \
  --num_workers 1
