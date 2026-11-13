#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p test_docker_layout.py -v
# Override the body entrypoint: no driver starts and the live simulation is untouched.
docker compose run --rm --no-deps -T --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/body body - < tests/test_private_evaluator.py
