#!/bin/bash
set -e
source /opt/ros/humble/setup.bash
source /opt/perception_ws/install/setup.bash
exec "$@"
