#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ $# -lt 1 || ${1:-} == --help ]]; then
  echo 'Usage: bash robot.sh <ROBOT_TYPE> [--domain-id 42]'
  echo 'Available robot types:'
  exec python3 scripts/runtime.py robot-types
fi
if [[ $1 == --list ]]; then
  exec python3 scripts/runtime.py robot-types
fi
environment=$1
shift
exec python3 scripts/runtime.py robot --shell --environment "$environment" "$@"
