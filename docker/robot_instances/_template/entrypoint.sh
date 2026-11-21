#!/bin/bash
set -eo pipefail
source /opt/ros/humble/setup.bash
# 按需修改：加载自己的 ROS workspace，再替换为实际驱动启动命令。
# ROS_DOMAIN_ID、AGENTICROS_ROBOT_ID 由启动器传入，不要在这里写死。
exec python3 /opt/robot/driver.py
