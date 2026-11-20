#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Maintainer-only, explicitly selected robot container. Never relay truth to Agent.
python3 - "$@" <<'PY'
import json,subprocess,sys
sys.path.insert(0,'scripts')
import lan
if len(sys.argv)!=2:raise SystemExit('Usage: bash scripts/docker-evaluate.sh ROBOT_ID')
config=json.loads(lan.client_file(sys.argv[1]).read_text())
lan.run(config,['exec','-T','client','/opt/body-venv/bin/python','/opt/body/evaluator.py'],True)
PY
