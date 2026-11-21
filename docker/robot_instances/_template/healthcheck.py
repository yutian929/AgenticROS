"""模板只检查 ROS 回调循环；实际接入时改为本型号的就绪条件。"""
from pathlib import Path
import time

ready = Path('/tmp/robot-ready')
assert ready.exists() and time.time() - ready.stat().st_mtime < 5, 'Driver heartbeat stale'
# 按需修改：例如检查驱动连接/传感器更新，不发送运动指令。
