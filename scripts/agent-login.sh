#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ $# -eq 0 ]]; then set -- --device-auth; fi
exec python3 scripts/lan.py login -- "$@"
