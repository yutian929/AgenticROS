#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
image_tag=${AGENTICROS_TEST_IMAGE_TAG:-single}
for test in test_docker_layout test_lan test_viewer test_instance test_connection test_workspace test_agent_rules test_capabilities test_clean_runtime test_ros_client test_migration test_sam3 test_valen test_quanta; do
  python3 -m unittest discover -s tests -p "$test.py" -v
done
# Disposable processes: no driver, no user volumes, no network.
for test in test_private_evaluator test_sensors; do
  docker run --rm -i --network none --entrypoint /opt/body-venv/bin/python \
    -e PYTHONPATH=/opt/body agenticros-cartesian_gripper:"$image_tag" - < "tests/$test.py"
done
docker run --rm -i --network none --entrypoint /opt/body-venv/bin/python \
  -e PYTHONPATH=/opt/agenticros agenticros-cartesian_gripper:"$image_tag" - < tests/test_desktop.py
docker run --rm -i --network none --entrypoint bash agenticros-viewer:"$image_tag" \
  -c 'source /opt/ros/humble/setup.bash; python3 -' < tests/test_viewer_ui.py
