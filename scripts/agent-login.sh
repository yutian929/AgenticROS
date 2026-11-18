#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/agent-options.sh
if [[ $instance != default || -f runs/lan/server.json || -f runs/agent_instances/default/lan/server.json ]]; then
  if [[ $# -eq 0 ]]; then
    set -- --device-auth
  fi
  exec python3 scripts/lan.py login --agent_id "$instance" -- "$@"
fi
export AGENTICROS_WORKSPACE_GID="$(id -g)"
python3 scripts/prepare_workspace.py
# Only the container's dedicated Codex volume is used; no host credential import.
if [[ $# -eq 0 ]]; then
  set -- --device-auth
fi
options=(--rm --no-deps)
if [[ ! -t 0 ]]; then
  options+=(-T)
fi
exec docker compose run "${options[@]}" agenticros codex login "$@"
