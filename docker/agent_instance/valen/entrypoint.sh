#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
source /opt/decision_ws/install/setup.bash
exec "$@"
