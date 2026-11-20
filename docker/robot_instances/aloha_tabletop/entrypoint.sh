#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
source /opt/driver_ws/install/setup.bash
if [[ -n "${AGENTICROS_DDS_HOST:-}" ]]; then
  python3 /opt/body/lan_profile.py
  export FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/agenticros-lan.xml
fi
exec /opt/body-venv/bin/python /opt/body/driver.py
