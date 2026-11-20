#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -lt 1 || ${1:-} == --help ]]; then
  echo 'Usage: bash scripts/robot.sh <cartesian_gripper|aloha_tabletop> --server SERVER_IP --robot-id ID'
  exit 0
fi
environment=$1
shift
exec python3 scripts/lan.py robot --environment "$environment" "$@"
