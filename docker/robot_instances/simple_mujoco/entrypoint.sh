#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
source /opt/driver_ws/install/setup.bash
if [[ -n "${AGENTICROS_SERVER:-}" ]]; then
  exec /opt/body-venv/bin/python /opt/body/driver.py
fi
exec /opt/body-venv/bin/python /opt/agenticros/desktop.py
