"""Verify the integrated ROS/RViz/noVNC observation stack without moving the robot."""
import json
from pathlib import Path
import subprocess
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], cwd=ROOT, check=True, text=True, **kwargs)


PROBE = r'''
import json, math, os, socket, time, xml.etree.ElementTree as ET
import numpy as np
import rclpy
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, qos_profile_sensor_data
from sensor_msgs.msg import Image, CameraInfo, JointState, PointCloud2
from std_msgs.msg import String
from std_srvs.srv import Trigger
from tf2_msgs.msg import TFMessage

# The raw VNC server is loopback-only and must require authentication.
def receive(client, count):
    data = b''
    while len(data) < count:
        block = client.recv(count-len(data))
        assert block
        data += block
    return data
with socket.create_connection(('127.0.0.1', 5900), timeout=3) as client:
    banner = receive(client, 12)
    assert banner.startswith(b'RFB 003.008')
    client.sendall(b'RFB 003.008\n')
    types = receive(client, receive(client, 1)[0])
    assert 2 in types and 1 not in types, 'VNC must require a password'

rclpy.init()
n = rclpy.create_node('rviz_stack_check')
s = n.create_client(Trigger, '/driver/status')
assert s.wait_for_service(timeout_sec=15)
def status():
    f = s.call_async(Trigger.Request())
    rclpy.spin_until_future_complete(n, f, timeout_sec=10)
    assert f.done()
    return json.loads(f.result().message)
before = status()
assert not before['busy'] and before['stop_confirmed']
messages, frames, transforms = {}, {}, {}
def callback(topic, msg):
    messages[topic] = msg
    if topic.startswith('/camera/'):
        stamp = (msg.header.stamp.sec, msg.header.stamp.nanosec)
        frames.setdefault(stamp, set()).add(topic)
    if isinstance(msg, TFMessage):
        for t in msg.transforms:
            transforms[t.child_frame_id] = t
subscriptions = []
for topic, kind in [('/camera/color/image_raw',Image), ('/camera/depth/image_raw',Image),
                    ('/camera/camera_info',CameraInfo), ('/camera/depth/color/points',PointCloud2),
                    ('/driver/joint_states',JointState), ('/robot_description',String),
                    ('/tf',TFMessage), ('/tf_static',TFMessage)]:
    qos = QoSProfile(depth=10, reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL) if topic in ('/robot_description','/tf_static') else qos_profile_sensor_data
    subscriptions.append(n.create_subscription(kind,topic,lambda msg,topic=topic:callback(topic,msg),qos))
deadline = time.monotonic()+15
while time.monotonic()<deadline:
    rclpy.spin_once(n,timeout_sec=.1)
    if len(messages)==8 and 'camera_optical_frame' in transforms and any(len(x)==4 for x in frames.values()):
        break
assert len(messages)==8, list(messages)
assert any(len(x)==4 for x in frames.values()), 'Camera images, cloud and calibration are not synchronized'
assert messages['/camera/color/image_raw'].encoding=='rgb8'
assert messages['/camera/depth/image_raw'].encoding=='32FC1'
cloud = messages['/camera/depth/color/points']
assert cloud.header.frame_id=='camera_optical_frame' and cloud.point_step==16
assert cloud.width>1000 and len(cloud.data)==cloud.width*16
assert [(f.name,f.offset,f.datatype) for f in cloud.fields]==[('x',0,7),('y',4,7),('z',8,7),('rgb',12,6)]
points = np.frombuffer(bytes(cloud.data),dtype=[('x','<f4'),('y','<f4'),('z','<f4'),('rgb','<u4')])
assert all(np.isfinite(points[axis]).all() for axis in ('x','y','z'))
assert np.all(points['z']>.01) and np.all(points['z']<3.)
model=ET.fromstring(messages['/robot_description'].data)
assert not any('block' in e.attrib.get('name','') or 'plate' in e.attrib.get('name','') for e in model.iter())
q=dict(zip(messages['/driver/joint_states'].name,messages['/driver/joint_states'].position))
assert math.isclose(transforms['carriage'].transform.translation.z,.2+q['z'],abs_tol=1e-6)
assert transforms['camera_optical_frame'].header.frame_id=='base'
assert any('rviz' in name for name in n.get_node_names()), 'RViz node missing'
after=status()
assert before['session_id']==after['session_id'] and before['state']['sim_time']==after['state']['sim_time']
assert not after['busy'] and after['stop_confirmed']
print(json.dumps({'rviz_ros_stack':'PASS','session_id':after['session_id'],'points':cloud.width,
                  'synchronized_rgbd':True,'vnc_authentication':'required','physics_advanced':False}))
n.destroy_node();rclpy.shutdown()
'''


def main():
    config = json.loads(docker('compose', '--profile', 'simulation', 'config', '--format', 'json', capture_output=True).stdout)
    assert set(config['services']) == {'cartesian_gripper', 'agenticros'}
    port = config['services']['cartesian_gripper']['ports'][0]
    assert port['host_ip'] == '127.0.0.1' and port['target'] == 8080
    base = 'http://127.0.0.1:' + str(port['published'])
    with urllib.request.urlopen(base + '/vnc.html', timeout=5) as response:
        assert b'noVNC' in response.read()
    for path in ('/evaluate', '/opt/body/world.py', '/snapshots/', '/api/state', '/api/cloud', '/viewer-auth/password'):
        try:
            urllib.request.urlopen(base + path, timeout=5)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError('Private path exposed: ' + path)
    container = docker('compose','ps','-q','cartesian_gripper',capture_output=True).stdout.strip()
    actual=json.loads(docker('inspect',container,capture_output=True).stdout)[0]
    assert actual['Config']['User']=='body'
    assert actual['HostConfig']['CapDrop']==['ALL'] and actual['HostConfig']['ReadonlyRootfs']
    assert {m['Destination'] for m in actual['Mounts']}=={'/observations','/viewer-auth'}
    assert all(m['Type']=='volume' for m in actual['Mounts'])
    docker('exec',container,'/ros_entrypoint.sh','/opt/body-venv/bin/python','-c',PROBE)
    docker('exec',container,'/opt/body-venv/bin/python','/opt/agenticros/healthcheck.py')
    print('PASS: RViz2, standard ROS observations and authenticated noVNC in cartesian_gripper')


if __name__=='__main__':
    main()
