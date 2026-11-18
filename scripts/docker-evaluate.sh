#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
# Maintainer-only local socket. Do not relay the result to a task-performing agent.
docker compose exec -T cartesian_gripper /opt/body-venv/bin/python /opt/body/evaluator.py
