#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Only the container's dedicated Codex volume is used; no host credential import.
if [[ $# -eq 0 ]]; then
  set -- --device-auth
fi
options=(--rm --no-deps)
if [[ ! -t 0 ]]; then
  options+=(-T)
fi
exec docker compose run "${options[@]}" agenticros codex login "$@"
