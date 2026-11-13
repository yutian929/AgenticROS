#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Only the container's dedicated Codex volume is used; no host credential import.
docker compose run --rm --no-deps agenticros codex login --device-auth
