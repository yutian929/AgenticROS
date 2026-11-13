#!/bin/bash
set -e
source /opt/ros/jazzy/setup.bash
source /opt/driver_ws/install/setup.bash
exec /opt/body-venv/bin/python /opt/body/driver.py
