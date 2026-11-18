#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
usage() {
  echo 'Usage: bash scripts/environment.sh <environment>' >&2
  echo 'Robot instance: add --join CODE --robot-id ID; resume with --instance AGENT_NAME --robot-id ID' >&2
  echo 'Without options: legacy single-host environment.' >&2
  echo 'Available environments:' >&2
  for startup in docker/robot_instances/*/start.sh; do
    [[ -f "$startup" ]] || continue
    name=${startup%/start.sh}
    printf '  %s\n' "${name##*/}" >&2
  done
}
if [[ $# -lt 1 || ! "${1:-}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  usage
  exit 2
fi
startup="docker/robot_instances/$1/start.sh"
if [[ ! -f "$startup" ]]; then
  printf 'Unknown environment: %s\n' "$1" >&2
  usage
  exit 2
fi
if [[ $# -gt 1 ]]; then
  environment=$1
  shift
  exec python3 scripts/lan.py client --environment "$environment" "$@"
fi
exec bash "$startup"
