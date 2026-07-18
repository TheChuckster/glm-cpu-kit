#!/bin/bash
# Generate the server API key at ~/.glm-api-key (referenced by serve-glm.sh and
# by the Prometheus llama-glm scrape job). Run once.
set -e
if [ -s "$HOME/.glm-api-key" ]; then
  echo "already exists: $HOME/.glm-api-key"
else
  head -c 24 /dev/urandom | base64 | tr -d '/+=' > "$HOME/.glm-api-key"
  chmod 600 "$HOME/.glm-api-key"
  echo "wrote $HOME/.glm-api-key"
fi
echo "key: $(cat "$HOME/.glm-api-key")"
