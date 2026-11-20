#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
for test in test_docker_layout test_lan test_viewer test_instance test_connection test_workspace test_agent_rules test_capabilities test_clean_runtime test_ros_client test_migration; do
  python3 -m unittest discover -s tests -p "$test.py" -v
done
# Disposable processes: no driver, no user volumes, no network.
for test in test_private_evaluator test_sensors; do
  docker run --rm -i --network none --entrypoint /opt/body-venv/bin/python \
    -e PYTHONPATH=/opt/body agenticros-cartesian_gripper:single - < "tests/$test.py"
done
docker run --rm -i --network none --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/agenticros agenticros-cartesian_gripper:single - < tests/test_desktop.py
docker run --rm -i --network none --entrypoint bash agenticros-server:single \
  -c 'source /opt/ros/humble/setup.bash; python3 -' < tests/test_viewer_ui.py
