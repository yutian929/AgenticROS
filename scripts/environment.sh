#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
usage() {
  echo 'Usage: bash scripts/environment.sh <environment>' >&2
  echo 'Available environments:' >&2
  for startup in docker/clients/*/start.sh; do
    [[ -f "$startup" ]] || continue
    name=${startup%/start.sh}
    printf '  %s\n' "${name##*/}" >&2
  done
}
if [[ $# -lt 1 || ! "${1:-}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  usage
  exit 2
fi
startup="docker/clients/$1/start.sh"
if [[ ! -f "$startup" ]]; then
  printf 'Unknown environment: %s\n' "$1" >&2
  usage
  exit 2
fi
if [[ $# -gt 1 ]]; then
  if [[ $1 != simple_mujoco ]]; then
    echo 'LAN v1 currently supports simple_mujoco only' >&2
    exit 2
  fi
  shift
  exec python3 scripts/lan.py client "$@"
fi
exec bash "$startup"
