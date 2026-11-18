"""Raw position-servo ROS driver; deliberately no move/grip standard adapter."""
import json
import io
import math
import os
import threading
import time
from pathlib import Path

import numpy as np
from PIL import Image as PILImage
import mujoco
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.node import Node
from rcl_interfaces.msg import ParameterDescriptor
from sensor_msgs.msg import JointState, Image, CompressedImage, CameraInfo, PointCloud2, PointField
from geometry_msgs.msg import TransformStamped
from std_msgs.msg import String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster
from rclpy.qos import qos_profile_sensor_data
from std_srvs.srv import Trigger
from aloha_driver_interfaces.action import Actuate
from aloha_driver_interfaces.srv import Snapshot
from world import Simulation, World
from evaluator import start_evaluator
from sensors import point_cloud_buffer


def valid_goal(positions, duration, limits):
    q = np.asarray(positions, dtype=float)
    return (q.shape == (7,) and np.isfinite(q).all() and np.all(q >= limits[:, 0])
            and np.all(q <= limits[:, 1]) and math.isfinite(duration) and .1 <= duration <= 30)


class CameraPublisher(Node):
    def __init__(self, world, camera, lan=None, node_name='camera_driver'):
        self.lan = lan
        self.robot_namespace = (lan.membership['namespace'] if lan else '') + '/' + camera
        super().__init__(node_name, namespace=self.robot_namespace)
        self.sim = Simulation(world, camera)
        self.render = world.render
        self.pending_frame = None
        self.rgb = self.create_publisher(Image, 'camera/color/image_raw', qos_profile_sensor_data)
        self.rgb_compressed = self.create_publisher(CompressedImage, 'camera/color/image_raw/compressed', qos_profile_sensor_data)
        self.depth = self.create_publisher(Image, 'camera/depth/image_raw', qos_profile_sensor_data)
        self.camera_info = self.create_publisher(CameraInfo, 'camera/camera_info', qos_profile_sensor_data)
        self.points = self.create_publisher(PointCloud2, 'camera/depth/color/points', qos_profile_sensor_data)
        self.camera_tf = (StaticTransformBroadcaster(self) if camera == 'overhead' else TransformBroadcaster(self))
        self.camera_tf_sent = False
        self.create_service(Snapshot, 'driver/observe', self.observe,
                            callback_group=ReentrantCallbackGroup())
        self.create_timer(.5, self.camera)

    def frame(self, name):
        root = (self.lan.membership['namespace'].strip('/')+'/' if self.lan else '')
        return root + (self.sim.arm+'/' if name == 'camera_optical_frame' else '') + name

    def observe(self, request, response):
        snapshot = self.render.submit(self.sim.snapshot, os.getenv('OBSERVATION_ROOT', '/observations')).result()
        if self.lan:
            snapshot = self.lan.export(snapshot)
        response.snapshot_json = json.dumps(snapshot)
        return response

    def capture_frame(self):
        rgb, depth, calibration, _ = self.sim.observe()
        stamp = self.get_clock().now().to_msg()
        stamp.sec = int(calibration['captured_at_unix'])
        stamp.nanosec = int((calibration['captured_at_unix']-stamp.sec)*1e9)
        if self.rgb_compressed.get_subscription_count():
            buffer = io.BytesIO()
            PILImage.fromarray(rgb).save(buffer, format='JPEG', quality=75)
            compressed = CompressedImage(format='rgb8; jpeg compressed bgr8', data=buffer.getvalue())
            compressed.header.stamp, compressed.header.frame_id = stamp, self.frame('camera_optical_frame')
            self.rgb_compressed.publish(compressed)
        for publisher, pixels, encoding, step in (
                (self.rgb, rgb, 'rgb8', rgb.shape[1] * 3),
                (self.depth, depth.astype('<f4'), '32FC1', depth.shape[1] * 4)):
            msg = Image(height=pixels.shape[0], width=pixels.shape[1], encoding=encoding,
                        is_bigendian=0, step=step, data=pixels.tobytes())
            msg.header.stamp, msg.header.frame_id = stamp, self.frame('camera_optical_frame')
            publisher.publish(msg)
        k = np.asarray(calibration['K']).reshape(3, 3)
        info = CameraInfo(width=calibration['width'], height=calibration['height'],
                          distortion_model='plumb_bob', d=[0.] * 5,
                          k=k.ravel().tolist(), r=np.eye(3).ravel().tolist(),
                          p=np.column_stack((k, np.zeros(3))).ravel().tolist())
        info.header.stamp, info.header.frame_id = stamp, self.frame('camera_optical_frame')
        self.camera_info.publish(info)
        buffer, count = point_cloud_buffer(depth, rgb, k)
        cloud = PointCloud2(height=1, width=count, is_bigendian=False, is_dense=True,
                            point_step=16, row_step=count * 16, data=buffer,
                            fields=[PointField(name=name, offset=index * 4, count=1,
                                               datatype=PointField.UINT32 if name == 'rgb' else PointField.FLOAT32)
                                    for index, name in enumerate(('x', 'y', 'z', 'rgb'))])
        cloud.header = info.header
        self.points.publish(cloud)
        if self.sim.arm != 'overhead' or not self.camera_tf_sent:
            transform = TransformStamped()
            transform.header.stamp, transform.header.frame_id = stamp, self.frame('base')
            transform.child_frame_id = self.frame('camera_optical_frame')
            t = calibration['optical_to_base_translation']
            transform.transform.translation.x, transform.transform.translation.y, transform.transform.translation.z = t
            quaternion = np.empty(4)
            mujoco.mju_mat2Quat(quaternion, np.asarray(calibration['optical_to_base_rotation']).ravel())
            q = transform.transform.rotation
            q.w, q.x, q.y, q.z = quaternion.tolist()
            self.camera_tf.sendTransform(transform)
            self.camera_tf_sent = True
        # Health is sensor production, independent of any visualization process.
        Path('/tmp/body-camera-ready').touch()

    def camera(self):
        if self.pending_frame is None or self.pending_frame.done():
            self.pending_frame = self.render.submit(self.capture_frame)


class Driver(CameraPublisher):
    def __init__(self, world, arm, lan=None):
        super().__init__(world, arm, lan, node_name='raw_body_driver')
        # Public device metadata is discovered over standard ROS parameter services.
        # No simulator source, scene state, evaluator or client program is exported.
        public = Path('/opt/device-public')
        sources = {name: (public / 'interfaces' / name).read_text() for name in (
            'package.xml', 'CMakeLists.txt', 'action/Actuate.action', 'srv/Snapshot.srv')}
        documentation = (public / 'body.md').read_text()
        if lan:
            documentation = documentation.replace('`/driver/', '`'+self.robot_namespace+'/driver/').replace('`/camera/', '`'+self.robot_namespace+'/camera/')
            documentation += ('\nLAN mode (headless): identity '+json.dumps(lan.identity())+
                '\nAll endpoints and frames are scoped to this robot. No robot_description publisher or RViz desktop in LAN mode. '
                'Snapshot returns rgb_url, depth_url, snapshot_json_url instead of local paths. '
                'Download these immutable public artifacts into your workspace; validate session, identity and SHA256. '
                'Keep URLs private: they carry read-only snapshot tokens. '
                'Instance heartbeat loss aborts active motion and rejects new goals until explicit client restart.\n')
        self.declare_parameter('device_documentation', documentation,
                               ParameterDescriptor(read_only=True, description='Public device manual'))
        self.declare_parameter('interface_sources', json.dumps({
            'package': 'aloha_driver_interfaces', 'files': sources}),
            ParameterDescriptor(read_only=True, description='Public ROS interface package sources; build locally to use custom types'))
        self.gate = threading.Lock()
        self.busy = self.recovery_required = False
        self.receipts = {}
        self.active_goal_id = None
        self.shutting_down = threading.Event()
        group = ReentrantCallbackGroup()
        self.action = ActionServer(self, Actuate, 'driver/actuate', self.execute,
                                   goal_callback=self.accept, handle_accepted_callback=self.accepted,
                                   cancel_callback=lambda _: CancelResponse.ACCEPT,
                                   callback_group=group)
        self.create_service(Trigger, 'driver/status', self.status, callback_group=group)
        self.joints = self.create_publisher(JointState, 'driver/joint_states', 10)
        self.telemetry = self.create_publisher(String, 'driver/state', 10)
        self.create_timer(.1, self.publish)

    def measured_state(self):
        state = self.sim.state()
        if self.lan:
            state['frame'] = self.frame('base')
        return state

    def accept(self, request):
        with self.gate:
            if (self.shutting_down.is_set() or request.session_id != self.sim.session
                    or (self.lan and not self.lan.lease.valid())
                    or self.busy or self.recovery_required
                    or self.sim.world.failure or not valid_goal(request.positions, request.duration, self.sim.limits)):
                return GoalResponse.REJECT
            self.busy = True
        return GoalResponse.ACCEPT

    def accepted(self, handle):
        with self.gate:
            self.active_goal_id = bytes(handle.goal_id.uuid).hex()
        handle.execute()

    def hold(self):
        self.sim.set_target(self.measured_state()['joint_positions'])
        deadline = time.monotonic() + 2.
        while time.monotonic() < deadline:
            if self.sim.world.failure:
                return False
            if max(abs(v) for v in self.measured_state()['joint_velocities']) < .02:
                return True
            time.sleep(.02)
        return False

    def execute(self, handle):
        gid = bytes(handle.goal_id.uuid).hex()
        count = max(1, int(math.ceil(handle.request.duration / .02)))
        start = np.array(self.measured_state()['joint_positions'])
        target = np.array(handle.request.positions)
        executed, reason, complete, stopped = 'none', 'settled', False, False
        try:
            for i in range(1, count + 61):
                if handle.is_cancel_requested or self.shutting_down.is_set() or self.sim.world.failure or (self.lan and not self.lan.lease.valid()):
                    reason = "cancelled" if handle.is_cancel_requested else ("lease_lost" if self.lan and not self.lan.lease.valid() else "physics_fault" if self.sim.world.failure else "shutdown")
                    stopped = self.hold()
                    break
                with self.sim.lock:
                    self.sim.set_target(start + min(i / count, 1.) * (target - start))
                    positions = self.measured_state()['joint_positions']
                executed = 'some'
                handle.publish_feedback(Actuate.Feedback(positions=positions))
                time.sleep(.02)
            else:
                with self.sim.lock:
                    stopped = bool(np.max(np.abs(self.measured_state()['joint_velocities'])) < .02)
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
                       reason=reason, arm=self.sim.arm, state=self.measured_state(), goal_id=gid, session_id=self.sim.session)
        if self.lan:
            receipt.update(self.lan.identity())
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
            state = dict(session_id=self.sim.session, arm=self.sim.arm, busy=self.busy,
                         recovery_required=self.recovery_required, active_goal_id=self.active_goal_id,
                         execution_status=("fault" if self.recovery_required or self.sim.world.failure else
                                           "executing" if self.busy else "idle"),
                         receipts=dict(self.receipts))
        state['state'] = self.measured_state()
        state['stop_confirmed'] = bool(not self.sim.world.failure and not state['busy'] and not state['recovery_required']
                                       and np.max(np.abs(state['state']['joint_velocities'])) < .02)
        if self.lan:
            state.update(self.lan.identity())
            state['connected'] = self.lan.lease.valid()
        response.success, response.message = True, json.dumps(state)
        return response

    def publish(self):
        state = self.measured_state()
        msg = JointState()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.name, msg.position, msg.velocity = state['joint_names'], state['joint_positions'], state['joint_velocities']
        self.joints.publish(msg)
        with self.gate:
            telemetry = dict(session_id=self.sim.session, busy=self.busy,
                             recovery_required=self.recovery_required, state=state)
        if self.lan:
            telemetry.update(self.lan.identity())
            telemetry['connected'] = self.lan.lease.valid()
        self.telemetry.publish(String(data=json.dumps(telemetry)))



def main():
    lan = None
    if os.getenv('AGENTICROS_SERVER'):
        from registration import Client
        lan = Client()
    rclpy.init()
    world = World(int(os.getenv('BODY_SEED', '0')))
    if lan:
        world.session = lan.session
    drivers = [Driver(world, arm, lan) for arm in ('left', 'right')]
    cameras = [CameraPublisher(world, 'overhead', lan)]
    evaluator = start_evaluator(drivers)
    executor = MultiThreadedExecutor(num_threads=8)
    for driver in [*drivers, *cameras]:
        executor.add_node(driver)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        for driver in drivers:
            driver.shutting_down.set()
        executor.shutdown()
        evaluator.shutdown()
        evaluator.server_close()
        for driver in [*drivers, *cameras]:
            driver.destroy_node()
        world.close()
        if lan:
            lan.close()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
