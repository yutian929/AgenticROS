"""Joint and Cartesian position actions with shared per-arm ownership."""
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
from sensor_msgs.msg import JointState, Image, CompressedImage, CameraInfo, PointCloud2, PointField
from geometry_msgs.msg import TransformStamped, Pose, PoseStamped
from std_msgs.msg import String
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_srvs.srv import Trigger
from aloha_driver_interfaces.action import Actuate, MovePose, MoveDelta, Recover
from world import Simulation, World
from evaluator import start_evaluator
from sensors import point_cloud_buffer
from cartesian import (CartesianPath, resolve_target, tcp_pose, matrix_quat, pose_error,
                       arm_base_pose, relative_pose, public_pose,
                       POSITION_TOLERANCE, ORIENTATION_TOLERANCE)


def valid_goal(positions, duration, limits):
    q = np.asarray(positions, dtype=float)
    return (q.shape == (7,) and np.isfinite(q).all() and np.all(q >= limits[:, 0])
            and np.all(q <= limits[:, 1]) and math.isfinite(duration) and .1 <= duration <= 30)


# Compatible with RViz defaults and Best Effort sensor subscribers.
SENSOR_QOS = QoSProfile(depth=5, reliability=ReliabilityPolicy.RELIABLE)


class CameraPublisher(Node):
    def __init__(self, world, camera, lan=None, node_name='camera_driver'):
        self.lan = lan
        self.robot_namespace = (lan.namespace if lan else '') + '/' + camera
        super().__init__(node_name, namespace=self.robot_namespace)
        self.sim = Simulation(world, camera)
        self.render = world.render
        self.pending_frame = None
        self.rgb = self.create_publisher(Image, 'camera/color/image_raw', SENSOR_QOS)
        self.rgb_compressed = self.create_publisher(CompressedImage, 'camera/color/image_raw/compressed', SENSOR_QOS)
        self.depth = self.create_publisher(Image, 'camera/depth/image_raw', SENSOR_QOS)
        self.camera_info = self.create_publisher(CameraInfo, 'camera/camera_info', SENSOR_QOS)
        self.points = self.create_publisher(PointCloud2, 'camera/depth/color/points', SENSOR_QOS)
        self.camera_tf = (StaticTransformBroadcaster(self) if camera in ('head', 'overhead') else TransformBroadcaster(self))
        self.camera_tf_sent = False
        self.create_timer(.5, self.camera)

    def frame(self, name):
        root = (self.lan.namespace.strip('/')+'/' if self.lan else '')
        return root + (self.sim.arm+'/' if name == 'camera_optical_frame' else '') + name


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
        if self.sim.arm not in ('head', 'overhead') or not self.camera_tf_sent:
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


class ScannerPublisher(Node):
    def __init__(self, world, lan=None):
        super().__init__('scanner_driver', namespace=lan.namespace if lan else '')
        self.world, self.lan = world, lan
        self.publisher = self.create_publisher(String, 'scanner/state', 10)
        self.create_timer(.1, self.publish)

    def publish(self):
        with self.world.lock:
            state = dict(session_id=self.world.session, observed_at_unix=time.time(),
                         **self.world.scanner.status())
        if self.lan:
            state.update(self.lan.identity())
        self.publisher.publish(String(data=json.dumps(state)))


class Driver(CameraPublisher):
    def __init__(self, world, arm, lan=None):
        super().__init__(world, arm, lan, node_name='raw_body_driver')
        self.gate = threading.Lock()
        self.busy = self.recovery_required = False
        self.receipts = {}
        self.active_goal_id = None
        self.recovery_goal_id = None
        self.active_operation = self.active_phase = None
        self.shutting_down = threading.Event()
        group = ReentrantCallbackGroup()
        self.action = ActionServer(self, Actuate, 'driver/actuate', self.execute,
                                   goal_callback=self.accept, handle_accepted_callback=self.accepted,
                                   cancel_callback=lambda _: CancelResponse.ACCEPT,
                                   callback_group=group)
        self.pose_action = ActionServer(self, MovePose, 'driver/move_pose',
            lambda handle: self.execute_cartesian(handle, False),
            goal_callback=lambda request: self.accept_cartesian(request, False),
            handle_accepted_callback=self.accepted, cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=group)
        self.delta_action = ActionServer(self, MoveDelta, 'driver/move_delta',
            lambda handle: self.execute_cartesian(handle, True),
            goal_callback=lambda request: self.accept_cartesian(request, True),
            handle_accepted_callback=self.accepted, cancel_callback=lambda _: CancelResponse.ACCEPT,
            callback_group=group)
        self.recover_action = ActionServer(self, Recover, 'driver/recover', self.execute_recovery,
            goal_callback=self.accept_recovery, handle_accepted_callback=self.accepted,
            cancel_callback=lambda _: CancelResponse.ACCEPT, callback_group=group)
        self.tcp_tf = TransformBroadcaster(self)
        self.mount_tf = StaticTransformBroadcaster(self)
        with self.sim.lock:
            position, rotation = arm_base_pose(self.sim.model, self.sim.data, arm)
        mount = TransformStamped()
        mount.header.stamp = self.get_clock().now().to_msg()
        mount.header.frame_id, mount.child_frame_id = self.frame('base'), self.frame(arm+'/arm_base')
        mount.transform.translation.x, mount.transform.translation.y, mount.transform.translation.z = map(float, position)
        q = mount.transform.rotation
        q.x, q.y, q.z, q.w = map(float, matrix_quat(rotation))
        self.mount_tf.sendTransform(mount)
        self.create_service(Trigger, 'driver/status', self.status, callback_group=group)
        self.joints = self.create_publisher(JointState, 'driver/joint_states', 10)
        self.telemetry = self.create_publisher(String, 'driver/state', 10)
        self.ee_pose = self.create_publisher(PoseStamped, 'driver/ee_pose', 10)
        self.create_timer(.1, self.publish)

    def measured_state(self):
        state = self.sim.state()
        if self.lan:
            state['frame'] = self.frame('base')
            state['tcp_frame'] = self.frame(self.sim.arm+'/tcp')
            state['pose_frame'] = self.frame(self.sim.arm+'/arm_base')
        return state

    def accept(self, request):
        return self.reserve(request, valid_goal(request.positions, request.duration, self.sim.limits))

    def reserve(self, request, valid, operation='actuate'):
        with self.gate:
            if (self.shutting_down.is_set() or request.session_id != self.sim.session
                    or self.busy or self.recovery_required
                    or self.sim.world.failure or not self.feedback_fresh() or not valid):
                return GoalResponse.REJECT
            self.busy = True
            self.active_operation, self.active_phase = operation, 'reserved'
        return GoalResponse.ACCEPT

    def feedback_fresh(self):
        return bool(self.sim.world.updated and time.time()-self.sim.world.updated < 5.)

    def accept_recovery(self, request):
        with self.gate:
            if (self.shutting_down.is_set() or self.sim.world.failure or not self.feedback_fresh()
                    or self.busy or not self.recovery_required or request.session_id != self.sim.session
                    or request.failed_goal_id != self.recovery_goal_id):
                return GoalResponse.REJECT
            self.busy = True
            self.active_operation, self.active_phase = 'recover', 'reserved'
        return GoalResponse.ACCEPT

    def reference_frame(self, name):
        name = name.lstrip('/')
        for short, full in [('base', self.frame('base')),
                            ('arm_base', self.frame(self.sim.arm+'/arm_base')),
                            ('tcp', self.frame(self.sim.arm+'/tcp'))]:
            if name in (short, full):
                return short
        raise ValueError('reference_frame must identify this robot base, arm_base or tcp')

    @staticmethod
    def cartesian_values(request, delta):
        position = request.translation if delta else request.target.position
        rotation = request.rotation if delta else request.target.orientation
        return [position.x, position.y, position.z], [rotation.x, rotation.y, rotation.z, rotation.w]

    def accept_cartesian(self, request, delta):
        try:
            translation, rotation = self.cartesian_values(request, delta)
            frame = self.reference_frame(request.reference_frame)
            resolve_target((np.zeros(3), np.eye(3)), translation, rotation, frame, delta,
                           (np.zeros(3), np.eye(3)))
            valid = math.isfinite(request.duration) and .1 <= request.duration <= 30 and self.settled()
        except (ValueError, TypeError):
            valid = False
        return self.reserve(request, valid, 'move_delta' if delta else 'move_pose')

    def interruption(self, handle):
        if handle.is_cancel_requested:
            return 'cancelled'
        if self.shutting_down.is_set():
            return 'shutdown'
        if self.sim.world.failure:
            return 'physics_fault'
        if not self.feedback_fresh():
            return 'feedback_unavailable'
        return None

    @staticmethod
    def pose_message(pose):
        result = Pose()
        result.position.x, result.position.y, result.position.z = map(float, pose[0])
        q = result.orientation
        q.x, q.y, q.z, q.w = map(float, matrix_quat(pose[1]))
        return result

    def current_pose(self):
        with self.sim.lock:
            return tcp_pose(self.sim.model, self.sim.data, self.sim.arm)

    def local_pose(self, pose):
        with self.sim.lock:
            return relative_pose(pose, arm_base_pose(self.sim.model, self.sim.data, self.sim.arm))

    def settled(self):
        return bool(not self.sim.world.failure and self.feedback_fresh()
                    and self.sim.stop_evidence()['settled'])

    def phase(self, name):
        with self.gate:
            self.active_phase = name

    def execute_cartesian(self, handle, delta):
        action_type = MoveDelta if delta else MovePose
        executed, stopped, complete, reason = 'none', False, False, 'planning'
        target = None
        extra = dict(operation='move_delta' if delta else 'move_pose',
                     reference_frame=handle.request.reference_frame, target_frame=self.frame('base'),
                     position_tolerance=POSITION_TOLERANCE, orientation_tolerance=ORIENTATION_TOLERANCE,
                     collision_checked=False)
        def feedback(phase):
            self.phase(phase)
            pose = self.current_pose()
            ep, er = pose_error(pose, target)
            handle.publish_feedback(action_type.Feedback(current_pose=self.pose_message(self.local_pose(pose)),
                position_error=ep, orientation_error=er, phase=phase))
            return ep, er
        try:
            planner = CartesianPath(self.sim)
            translation, rotation = self.cartesian_values(handle.request, delta)
            target = resolve_target(planner.start, translation, rotation,
                                    self.reference_frame(handle.request.reference_frame), delta, planner.arm_base)
            extra.update(start_sim_time=planner.sample_time,
                         start_pose=public_pose(relative_pose(planner.start, planner.arm_base)),
                         target_pose=public_pose(relative_pose(target, planner.arm_base)),
                         pose_frame=self.frame(self.sim.arm+'/arm_base'), gripper_target=planner.gripper_command,
                         start_tcp_xyz=planner.start[0].tolist(),
                         start_tcp_quaternion_xyzw=matrix_quat(planner.start[1]).tolist(),
                         target_tcp_xyz=target[0].tolist(), target_tcp_quaternion_xyzw=matrix_quat(target[1]).tolist())
            feedback('planning')
            path, dt = planner.plan(target, handle.request.duration, lambda: self.interruption(handle))
            if np.max(np.abs(np.asarray(self.measured_state()['joint_positions'])[:6]-planner.initial[:6])) > .03:
                raise ValueError('start_state_changed')
            points = [np.r_[planner.initial[:6], planner.gripper_command], *path]
            start_time = self.measured_state()['sim_time']
            while True:
                reason = self.interruption(handle)
                if reason:
                    raise InterruptedError(reason)
                elapsed = self.measured_state()['sim_time']-start_time
                sample = min(elapsed/dt, len(path))
                index = min(int(sample), len(path)-1)
                blend = sample-index
                self.sim.set_target(points[index]+blend*(points[index+1]-points[index]), compensate=True)
                executed = 'some'
                feedback('executing')
                if elapsed >= handle.request.duration:
                    break
                time.sleep(.01)
            while self.measured_state()['sim_time']-start_time < handle.request.duration+3:
                reason = self.interruption(handle)
                if reason:
                    raise InterruptedError(reason)
                ep, er = feedback('settling')
                stopped = self.settled()
                if stopped and ep <= POSITION_TOLERANCE and er <= ORIENTATION_TOLERANCE:
                    complete, reason = True, 'reached'
                    break
                time.sleep(.02)
            if not complete:
                reason = 'target_tolerance_not_reached'
                stopped = self.hold(compensate=True)
        except InterruptedError as exc:
            reason = str(exc)
            stopped = self.hold(compensate=True)
        except Exception as exc:
            reason = type(exc).__name__+': '+str(exc)
            try:
                stopped = self.hold(compensate=True)
            except Exception:
                stopped = False
        if target is not None:
            ep, er = pose_error(self.current_pose(), target)
            extra.update(position_error=ep, orientation_error=er)
            extra['measured_pose'] = public_pose(self.local_pose(self.current_pose()))
        return self.finish(handle, action_type, complete, executed, stopped, reason, extra)

    def accepted(self, handle):
        with self.gate:
            self.active_goal_id = bytes(handle.goal_id.uuid).hex()
            self.active_phase = 'planning' if self.active_operation in ('move_pose', 'move_delta') else 'running'
        handle.execute()

    def hold(self, compensate=False):
        self.phase('stopping')
        if self.sim.world.failure or not self.feedback_fresh():
            return False
        with self.sim.lock:
            target = self.sim.data.qpos[self.sim.qids].copy()
            if compensate:
                target[-1] = self.sim.data.ctrl[self.sim.aids[-1]]
            self.sim.set_target(target, compensate=compensate)
            self.sim.world.motion[self.sim.arm].reset()
        return self.wait_for_stop()

    def wait_for_stop(self, handle=None):
        start_time = self.measured_state()['sim_time']
        deadline = time.monotonic()+8.
        while time.monotonic() < deadline:
            if (self.sim.world.failure or not self.feedback_fresh()
                    or (handle is not None and self.interruption(handle))):
                return False
            if self.settled():
                return True
            if self.measured_state()['sim_time']-start_time > 2.:
                break
            if handle is not None:
                handle.publish_feedback(Recover.Feedback(positions=self.measured_state()['joint_positions']))
            time.sleep(.02)
        return False

    def execute_recovery(self, handle):
        self.phase('recovering')
        with self.sim.lock:
            self.sim.world.motion[self.sim.arm].reset()
        stopped = self.wait_for_stop(handle)
        reason = self.interruption(handle) or ('recovered' if stopped else 'recovery_not_settled')
        return self.finish(handle, Recover, stopped and reason == 'recovered', 'none', stopped, reason,
                           dict(operation='recover', failed_goal_id=handle.request.failed_goal_id))

    def execute(self, handle):
        start = np.array(self.measured_state()['joint_positions'])
        target = np.array(handle.request.positions)
        start_time = self.measured_state()['sim_time']
        executed, reason, complete, stopped = 'none', 'target_tolerance_not_reached', False, False
        try:
            while True:
                interrupted = self.interruption(handle)
                if interrupted:
                    reason = interrupted
                    stopped = self.hold()
                    break
                elapsed = self.measured_state()['sim_time']-start_time
                with self.sim.lock:
                    self.sim.set_target(start + min(elapsed/handle.request.duration, 1.) * (target - start))
                    positions = self.measured_state()['joint_positions']
                executed = 'some'
                handle.publish_feedback(Actuate.Feedback(positions=positions))
                error = np.abs(np.asarray(positions)-target)
                if elapsed >= handle.request.duration and self.settled() and np.all(error < [.03]*6+[.0005]):
                    complete, stopped, reason = True, True, 'reached'
                    break
                if elapsed > handle.request.duration+3.:
                    stopped = self.hold()
                    break
                time.sleep(.02)
        except Exception as exc:
            reason, executed = type(exc).__name__ + ': ' + str(exc), 'unknown'
            try:
                stopped = self.hold()
            except Exception:
                stopped = False
        return self.finish(handle, Actuate, complete, executed, stopped, reason, dict(operation='actuate'))

    def finish(self, handle, action_type, complete, executed, stopped, reason, extra=None):
        with self.sim.lock:
            stopped = bool(stopped and self.settled())
            measured = self.measured_state()
            evidence = self.sim.stop_evidence()
        complete = bool(complete and stopped)
        receipt = dict(complete=complete, executed=executed, stop_confirmed=stopped,
                       reason=reason, arm=self.sim.arm, state=measured,
                       stop_evidence=evidence, task_success=None,
                       joint_positions=measured['joint_positions'],
                       failed_goal_id=None, goal_id=bytes(handle.goal_id.uuid).hex(), session_id=self.sim.session)
        receipt.update(extra or {})
        if self.lan:
            receipt.update(self.lan.identity())
        with self.gate:
            self.receipts[receipt['goal_id']] = receipt
            recovery_failed = action_type is Recover and reason != 'recovered'
            self.recovery_required = not stopped or recovery_failed
            if not stopped and action_type is not Recover:
                self.recovery_goal_id = receipt['goal_id']
            if not self.recovery_required:
                self.recovery_goal_id = None
            self.busy = False
            self.active_goal_id = None
            self.active_phase = self.active_operation = None
        if reason == 'cancelled':
            handle.canceled()
        elif complete:
            handle.succeed()
        else:
            handle.abort()
        return action_type.Result(receipt_json=json.dumps(receipt))

    def snapshot(self):
        with self.gate, self.sim.lock:
            state = dict(session_id=self.sim.session, arm=self.sim.arm, busy=self.busy,
                         reported_at_unix=self.sim.world.updated, simulation_ready=bool(self.sim.world.updated),
                         feedback_fresh=self.feedback_fresh(), fault=self.sim.world.failure,
                         active_operation=self.active_operation, active_phase=self.active_phase,
                         recovery_goal_id=self.recovery_goal_id, stop_evidence=self.sim.stop_evidence(),
                         recovery_required=self.recovery_required, active_goal_id=self.active_goal_id,
                         execution_status=("fault" if self.recovery_required or self.sim.world.failure else
                                           "executing" if self.busy else "idle"),
                         receipts=dict(self.receipts), state=self.measured_state())
            state['stop_confirmed'] = bool(self.settled() and not self.busy and not self.recovery_required)
            state['control_available'] = bool(self.feedback_fresh() and not self.sim.world.failure
                and not self.shutting_down.is_set() and not self.busy and not self.recovery_required)
        if self.lan:
            state.update(self.lan.identity())
        return state

    def status(self, request, response):
        state = self.snapshot()
        response.success, response.message = True, json.dumps(state)
        return response

    def publish(self):
        snapshot = self.snapshot()
        state = snapshot['state']
        msg = JointState()
        stamp = snapshot['reported_at_unix']
        msg.header.stamp.sec, msg.header.stamp.nanosec = int(stamp), int((stamp % 1)*1e9)
        msg.name, msg.position, msg.velocity = state['joint_names'], state['joint_positions'], state['joint_velocities']
        self.joints.publish(msg)
        transform = TransformStamped()
        transform.header.stamp = msg.header.stamp
        transform.header.frame_id = state['pose_frame']
        transform.child_frame_id = self.frame(self.sim.arm+'/tcp')
        t, q = transform.transform.translation, transform.transform.rotation
        t.x, t.y, t.z = [state['tcp_pose']['position'][k] for k in ('x', 'y', 'z')]
        q.x, q.y, q.z, q.w = [state['tcp_pose']['orientation'][k] for k in ('x', 'y', 'z', 'w')]
        self.tcp_tf.sendTransform(transform)
        pose = PoseStamped()
        pose.header = transform.header
        pose.pose.position.x, pose.pose.position.y, pose.pose.position.z = t.x, t.y, t.z
        pose.pose.orientation.x, pose.pose.orientation.y, pose.pose.orientation.z, pose.pose.orientation.w = q.x, q.y, q.z, q.w
        self.ee_pose.publish(pose)
        self.telemetry.publish(String(data=json.dumps(snapshot)))



def main():
    lan = None
    if os.getenv('AGENTICROS_ROBOT_ID'):
        from robot_identity import RobotIdentity
        lan = RobotIdentity()
    rclpy.init()
    world = World(int(os.getenv('BODY_SEED', '0')))
    if lan:
        world.session = lan.session
    drivers = [Driver(world, arm, lan) for arm in ('left', 'right')]
    cameras = [CameraPublisher(world, camera, lan) for camera in ('head', 'overhead')]
    scanner = ScannerPublisher(world, lan)
    evaluator = start_evaluator(drivers)
    executor = MultiThreadedExecutor(num_threads=8)
    for driver in [*drivers, *cameras, scanner]:
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
        for driver in [*drivers, *cameras, scanner]:
            driver.destroy_node()
        world.close()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
