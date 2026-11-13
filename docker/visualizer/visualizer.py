"""Read-only ROS subscriber and local browser transport, independent of MuJoCo."""
import io
import json
import mimetypes
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import numpy as np
from PIL import Image as PillowImage
import rclpy
from rclpy.node import Node
from rclpy.executors import MultiThreadedExecutor, ExternalShutdownException
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo, JointState
from std_msgs.msg import String
from tf2_msgs.msg import TFMessage
from sensors import decode_image, project_depth

ROOT = Path(__file__).resolve().parent
TYPES = {'sensor_msgs/msg/Image': Image, 'sensor_msgs/msg/CameraInfo': CameraInfo,
         'sensor_msgs/msg/JointState': JointState, 'tf2_msgs/msg/TFMessage': TFMessage,
         'std_msgs/msg/String': String}


def stamp(msg):
    return (msg.header.stamp.sec, msg.header.stamp.nanosec)


class Observer(Node):
    def __init__(self):
        super().__init__('ros_observer')
        self.lock = threading.RLock()
        self.transforms, self.watches, self.frames = {}, {}, {}
        self.description, self.joints, self.telemetry = '', {}, {}
        self.jpeg, self.jpeg_at = None, 0
        self.camera = {}
        self.graph = []
        self.started = time.monotonic()
        # Standard ROS remapping also works when using a different driver.
        defaults = [('/camera/color/image_raw', 'sensor_msgs/msg/Image'),
                    ('/camera/depth/image_raw', 'sensor_msgs/msg/Image'),
                    ('/camera/camera_info', 'sensor_msgs/msg/CameraInfo'),
                    ('/driver/joint_states', 'sensor_msgs/msg/JointState'),
                    ('/tf', 'tf2_msgs/msg/TFMessage'), ('/tf_static', 'tf2_msgs/msg/TFMessage'),
                    ('/robot_description', 'std_msgs/msg/String'), ('/driver/state', 'std_msgs/msg/String')]
        for topic, kind in defaults:
            self.watch(topic, kind)
        self.create_timer(1., self.refresh_graph)

    def refresh_graph(self):
        graph = [{'name': name, 'types': kinds, 'supported': any(k in TYPES for k in kinds)}
                 for name, kinds in sorted(self.get_topic_names_and_types())]
        with self.lock:
            self.graph = graph

    def watch(self, topic, kind=None):
        with self.lock:
            if topic in self.watches:
                return
            if len(self.watches) >= 32:
                raise ValueError('At most 32 topic subscriptions')
            if kind is None:
                matches = [g for g in self.graph if g['name'] == topic]
                if not matches or len(matches[0]['types']) != 1 or matches[0]['types'][0] not in TYPES:
                    raise ValueError('Select a discovered topic with a supported type')
                kind = matches[0]['types'][0]
            durable = topic in ('/tf_static', '/robot_description')
            qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                             durability=DurabilityPolicy.TRANSIENT_LOCAL) if durable else qos_profile_sensor_data
            record = {'type': kind, 'received': 0, 'at': None, 'summary': None, 'error': None}
            self.watches[topic] = record
            record['subscription'] = self.create_subscription(TYPES[kind], topic,
                lambda msg, topic=topic: self.receive(topic, msg), qos)
        if self.executor:
            self.executor.wake()

    def receive(self, topic, msg):
        now = time.monotonic()
        try:
            with self.lock:
                record = self.watches[topic]
                record['received'] += 1
                record['at'], record['error'] = now, None
                if isinstance(msg, Image):
                    record['summary'] = dict(width=msg.width, height=msg.height, encoding=msg.encoding,
                                             frame_id=msg.header.frame_id, stamp=stamp(msg))
                    if topic not in ('/camera/color/image_raw', '/camera/depth/image_raw'):
                        return
                    pixels = decode_image(msg)
                    key = stamp(msg)
                    frame = self.frames.setdefault(key, {'at': now})
                    frame['frame_id'] = msg.header.frame_id
                    if topic == '/camera/color/image_raw':
                        if msg.encoding not in ('rgb8', 'bgr8'):
                            raise ValueError('Color layer requires rgb8/bgr8')
                        frame['rgb'] = pixels
                        output = io.BytesIO()
                        PillowImage.fromarray(pixels).save(output, format='JPEG', quality=85)
                        self.jpeg, self.jpeg_at = output.getvalue(), now
                    else:
                        if msg.encoding not in ('32FC1', '16UC1'):
                            raise ValueError('Depth layer requires 32FC1 metres or 16UC1 millimetres')
                        frame['depth'] = pixels.astype(np.float32) * (.001 if msg.encoding == '16UC1' else 1.)
                    self.trim_frames()
                elif isinstance(msg, CameraInfo):
                    record['summary'] = dict(width=msg.width, height=msg.height, k=list(msg.k),
                                             frame_id=msg.header.frame_id, stamp=stamp(msg))
                    if topic == '/camera/camera_info':
                        self.camera = record['summary']
                        self.frames.setdefault(stamp(msg), {'at': now})['info'] = record['summary']
                        self.trim_frames()
                elif isinstance(msg, JointState):
                    record['summary'] = dict(names=list(msg.name), positions=list(msg.position),
                                             velocities=list(msg.velocity), stamp=stamp(msg))
                    if topic == '/driver/joint_states':
                        self.joints = record['summary']
                elif isinstance(msg, TFMessage):
                    record['summary'] = []
                    for t in msg.transforms:
                        v, q = t.transform.translation, t.transform.rotation
                        value = dict(parent=t.header.frame_id, child=t.child_frame_id,
                                     translation=[v.x, v.y, v.z], rotation=[q.x, q.y, q.z, q.w],
                                     static=topic == '/tf_static', at=now)
                        record['summary'].append({k: v for k, v in value.items() if k != 'at'})
                        if topic in ('/tf', '/tf_static'):
                            self.transforms[t.child_frame_id] = value
                elif isinstance(msg, String):
                    record['summary'] = msg.data[:2000]
                    if topic == '/robot_description':
                        self.description = msg.data
                    elif topic == '/driver/state':
                        self.telemetry = json.loads(msg.data)
        except (ValueError, KeyError, TypeError) as exc:
            with self.lock:
                self.watches[topic]['error'] = str(exc)

    def trim_frames(self):
        for key in sorted(self.frames)[:-5]:
            del self.frames[key]

    def state(self):
        now = time.monotonic()
        with self.lock:
            watched = {topic: {**{k: v for k, v in record.items() if k not in ('subscription', 'at')},
                               'age': None if record['at'] is None else round(now-record['at'], 2)}
                       for topic, record in self.watches.items()}
            transforms = [{**{k: v for k, v in t.items() if k != 'at'}, 'age': round(now-t['at'], 2)}
                          for t in self.transforms.values()]
            return dict(node=self.get_name(), fixed_frame='base', topics=self.graph, watched=watched,
                        robot_description=self.description, transforms=transforms,
                        joints=self.joints, driver=self.telemetry, camera=self.camera,
                        camera_age=None if self.jpeg is None else round(now-self.jpeg_at, 2))

    def cloud(self):
        with self.lock:
            complete = [(key, f) for key, f in self.frames.items() if all(k in f for k in ('rgb', 'depth', 'info'))]
            if not complete:
                raise ValueError('Waiting for synchronized RGB, depth and CameraInfo')
            key, frame = max(complete)
            if time.monotonic()-frame['at'] > 3:
                raise ValueError('RGB-D stream is stale')
            info = frame['info']
            if frame['frame_id'] != info['frame_id']:
                raise ValueError('Camera frame mismatch')
            points, colors = project_depth(frame['depth'], frame['rgb'], info['k'])
            return dict(stamp=key, frame_id=frame['frame_id'], count=len(points),
                        positions=np.round(points, 5).ravel().tolist(), colors=colors.ravel().tolist())


def handler(observer):
    class Viewer(BaseHTTPRequestHandler):
        def send(self, body, content_type='application/json', status=200):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == '/api/state':
                    self.send(json.dumps(observer.state(), allow_nan=False).encode())
                elif path == '/api/cloud':
                    self.send(json.dumps(observer.cloud(), allow_nan=False).encode())
                elif path == '/frame.jpg':
                    with observer.lock:
                        jpeg, age = observer.jpeg, time.monotonic()-observer.jpeg_at
                    if jpeg is None or age > 3:
                        raise ValueError('Camera topic has no recent data')
                    self.send(jpeg, 'image/jpeg')
                elif path == '/health':
                    state = observer.state()
                    ready = state['camera_age'] is not None and state['camera_age'] < 3 and bool(state['robot_description'])
                    self.send(json.dumps({'ready': ready}).encode(), status=200 if ready else 503)
                elif path in ('/', '/app.js', '/style.css', '/assets/three.module.js',
                              '/assets/three.core.js', '/assets/OrbitControls.js', '/assets/LICENSE.three'):
                    file = ROOT / ('index.html' if path == '/' else path.lstrip('/'))
                    kind = 'text/javascript' if file.suffix == '.js' else mimetypes.guess_type(str(file))[0] or 'text/plain'
                    self.send(file.read_bytes(), kind)
                else:
                    self.send_error(404)
            except (ValueError, KeyError) as exc:
                self.send(json.dumps({'error': str(exc)}).encode(), status=503)

        def do_POST(self):
            if self.path != '/api/watch':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 2048:
                    raise ValueError('Invalid request size')
                body = json.loads(self.rfile.read(length))
                observer.watch(body['topic'])
                self.send(b'{"ok":true}')
            except (ValueError, KeyError, TypeError) as exc:
                self.send(json.dumps({'error': str(exc)}).encode(), status=400)

        def log_message(self, *_):
            pass
    return Viewer


def main():
    rclpy.init()
    observer = Observer()
    # This node publishes only the public robot model/kinematics, never scene objects.
    publisher = subprocess.Popen(['ros2', 'run', 'robot_state_publisher', 'robot_state_publisher',
        '--ros-args', '-p', 'robot_description:=' + (ROOT/'robot.urdf').read_text(),
        '-r', 'joint_states:=/driver/joint_states'])
    server = ThreadingHTTPServer(('0.0.0.0', int(os.getenv('VIEWER_PORT', '8080'))), handler(observer))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    executor = MultiThreadedExecutor(num_threads=3)
    executor.add_node(observer)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        server.shutdown()
        publisher.send_signal(signal.SIGINT)
        try:
            publisher.wait(timeout=8)
        except subprocess.TimeoutExpired:
            publisher.terminate()
            publisher.wait(timeout=3)
        executor.shutdown()
        observer.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
