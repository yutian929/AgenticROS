#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/ros_env.sh
exec python -m rosbody.cli "$@"
