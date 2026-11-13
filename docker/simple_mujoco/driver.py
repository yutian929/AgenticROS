"""Raw position-servo ROS driver; deliberately no move/grip standard adapter."""
import io
import json
import math
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_srvs.srv import Trigger
from rosbody_driver_interfaces.action import Actuate
from rosbody_driver_interfaces.srv import Snapshot
from world import Simulation
from evaluator import start_evaluator


LOW = np.array([-.38, -.26, -.175, 0, 0])
HIGH = np.array([.38, .26, .13, .04, .04])


def valid_goal(positions, duration):
    q = np.asarray(positions, dtype=float)
    return (q.shape == (5,) and np.isfinite(q).all() and np.all(q >= LOW)
            and np.all(q <= HIGH) and math.isfinite(duration) and .1 <= duration <= 30)


class Driver(Node):
    def __init__(self):
        super().__init__('raw_body_driver')
        self.sim = Simulation(int(os.getenv('BODY_SEED', '0')))
        self.gate = threading.Lock()
        self.busy = self.recovery_required = False
        self.receipts = {}
        self.active_goal_id = None
        self.shutting_down = threading.Event()
        self.render = ThreadPoolExecutor(max_workers=1)
        self.frame = None
        self.pending_frame = None
        group = ReentrantCallbackGroup()
        self.action = ActionServer(self, Actuate, '/driver/actuate', self.execute,
                                   goal_callback=self.accept, handle_accepted_callback=self.accepted,
                                   cancel_callback=lambda _: CancelResponse.ACCEPT,
                                   callback_group=group)
        self.create_service(Trigger, '/driver/status', self.status, callback_group=group)
        self.create_service(Snapshot, '/driver/observe', self.observe, callback_group=group)
        self.joints = self.create_publisher(JointState, '/driver/joint_states', 10)
        self.create_timer(.1, self.publish)
        self.create_timer(.5, self.camera)

    def accept(self, request):
        with self.gate:
            if (self.shutting_down.is_set() or request.session_id != self.sim.session
                    or self.busy or self.recovery_required
                    or not valid_goal(request.positions, request.duration)):
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    def accepted(self, handle):
        with self.gate:
            self.active_goal_id = bytes(handle.goal_id.uuid).hex()
        handle.execute()

    def hold(self):
        with self.sim.lock:
            self.sim.data.ctrl[:] = self.sim.data.qpos[:5]
            self.sim.step(150)
            return bool(np.max(np.abs(self.sim.data.qvel[:5])) < .02)

    def execute(self, handle):
        gid = bytes(handle.goal_id.uuid).hex()
        count = max(1, int(math.ceil(handle.request.duration / .02)))
        start = np.array(self.sim.state()['joint_positions'])
        target = np.array(handle.request.positions)
        executed, reason, complete, stopped = 'none', 'settled', False, False
        try:
            for i in range(1, count + 61):
                if handle.is_cancel_requested or self.shutting_down.is_set():
                    reason = "cancelled" if handle.is_cancel_requested else "shutdown"
                    stopped = self.hold()
                    break
                with self.sim.lock:
                    self.sim.data.ctrl[:] = start + min(i / count, 1.) * (target - start)
                    self.sim.data.qfrc_applied[:3] = self.sim.data.qfrc_bias[:3]
                    self.sim.step(10)
                    positions = self.sim.data.qpos[:5].tolist()
                executed = 'some'
                handle.publish_feedback(Actuate.Feedback(positions=positions))
                time.sleep(.02)
            else:
                with self.sim.lock:
                    stopped = bool(np.max(np.abs(self.sim.data.qvel[:5])) < .02)
                complete = stopped
                if not stopped:
                    reason = 'not_settled'
                    stopped = self.hold()
        except Exception as exc:
            reason, executed = type(exc).__name__ + ': ' + str(exc), 'unknown'
            try:
                stopped = self.hold()
            except Exception:
                stopped = False
        receipt = dict(complete=complete, executed=executed, stop_confirmed=stopped,
                       reason=reason, state=self.sim.state(), goal_id=gid, session_id=self.sim.session)
        with self.gate:
            self.receipts[gid] = receipt
            self.recovery_required = not stopped
            self.busy = False
            self.active_goal_id = None
        if reason == 'cancelled':
            handle.canceled()
        elif complete:
            handle.succeed()
        else:
            handle.abort()
        return Actuate.Result(receipt_json=json.dumps(receipt))

    def status(self, request, response):
        with self.gate:
            state = dict(session_id=self.sim.session, busy=self.busy,
                         recovery_required=self.recovery_required, active_goal_id=self.active_goal_id,
                         execution_status=("fault" if self.recovery_required else
                                           "executing" if self.busy else "idle"),
                         receipts=dict(self.receipts))
        state['state'] = self.sim.state()
        state['stop_confirmed'] = bool(not state['busy'] and not state['recovery_required']
                                       and np.max(np.abs(state['state']['joint_velocities'])) < .02)
        response.success, response.message = True, json.dumps(state)
        return response

    def observe(self, request, response):
        snapshot = self.render.submit(self.sim.snapshot, os.getenv('OBSERVATION_ROOT', '/observations')).result()
        response.snapshot_json = json.dumps(snapshot)
        return response

    def publish(self):
        state = self.sim.state()
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name, msg.position, msg.velocity = state['joint_names'], state['joint_positions'], state['joint_velocities']
        self.joints.publish(msg)

    def capture_frame(self):
        rgb, _, _, _ = self.sim.observe()
        output = io.BytesIO()
        Image.fromarray(rgb).save(output, format='JPEG')
        self.frame = output.getvalue()

    def camera(self):
        if self.pending_frame is None or self.pending_frame.done():
            self.pending_frame = self.render.submit(self.capture_frame)


def viewer_handler(driver):
    class Viewer(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.split('?')[0] == '/frame.jpg':
                body, kind = driver.frame, 'image/jpeg'
                if body is None:
                    self.send_error(503, 'Camera starting')
                    return
            elif self.path == '/':
                body = b'''<!doctype html><meta charset="utf-8"><title>ROSBody camera</title>
<style>body{background:#15191f;color:#eef;font:18px system-ui;text-align:center}img{width:min(95vw,960px)}</style>
<h1>ROSBody live camera</h1><p>Read-only observation; control from the Codex terminal.</p>
<img id="camera"><script>setInterval(()=>{document.getElementById('camera').src='/frame.jpg?t='+Date.now()},500)</script>'''
                kind = 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    return Viewer


def main():
    rclpy.init()
    driver = Driver()
    server = ThreadingHTTPServer(('0.0.0.0', int(os.getenv('VIEWER_PORT', '8080'))), viewer_handler(driver))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    evaluator = start_evaluator(driver)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(driver)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        driver.shutting_down.set()
        server.shutdown()
        evaluator.shutdown()
        evaluator.server_close()
        executor.shutdown()
        driver.render.shutdown()
        driver.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
