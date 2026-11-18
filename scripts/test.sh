#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests -p test_docker_layout.py -v
python3 -m unittest discover -s tests -p test_lan.py -v
python3 -m unittest discover -s tests -p test_viewer.py -v
python3 -m unittest discover -s tests -p test_instance.py -v
python3 -m unittest discover -s tests -p test_connection.py -v
python3 -m unittest discover -s tests -p test_workspace.py -v
python3 -m unittest discover -s tests -p test_delete_instance.py -v
python3 -m unittest discover -s tests -p test_capabilities.py -v
# Override the body entrypoint: no driver starts and the live simulation is untouched.
docker compose run --rm --no-deps -T --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/body cartesian_gripper - < tests/test_private_evaluator.py
docker compose run --rm --no-deps -T --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/body cartesian_gripper - < tests/test_sensors.py
docker compose run --rm --no-deps -T --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/agenticros cartesian_gripper - < tests/test_desktop.py
