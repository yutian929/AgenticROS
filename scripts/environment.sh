#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
usage() {
  echo 'Usage: bash scripts/environment.sh <environment>' >&2
  echo 'Available environments:' >&2
  for startup in docker/environments/*/start.sh; do
    [[ -f "$startup" ]] || continue
    name=${startup%/start.sh}
    printf '  %s\n' "${name##*/}" >&2
  done
}
if [[ $# -ne 1 || ! "${1:-}" =~ ^[a-z][a-z0-9_]*$ ]]; then
  usage
  exit 2
fi
startup="docker/environments/$1/start.sh"
if [[ ! -f "$startup" ]]; then
  printf 'Unknown environment: %s\n' "$1" >&2
  usage
  exit 2
fi
exec bash "$startup"
