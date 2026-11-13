"""Verify the ROS observer's real subscriptions, RGB-D/TF mapping and isolation."""
import json
import math
import subprocess
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

config = json.loads(subprocess.check_output(['docker', 'compose', '--profile', 'simulation',
                                            'config', '--format', 'json'], text=True))
base = 'http://127.0.0.1:' + str(config['services']['visualizer']['ports'][0]['published'])


def get(path):
    with urllib.request.urlopen(base + path, timeout=5) as response:
        return json.load(response)


state = get('/api/state')
assert get('/health')['ready']
assert state['node'] == 'ros_observer'
assert state['camera_age'] < 3
assert state['driver']['session_id'] and not state['driver']['busy'], 'Run with an idle simulator'
for topic in ('/camera/color/image_raw', '/camera/depth/image_raw', '/camera/camera_info',
              '/driver/joint_states', '/robot_description', '/tf', '/tf_static'):
    assert state['watched'][topic]['received'] > 0, topic
assert state['camera']['width'] == 640 and state['camera']['height'] == 480
robot = ET.fromstring(state['robot_description'])
assert robot.tag == 'robot'
assert not any('block' in e.attrib.get('name', '') or 'plate' in e.attrib.get('name', '') for e in robot.iter())
transforms = {t['child']: t for t in state['transforms']}
q = dict(zip(state['joints']['names'], state['joints']['positions']))
assert math.isclose(transforms['x_link']['translation'][0], q['x'], abs_tol=1e-6)
assert math.isclose(transforms['y_link']['translation'][1], q['y'], abs_tol=1e-6)
assert math.isclose(transforms['carriage']['translation'][2], .2 + q['z'], abs_tol=1e-6)
assert transforms['camera_optical_frame']['parent'] == 'base'
cloud = get('/api/cloud')
assert cloud['frame_id'] == 'camera_optical_frame' and cloud['count'] > 1000
assert len(cloud['positions']) == len(cloud['colors']) == cloud['count'] * 3
assert all(math.isfinite(x) for x in cloud['positions'])
assert all(0 <= c <= 255 for c in cloud['colors'])
assert not any(k in cloud for k in ('objects', 'object_positions', 'success'))
# Exercise dynamic topic inspection with an existing discovered topic.
request = urllib.request.Request(base + '/api/watch', data=json.dumps({'topic': '/rosout'}).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
# rosout is intentionally unsupported; the bridge must refuse unknown message types.
try:
    urllib.request.urlopen(request, timeout=5)
except urllib.error.HTTPError as exc:
    assert exc.code == 400
else:
    raise AssertionError('Unsupported type accepted')
for path in ('/opt/body/world.py', '/snapshots/', '/evaluate', '/assets/../robot.urdf'):
    try:
        urllib.request.urlopen(base + path, timeout=5)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError('Private or unlisted path exposed: ' + path)
container = subprocess.check_output(['docker', 'compose', '--profile', 'simulation', 'ps', '-q', 'visualizer'], text=True).strip()
actual = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
assert actual['Mounts'] == []
assert actual['HostConfig']['ReadonlyRootfs'] and actual['HostConfig']['CapDrop'] == ['ALL']
subprocess.run(['docker', 'exec', container, 'sh', '-c',
                'test ! -e /opt/body && test ! -e /observations && test ! -e /var/run/docker.sock'], check=True)
# A new ROS publisher must be discovered and inspectable without rebuilding the UI.
publisher_code = '''
import time, rclpy
from std_msgs.msg import String
rclpy.init()
n = rclpy.create_node('observer_subscription_test')
p = n.create_publisher(String, '/observer/test_text', 10)
end = time.monotonic() + 8
while time.monotonic() < end:
    p.publish(String(data='dynamic ROS topic received'))
    rclpy.spin_once(n, timeout_sec=.1)
n.destroy_node()
rclpy.shutdown()
'''
publisher = subprocess.Popen(['docker', 'exec', container, '/ros_entrypoint.sh', 'python3', '-c', publisher_code],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if any(t['name'] == '/observer/test_text' for t in get('/api/state')['topics']):
            break
        time.sleep(.2)
    request = urllib.request.Request(base + '/api/watch', data=b'{"topic":"/observer/test_text"}',
                                     headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=5) as response:
        assert json.load(response)['ok']
    for _ in range(15):
        record = get('/api/state')['watched']['/observer/test_text']
        if record['received']:
            break
        time.sleep(.2)
    assert record['summary'] == 'dynamic ROS topic received'
finally:
    assert publisher.wait(timeout=10) == 0
# Browsing the observer must not advance the simulation.
time.sleep(.6)
after = get('/api/state')
assert after['driver']['session_id'] == state['driver']['session_id']
assert after['driver']['state']['sim_time'] == state['driver']['state']['sim_time']
print(json.dumps({'observer': 'PASS', 'robot_tf': 'matches joint state', 'rgbd_points': cloud['count'],
                  'dynamic_subscription': 'PASS', 'source': 'ROS subscriptions only',
                  'private_mounts': 'none', 'physics_advanced': False}))
