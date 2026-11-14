#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# The Agent is independent of any particular environment; never start a body here.
docker compose build agenticros
options=(--rm --no-deps)
# Forward only an explicitly supplied model key, never the host's credential files.
if [[ -n "${AGENTICROS_MODEL_API_KEY:-}" ]]; then
  options+=(-e AGENTICROS_MODEL_API_KEY)
fi
# The container-local menu validates and persists the selected cloud connection.
exec docker compose run "${options[@]}" agenticros agenticros start "$@"
