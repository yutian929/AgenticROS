#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
  echo 'Usage: bash scripts/agent.sh --agent_id ID [Codex arguments]'
  echo 'Example: bash scripts/agent.sh --agent_id account-a resume --last'
  echo 'Starts or resumes the Agent instance; use scripts/robot.sh for a Robot instance.'
  exit 0
fi
source scripts/agent-options.sh
lan_config="runs/${AGENTICROS_WORKSPACE_SUBDIR%/workspace}/lan/server.json"
if [[ $AGENTICROS_WORKSPACE_SUBDIR == workspace ]]; then
  lan_config="runs/lan/server.json"
fi
if [[ $instance != default || -f "$lan_config" ]]; then
  exec python3 scripts/lan.py agent --agent_id "$instance" -- "$@"
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
