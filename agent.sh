#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-}" in
  agent|viewer) mode="$1"; shift ;;
  *) echo 'Usage: bash agent.sh <agent|viewer> [--domain-id 42] [Codex arguments...]' >&2; exit 1 ;;
esac
if [[ "$mode" == viewer ]]; then
  exec python3 scripts/runtime.py viewer "$@"
fi
if [[ ${1:-} == --domain-id ]]; then
  [[ $# -ge 2 ]] || { echo '--domain-id requires a value' >&2; exit 1; }
  domain="$2"; shift 2
  exec python3 scripts/runtime.py agent --domain-id "$domain" -- "$@"
fi
exec python3 scripts/runtime.py agent -- "$@"
