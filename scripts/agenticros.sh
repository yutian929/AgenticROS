#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/instance.sh
lan_config="runs/lan/server.json"
if [[ $instance != default ]]; then
  lan_config="runs/instances/$instance/lan/server.json"
fi
if [[ $instance != default || -f "$lan_config" ]]; then
  exec python3 scripts/lan.py agent --instance "$instance" -- "$@"
fi
# The Agent is independent of any particular environment; never start a body here.
docker compose build agenticros
export AGENTICROS_WORKSPACE_GID="$(id -g)"
python3 scripts/prepare_workspace.py
options=(--rm --no-deps)
# Forward only an explicitly supplied model key, never the host's credential files.
if [[ -n "${AGENTICROS_MODEL_API_KEY:-}" ]]; then
  options+=(-e AGENTICROS_MODEL_API_KEY)
fi
# The container-local menu validates and persists the selected cloud connection.
exec docker compose run "${options[@]}" agenticros agenticros start "$@"
