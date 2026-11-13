"""ROS 2 server. Simulation only; one action owns the body at a time."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
import uuid
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, GoalResponse, CancelResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo, JointState
from geometry_msgs.msg import TransformStamped
from tf2_msgs.msg import TFMessage
from std_srvs.srv import Trigger
from rosbody_interfaces.action import Move, Gripper
from rosbody_interfaces.srv import Snapshot
from .sim import Simulation


class BodyServer(Node):
    def __init__(self, root, seed=0, viewer=False):
        super().__init__("rosbody_sim")
        self.sim = Simulation(seed)
        self.feedback_delay = 0.02 if viewer else 0.002
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.group = ReentrantCallbackGroup()
        self.admission = threading.Lock()
        self.busy = False
        self.recovery_required = False
        self.log_lock = threading.Lock()
        self.render_worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="renderer")
        self.receipts = {}
        self.move_server = ActionServer(self, Move, "/body/move_free", self.execute_move,
            goal_callback=self.accept_move, cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=self.group)
        self.grip_server = ActionServer(self, Gripper, "/body/gripper", self.execute_grip,
            goal_callback=self.accept_grip, cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=self.group)
        self.create_service(Snapshot, "/body/capture_snapshot", self.snapshot, callback_group=self.group)
        self.create_service(Trigger, "/body/status", self.status, callback_group=self.group)
        self.create_service(Trigger, "/sim/reset", self.reset, callback_group=self.group)
        self.rgb_pub = self.create_publisher(Image, "/camera/front/rgb", 2)
        self.depth_pub = self.create_publisher(Image, "/camera/front/depth", 2)
        self.info_pub = self.create_publisher(CameraInfo, "/camera/front/camera_info", 2)
        self.joint_pub = self.create_publisher(JointState, "/joint_states", qos_profile_sensor_data)
        self.tf_pub = self.create_publisher(TFMessage, "/tf", 10)
        self.create_timer(0.1, self.publish_state)
        self.create_timer(1.0, self.publish_camera)
        self.get_logger().info(f"Simulation ready; records: {self.root}; one active action; no hardware")

    def log(self, event):
        with self.log_lock:
            with (self.root / "events.jsonl").open("a") as f:
                f.write(json.dumps({"wall_time": time.time(), **event}) + "\n")

    def reserve(self):
        with self.admission:
            if self.busy or self.recovery_required:
                return GoalResponse.REJECT
            self.busy = True
            return GoalResponse.ACCEPT

    def accept_move(self, request):
        try:
            self.sim.validate_move(request.xyz, request.speed)
        except ValueError:
            return GoalResponse.REJECT
        return self.reserve()

    def accept_grip(self, request):
        if not np.isfinite(request.width) or not 0 <= request.width <= 0.08:
            return GoalResponse.REJECT
        return self.reserve()

    def execute(self, handle, kind):
        goal_id = bytes(handle.goal_id.uuid).hex()
        self.log({"kind": "action_started", "goal_id": goal_id, "action": kind})
        try:
            def feedback(state):
                if kind == "move":
                    msg = Move.Feedback()
                    msg.actual_xyz = state["tcp_xyz"]
                    msg.remaining_distance = float(np.linalg.norm(np.array(handle.request.xyz)-state["tcp_xyz"]))
                else:
                    msg = Gripper.Feedback()
                    msg.actual_width = state["gripper_width"]
                handle.publish_feedback(msg)
                time.sleep(self.feedback_delay)  # Visible pacing; release simulation lock before sleeping.
            if kind == "move":
                receipt = self.sim.move(handle.request.xyz, handle.request.speed,
                                        lambda: handle.is_cancel_requested, feedback)
            else:
                receipt = self.sim.grip(handle.request.width, lambda: handle.is_cancel_requested, feedback)
        except Exception as exc:
            receipt = self.sim.receipt(False, "unknown", f"{type(exc).__name__}: {exc}", False)
        receipt.update(goal_id=goal_id, action=kind, session_id=self.sim.session)
        self.log({"kind": "action_result", **receipt})
        with self.admission:
            self.receipts[goal_id] = receipt
            self.recovery_required = not receipt["stop_confirmed"]
            self.busy = False
        if receipt["reason"] == "cancelled":
            handle.canceled()
        elif receipt["complete"]:
            handle.succeed()
        else:
            handle.abort()
        result = Move.Result() if kind == "move" else Gripper.Result()
        result.receipt_json = json.dumps(receipt)
        # Evaluator-only file; deliberately absent from ROS results.
        (self.root / "evaluator.json").write_text(json.dumps(self.sim.evaluate(), indent=2))
        return result

    def execute_move(self, handle):
        return self.execute(handle, "move")

    def execute_grip(self, handle):
        return self.execute(handle, "gripper")

    def snapshot(self, request, response):
        result = self.render_worker.submit(self.sim.snapshot, self.root).result()
        self.log({"kind": "snapshot", "snapshot_id": result["snapshot_id"]})
        response.snapshot_json = json.dumps(result)
        return response

    def status(self, request, response):
        with self.admission:
            payload = {"session_id": self.sim.session, "busy": self.busy,
                       "recovery_required": self.recovery_required,
                       "receipts": dict(self.receipts)}
        payload["state"] = self.sim.state()
        response.success = True
        response.message = json.dumps(payload)
        return response

    def reset(self, request, response):
        with self.admission:
            if self.busy:
                response.success = False
                response.message = "active action; cancel and wait before reset"
                return response
            self.busy = True
        try:
            self.sim.reset(self.sim.seed)
            self.sim.session = uuid.uuid4().hex[:12]
            with self.admission:
                self.receipts.clear()
                self.recovery_required = False
            response.success = True
            response.message = "simulation reset; discard previous observations"
            self.log({"kind": "reset", "session_id": self.sim.session})
        finally:
            with self.admission:
                self.busy = False
        return response

    def publish_state(self):
        state = self.sim.state()
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "base"
        msg.name = state["joint_names"]
        msg.position = state["joint_positions"]
        msg.velocity = state["joint_velocities"]
        self.joint_pub.publish(msg)

    def publish_camera(self):
        rgb, depth, cal, state = self.render_worker.submit(self.sim.observe).result()
        stamp = self.get_clock().now().to_msg()
        for array, encoding, publisher in [(rgb, "rgb8", self.rgb_pub), (depth.astype(np.float32), "32FC1", self.depth_pub)]:
            msg = Image()
            msg.header.stamp = stamp
            msg.header.frame_id = "front_optical"
            msg.height, msg.width = array.shape[:2]
            msg.encoding = encoding
            msg.step = array.strides[0]
            msg.data = array.tobytes()
            publisher.publish(msg)
        info = CameraInfo()
        info.header.stamp = stamp
        info.header.frame_id = "front_optical"
        info.width, info.height = 640, 480
        info.k = np.array(cal["K"]).flatten().tolist()
        info.distortion_model = "plumb_bob"
        info.d = [0.] * 5
        info.r = np.eye(3).flatten().tolist()
        info.p = np.column_stack((np.array(cal["K"]), np.zeros(3))).flatten().tolist()
        self.info_pub.publish(info)
        tf = TransformStamped()
        tf.header.stamp = stamp
        tf.header.frame_id = "base"
        tf.child_frame_id = "front_optical"
        tf.transform.translation.z = 1.25
        tf.transform.rotation.x = 1.0  # Optical y down, z towards the table.
        self.tf_pub.publish(TFMessage(transforms=[tf]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="runs/server")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--viewer", action="store_true")
    args = parser.parse_args()
    rclpy.init()
    node = BodyServer(args.out, args.seed, args.viewer)
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        if args.viewer:
            import mujoco.viewer
            spin = threading.Thread(target=executor.spin, daemon=True)
            spin.start()
            with mujoco.viewer.launch_passive(node.sim.model, node.sim.data) as viewer:
                viewer.cam.lookat[:] = [0, 0, 0]
                viewer.cam.distance = 1.15
                viewer.cam.azimuth = 90
                viewer.cam.elevation = -65
                while viewer.is_running() and rclpy.ok():
                    with node.sim.lock:
                        viewer.sync()
                    time.sleep(0.03)
        else:
            executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        executor.shutdown()
        node.render_worker.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
