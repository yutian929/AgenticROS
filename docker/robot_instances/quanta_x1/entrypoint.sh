#!/bin/bash
set -eo pipefail
source /opt/ros/jazzy/setup.bash
source /opt/driver_ws/install/setup.bash
mkdir -p /tmp/kit
exec /opt/sim/bin/python /opt/body/simulation.py --portable-root /tmp/kit
