"""ROS facade; does not call Isaac/Kit APIs from executor threads."""
import json
import time
from pathlib import Path

import rclpy
from rclpy.node import Node
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, ReliabilityPolicy
from std_msgs.msg import String
from std_srvs.srv import Trigger
from sensor_msgs.msg import JointState, Image, CameraInfo
from geometry_msgs.msg import TransformStamped, Pose, PoseStamped
from tf2_ros import TransformBroadcaster
from quanta_driver_interfaces.action import Actuate, Recover, MovePose
from control import ARMS
from cartesian import Kinematics


class Driver(Node):
    def __init__(self, identity, control):
        super().__init__('driver', namespace=identity.namespace)
        self.identity, self.control = identity, control
        self.control.namespace = identity.namespace
        if self.control.kinematics is None:
            self.control.kinematics = Kinematics()
        self.telemetry, self.joints, self.images, self.intrinsics = {}, {}, {}, {}
        self.transforms = TransformBroadcaster(self)
        self.poses = {}
        self.scanner_state = self.create_publisher(String, 'scanner/state', 10)
        self.actions = []
        group = ReentrantCallbackGroup()
        for arm in ARMS:
            self.telemetry[arm] = self.create_publisher(String, f'{arm}/driver/state', 10)
            self.joints[arm] = self.create_publisher(JointState, f'{arm}/driver/joint_states', 10)
            self.poses[arm] = self.create_publisher(PoseStamped, f'{arm}/driver/ee_pose', 10)
            self.create_service(Trigger, f'{arm}/driver/status',
                                lambda req, res, a=arm: self.status(a, res), callback_group=group)
            self.actions.append(ActionServer(
                self, Actuate, f'{arm}/driver/actuate',
                lambda handle, a=arm: self.execute(a, handle),
                goal_callback=lambda req, a=arm: self.accept(a, req),
                handle_accepted_callback=lambda handle, a=arm: self.accepted(a, handle),
                cancel_callback=lambda handle: CancelResponse.ACCEPT,
                callback_group=group))
            self.actions.append(ActionServer(
                self, Recover, f'{arm}/driver/recover',
                lambda handle, a=arm: self.execute(a, handle, Recover),
                goal_callback=lambda req, a=arm: GoalResponse.ACCEPT if self.control.reserve_recovery(
                    a, req.session_id, req.failed_goal_id) else GoalResponse.REJECT,
                handle_accepted_callback=lambda handle, a=arm: self.accepted(a, handle),
                cancel_callback=lambda handle: CancelResponse.ACCEPT,
                callback_group=group))
            self.actions.append(ActionServer(
                self, MovePose, f'{arm}/driver/move_pose',
                lambda handle, a=arm: self.execute(a, handle, MovePose),
                goal_callback=lambda req, a=arm: self.accept_pose(a, req),
                handle_accepted_callback=lambda handle, a=arm: self.accepted(a, handle),
                cancel_callback=lambda handle: CancelResponse.ACCEPT,
                callback_group=group))
        qos = QoSProfile(depth=2, reliability=ReliabilityPolicy.RELIABLE)
        for camera in ('left', 'right', 'head'):
            self.images[camera] = self.create_publisher(Image, f'{camera}/camera/color/image_raw', qos)
            self.intrinsics[camera] = self.create_publisher(CameraInfo, f'{camera}/camera/camera_info', qos)
        self.create_timer(.1, self.publish)

    def snapshot(self, arm):
        return dict(**self.identity.identity(), **self.control.status(arm))

    def status(self, arm, response):
        response.success, response.message = True, json.dumps(self.snapshot(arm))
        return response

    def accept(self, arm, request):
        ok = self.control.reserve(arm, request.session_id, request.positions, request.duration)
        return GoalResponse.ACCEPT if ok else GoalResponse.REJECT

    def accepted(self, arm, handle):
        self.control.accepted(arm, bytes(handle.goal_id.uuid).hex())
        handle.execute()

    def accept_pose(self, arm, request):
        p, q = request.target.position, request.target.orientation
        ok = self.control.reserve_pose(arm, request.session_id, request.reference_frame,
            [p.x,p.y,p.z], [q.x,q.y,q.z,q.w], request.duration)
        return GoalResponse.ACCEPT if ok else GoalResponse.REJECT

    @staticmethod
    def pose(data):
        message = Pose()
        for key, value in data['position'].items():
            setattr(message.position, key, float(value))
        for key, value in data['orientation'].items():
            setattr(message.orientation, key, float(value))
        return message

    def execute(self, arm, handle, action_type=Actuate):
        gid = bytes(handle.goal_id.uuid).hex()
        if action_type is MovePose:
            def interrupted():
                if handle.is_cancel_requested:
                    self.control.cancel(arm, gid)
                    return 'cancelled'
                return None
            self.control.plan_pose(arm, gid, interrupted)
        while rclpy.ok():
            if handle.is_cancel_requested:
                self.control.cancel(arm, gid)
            state = self.snapshot(arm)
            receipt = state['receipts'].get(gid)
            if receipt is not None:
                if receipt['reason'] == 'cancelled':
                    handle.canceled()
                elif receipt['complete']:
                    handle.succeed()
                else:
                    handle.abort()
                return action_type.Result(receipt_json=json.dumps(dict(**self.identity.identity(), **receipt)))
            if not state['feedback_fresh']:
                self.control.fail('simulation feedback stale; stop not confirmed')
            if action_type is MovePose:
                if state['state'].get('tcp_pose') is not None:
                    handle.publish_feedback(MovePose.Feedback(current_pose=self.pose(state['state']['tcp_pose']),
                        position_error=state.get('position_error', 0.), orientation_error=state.get('orientation_error', 0.),
                        phase=state['active_phase'] or 'finished'))
            else:
                handle.publish_feedback(action_type.Feedback(positions=state['state']['joint_positions']))
            time.sleep(.02)
        self.control.fail('driver shutdown; stop not confirmed')
        handle.abort()
        return action_type.Result(receipt_json=json.dumps(self.control.status(arm)['receipts'][gid]))

    def publish(self):
        for arm in ARMS:
            state = self.snapshot(arm)
            self.telemetry[arm].publish(String(data=json.dumps(state)))
            msg = JointState()
            stamp = state['reported_at_unix']
            msg.header.stamp.sec = int(stamp)
            msg.header.stamp.nanosec = int((stamp % 1)*1e9)
            msg.name = state['state']['joint_names']
            msg.position = state['state']['joint_positions']
            msg.velocity = state['state']['joint_velocities']
            self.joints[arm].publish(msg)
            if state['state'].get('tcp_pose') is not None:
                pose = PoseStamped()
                pose.header.stamp = msg.header.stamp
                pose.header.frame_id = state['state']['pose_frame']
                pose.pose = self.pose(state['state']['tcp_pose'])
                self.poses[arm].publish(pose)

    def camera(self, name, rgb, calibration, position, quaternion_xyzw):
        k = calibration['k']
        stamp = self.get_clock().now().to_msg()
        frame = self.identity.namespace.strip('/')+'/'+name+'/camera_optical_frame'
        rgb = rgb[:, :, :3].copy()
        msg = Image(height=rgb.shape[0], width=rgb.shape[1], encoding='rgb8',
                    is_bigendian=0, step=rgb.shape[1]*3, data=rgb.tobytes())
        msg.header.stamp, msg.header.frame_id = stamp, frame
        self.images[name].publish(msg)
        info = CameraInfo(height=msg.height, width=msg.width, distortion_model=calibration['distortion_model'], d=calibration['d'],
                          k=k, r=[1.,0.,0.,0.,1.,0.,0.,0.,1.],
                          p=[float(k[0]),0.,float(k[2]),0.,0.,float(k[4]),float(k[5]),0.,0.,0.,1.,0.])
        info.header = msg.header
        self.intrinsics[name].publish(info)
        self.transform(name+'/camera_optical_frame', position, quaternion_xyzw, stamp)

    def scanner_observation(self, state):
        common = dict(**self.identity.identity(), session_id=self.identity.session, observed_at_unix=time.time())
        self.scanner_state.publish(String(data=json.dumps(dict(**common, **state))))

    def transform(self, child, position, quaternion_xyzw, stamp=None):
        tf = TransformStamped()
        tf.header.stamp = stamp or self.get_clock().now().to_msg()
        tf.header.frame_id = self.identity.namespace.strip('/')+'/world'
        tf.child_frame_id = self.identity.namespace.strip('/')+'/'+child
        t, q = tf.transform.translation, tf.transform.rotation
        t.x, t.y, t.z = map(float, position)
        q.x, q.y, q.z, q.w = map(float, quaternion_xyzw)
        self.transforms.sendTransform(tf)
