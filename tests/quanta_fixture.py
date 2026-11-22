"""Synthetic feedback backend for ROS transport only; not a physics simulation."""
import sys
sys.path.insert(0, '/opt/body')
import threading
import time
import rclpy
from rclpy.executors import MultiThreadedExecutor
from robot_identity import RobotIdentity
from control import Control, ARMS
from driver import Driver

rclpy.init()
identity = RobotIdentity()
control = Control(identity.session)
positions = {a: [0.]*7 for a in ARMS}
velocities = {a: [0.]*7 for a in ARMS}
control.initialize(positions, {a: [(-2.,2.)]*6+[(0.,1.89)] for a in ARMS})
# Start with a real state-machine stop failure for recovery transport checks.
control.reserve('left', identity.session, [.1]*7, 1.)
control.accepted('left', 'fixture_failed_goal')
control.cancel('left', 'fixture_failed_goal')
for step in range(550):
    positions['left'][0] += .001
    control.tick(positions, velocities, step*.005)
assert control.recovery['left']
node = Driver(identity, control)
executor = MultiThreadedExecutor(num_threads=6)
executor.add_node(node)
threading.Thread(target=executor.spin, daemon=True).start()
started = time.monotonic()
while rclpy.ok():
    positions = control.tick(positions, velocities, 3.+time.monotonic()-started)
    time.sleep(.01)
