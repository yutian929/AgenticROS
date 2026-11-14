#!/bin/bash
set -euo pipefail
# Compatibility entry point; startup and authentication are owned by Codex.
exec bash "$(dirname "$0")/agenticros.sh" "$@"
