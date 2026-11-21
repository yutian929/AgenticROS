#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Maintainer-only, explicitly selected robot container. Never relay truth to Agent.
python3 - "$@" <<'PY'
import argparse,json,subprocess,sys
sys.path.insert(0,'scripts')
import runtime
parser=argparse.ArgumentParser()
parser.add_argument('robot_id')
parser.add_argument('--domain-id',type=int,default=42)
args=parser.parse_args()
config=json.loads(runtime.client_file(args.robot_id,args.domain_id).read_text())
runtime.run(config,['exec','-T','client','/opt/body-venv/bin/python','/opt/body/evaluator.py'],True)
PY
