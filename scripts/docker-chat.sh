#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if ! command -v docker >/dev/null; then
  echo 'Docker not found. Install and start Docker Desktop first.' >&2
  exit 1
fi
docker info >/dev/null
# Preserve a running user's physical session; applying new body code/seed is explicit.
if [ -n "$(docker compose ps --status running -q body)" ]; then
  docker compose up -d --no-recreate --wait body
else
  docker compose up -d --build --wait body
fi
docker compose build agent
printf '\nCamera: http://localhost:%s\n' "${ROSBODY_VIEWER_PORT:-8080}"
# Container credentials persist separately from the host Codex login.
if ! docker compose run --rm --no-deps agent codex login status; then
  echo 'Log in once: docker compose run --rm agent codex login --device-auth' >&2
  exit 1
fi
docker compose run --rm --no-deps agent codex "$@"
