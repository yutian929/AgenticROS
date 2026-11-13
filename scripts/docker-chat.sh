#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Never start or restart an environment from the AgenticROS launcher.
current=$(docker compose ps --status running -q simple_mujoco)
legacy=$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-rosbody}" --filter label=com.docker.compose.service=body)
if [ -z "$current$legacy" ]; then
  echo 'Start the environment first: bash scripts/environment.sh simple_mujoco' >&2
  exit 1
fi
docker compose build agenticros
if ! docker compose run --rm --no-deps agenticros codex login status; then
  echo 'Log in once: bash scripts/docker-login.sh' >&2
  exit 1
fi
docker compose run --rm --no-deps agenticros codex "$@"
