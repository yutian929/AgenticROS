#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Deletion must never silently select the default account.
if [[ ${1:-} != --instance || $# -lt 2 ]]; then
  echo '用法：bash scripts/delete-instance.sh --instance Agent实例名称 [--dry-run]' >&2
  exit 2
fi
source scripts/instance.sh
exec python3 scripts/delete_instance.py "$instance" "$@"
