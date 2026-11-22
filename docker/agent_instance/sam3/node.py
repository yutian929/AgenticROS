"""Public ROS perception capability; never imports robot or evaluator code."""
from collections import deque
import json
import os
from pathlib import Path
import threading
import time
import uuid

import numpy as np
from PIL import Image
import rclpy
from rclpy.action import ActionServer, CancelResponse, GoalResponse
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import MultiThreadedExecutor
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from rosidl_runtime_py.convert import message_to_ordereddict
from sensor_msgs.msg import Image as RosImage
from std_srvs.srv import Trigger
from agenticros_perception_interfaces.action import Segment
from agenticros_perception_interfaces.msg import Instance, Segmentation
from core import Backend, Records, RequestError, atomic_json, check_age, validate

ENDPOINT = '/perception/sam3'


def stamp_ns(header):
    return header.stamp.sec*1_000_000_000+header.stamp.nanosec


def pixels(message):
    channels = {'rgb8': 3, 'bgr8': 3, 'rgba8': 4, 'bgra8': 4, 'mono8': 1}
    count = channels.get(message.encoding)
    if count is None or not 0 < message.height*message.width <= 1920*1080:
        raise RequestError('INVALID_REQUEST', 'supported: rgb8/bgr8/rgba8/bgra8/mono8, at most 1920x1080 pixels')
    if message.step < message.width*count or len(message.data) != message.step*message.height:
        raise RequestError('INVALID_REQUEST', 'invalid image stride or data length')
    array = np.frombuffer(bytes(message.data), np.uint8).reshape(message.height, message.step)
    array = array[:, :message.width*count].reshape(message.height, message.width, count)
    if count == 1:
        array = np.repeat(array, 3, axis=2)
    else:
        array = array[:, :, :3]
        if message.encoding.startswith('bgr'):
            array = array[:, :, ::-1]
    return Image.fromarray(array.copy(), 'RGB')


class Perception(Node):
    def __init__(self, backend=None, records_root=None):
        super().__init__('sam3', namespace='/perception')
        self.session = uuid.uuid4().hex
        self.records = Records(records_root or os.environ.get('SAM3_RECORDS', '/records'), self.session)
        self.backend = backend if backend is not None else Backend()
        self.group = ReentrantCallbackGroup()
        self.lock = threading.RLock()
        self.pending = 0
        self.active = {}
        self.recent = deque(maxlen=64)
        self.stopping = threading.Event()
        self.publisher = self.create_publisher(Segmentation, ENDPOINT+'/results',
            QoSProfile(depth=4, reliability=ReliabilityPolicy.RELIABLE,
                       durability=DurabilityPolicy.TRANSIENT_LOCAL))
        self.status_service = self.create_service(Trigger, ENDPOINT+'/status', self.status, callback_group=self.group)
        self.action = ActionServer(self, Segment, ENDPOINT+'/segment', self.execute,
            goal_callback=self.goal, cancel_callback=lambda _: CancelResponse.ACCEPT,
            handle_accepted_callback=self.accepted, callback_group=self.group, result_timeout=600)

    def status(self, _, response):
        with self.lock:
            data = dict(session_id=self.session, **self.backend.status(),
                        active_goals=dict(self.active), recent_results=list(self.recent),
                        pending=self.pending, result_topic=ENDPOINT+'/results',
                        result_cache_depth=4, action_result_retention_sec=600)
        response.success = data['model_state'] == 'READY'
        response.message = json.dumps(data)
        return response

    def goal(self, request):
        data = message_to_ordereddict(request)
        try:
            validate(data, self.session)
            with self.lock:
                if self.backend.status()['model_state'] != 'READY' or self.stopping.is_set():
                    raise RequestError('NOT_READY', 'model is not ready')
                if self.pending >= 4:
                    raise RequestError('BUSY', 'at most four pending goals')
                self.pending += 1
            return GoalResponse.ACCEPT
        except RequestError as exc:
            # rclpy's goal callback exposes the request, not the protocol Goal UUID.
            key = 'rejected-'+uuid.uuid4().hex
            try:
                # ROS float fields can contain NaN; rejection logs must remain serializable.
                safe = json.loads(json.dumps(data), parse_constant=lambda value: value)
                self.records.create(key, safe)
                self.records.finish(key, dict(code=exc.code, message=str(exc), success=False))
            except Exception as error:
                self.get_logger().error('Failed to record rejected request: '+str(error))
            return GoalResponse.REJECT

    def accepted(self, handle):
        # Recording and UUID reservation happen inside execute, before any inference.
        handle.execute()

    def phase(self, handle, key, stage, start):
        with self.lock:
            self.active[key] = stage
        self.records.event(key, stage)
        handle.publish_feedback(Segment.Feedback(stage=stage, elapsed_sec=float(time.monotonic()-start)))

    def cancelled(self, handle):
        if handle.is_cancel_requested or self.stopping.is_set():
            raise RequestError('CANCELLED', 'cancelled; no model work remains for this request')

    def frame(self, handle, key, request, start):
        self.phase(handle, key, 'WAITING_FOR_IMAGE', start)
        frames = deque(maxlen=8)
        subscription = self.create_subscription(RosImage, request.image_topic, frames.append,
                                                 qos_profile_sensor_data, callback_group=self.group)
        deadline = time.monotonic()+request.image_timeout_sec
        desired = request.image_stamp.sec*1_000_000_000+request.image_stamp.nanosec
        try:
            while time.monotonic() < deadline:
                self.cancelled(handle)
                candidates = list(frames)
                if request.frame_policy == 'EXACT':
                    candidates = [frame for frame in candidates if stamp_ns(frame.header) == desired]
                if candidates:
                    frame = candidates[-1]
                    check_age(stamp_ns(frame.header), self.get_clock().now().nanoseconds, request.max_image_age_sec)
                    return frame
                time.sleep(.02)
            raise RequestError('FRAME_TIMEOUT', 'no matching frame received; EXACT never substitutes another stamp')
        finally:
            self.destroy_subscription(subscription)

    def execute(self, handle):
        key = uuid.UUID(bytes=bytes(handle.goal_id.uuid)).hex
        request = handle.request
        start = time.monotonic()
        result = Segment.Result(session_id=self.session, goal_id=key, image_topic=request.image_topic,
                                record_id=self.session+'/'+key, instances_json='[]', result_topic=ENDPOINT+'/results')
        folder = None
        try:
            folder = self.records.create(key, message_to_ordereddict(request))
            self.cancelled(handle)
            frame = self.frame(handle, key, request, start)
            result.source_header, result.width, result.height = frame.header, frame.width, frame.height
            pixels(frame).save(folder/'input.png')
            atomic_json(folder/'source.json', dict(image_topic=request.image_topic,
                header=message_to_ordereddict(frame.header), width=frame.width, height=frame.height,
                model=self.backend.metadata))
            self.phase(handle, key, 'QUEUED', start)
            while not self.backend.lock.acquire(timeout=.05):
                self.cancelled(handle)
            try:
                self.cancelled(handle)
                check_age(stamp_ns(frame.header), self.get_clock().now().nanoseconds, request.max_image_age_sec)
                if self.backend.status()['model_state'] != 'READY':
                    raise RequestError('INFERENCE_FAILED', 'model worker is unavailable')
                self.phase(handle, key, 'RUNNING', start)
                try:
                    instances = self.backend.infer(folder, dict(text_prompt=request.text_prompt,
                        score_threshold=request.score_threshold, max_instances=request.max_instances))
                except Exception as exc:
                    raise RequestError('INFERENCE_FAILED', str(exc)) from exc
            finally:
                self.backend.lock.release()
            # Cancellation during CUDA inference is acknowledged only after inference returns.
            self.cancelled(handle)
            message = Segmentation(header=frame.header, session_id=self.session, goal_id=key,
                image_topic=request.image_topic, width=frame.width, height=frame.height, source_image=frame)
            for item in instances:
                mask = Image.open(folder/'masks'/f"{item['instance_id']}.png").convert('L')
                if mask.size != (frame.width, frame.height):
                    raise RequestError('INFERENCE_FAILED', 'mask dimensions differ from source frame')
                msg = RosImage(header=frame.header, width=frame.width, height=frame.height,
                    encoding='mono8', is_bigendian=0, step=frame.width, data=mask.tobytes())
                message.instances.append(Instance(instance_id=item['instance_id'], score=float(item['score']),
                                                  box_xyxy=item['box_xyxy'], mask=msg))
            result.instances_json = json.dumps(instances, allow_nan=False)
            result.success, result.code = True, 'OK'
            # Persist successful processing before announcing results.
            self.records.finish(key, message_to_ordereddict(result))
            self.phase(handle, key, 'PUBLISHING', start)
            self.publisher.publish(message)
            handle.succeed()
        except Exception as exc:
            result.success = False
            result.code = exc.code if isinstance(exc, RequestError) else 'RECORDING_FAILED' if isinstance(exc, OSError) else 'INTERNAL_ERROR'
            result.message = str(exc)
            try:
                if folder is not None:
                    self.records.finish(key, message_to_ordereddict(result))
            except Exception as error:
                result.code, result.message = 'RECORDING_FAILED', str(error)
                self.get_logger().error('Evidence write failed: '+str(error))
            if handle.is_cancel_requested:
                handle.canceled()
            else:
                handle.abort()
        finally:
            with self.lock:
                self.pending -= 1
                self.active.pop(key, None)
                self.recent.append(dict(goal_id=key, code=result.code, success=result.success,
                                        record_id=result.record_id))
        return result


def main():
    rclpy.init()
    node = Perception()
    executor = MultiThreadedExecutor(num_threads=8)
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.stopping.set()
        node.backend.close()
        executor.shutdown(timeout_sec=10)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
