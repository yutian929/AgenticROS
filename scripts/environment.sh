#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
case "${1:-}" in
  simple_mujoco)
    # Preserve the running pre-rename simulator, including its physical session.
    legacy=$(docker ps -q --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-rosbody}" --filter label=com.docker.compose.service=body)
    if [ -n "$legacy" ]; then
      echo "Existing simulator retained: $legacy (legacy body service)."
      echo 'To adopt the new service name, first stop control, then explicitly docker stop this container.'
      exit 0
    fi
    docker compose up -d --build --no-recreate --wait simple_mujoco
    printf 'simple_mujoco camera: http://localhost:%s\n' "${ROSBODY_VIEWER_PORT:-8080}"
    ;;
  real)
    echo 'Real environments are external ROS drivers; no simulator container will be started.'
    echo 'Hardware networking, public body documentation and sensor transport must be configured and verified separately. This checkout only validates simple_mujoco.'
    exit 2
    ;;
  *) echo 'Usage: bash scripts/environment.sh simple_mujoco|real' >&2; exit 2 ;;
esac
