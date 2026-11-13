"""Host-run Docker integration smoke test. Does not install any adapter."""
import json
import subprocess
import uuid
import urllib.request
import urllib.error
import sys
from pathlib import Path


def run(*args, **kwargs):
    return subprocess.run(['docker', 'compose', '--profile', 'simulation', *args], check=True, text=True, **kwargs)


probe = r'''
import json, pathlib, time, uuid
import rclpy
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from unique_identifier_msgs.msg import UUID
from rosbody_driver_interfaces.action import Actuate
from rosbody_driver_interfaces.srv import Snapshot
assert not pathlib.Path('/opt/body').exists(), 'private body source leaked'
assert not pathlib.Path('/var/run/docker.sock').exists(), 'Docker socket leaked'
assert not pathlib.Path('/tmp/rosbody-evaluator.sock').exists()
for root in ('/opt', '/workspace', '/home/agent'):
    for filename in ('tabletop.xml', 'world.py', 'evaluator.py', 'client.py'):
        # Python dependencies can have client.py; only the legacy rosbody package is forbidden.
        if filename != 'client.py':
            assert not list(pathlib.Path(root).rglob(filename)), (root, filename)
import importlib.util
assert importlib.util.find_spec('rosbody') is None, 'legacy adapter package leaked'
rclpy.init()
n = rclpy.create_node('docker_integration_check_' + uuid.uuid4().hex[:8])
def wait(f):
    rclpy.spin_until_future_complete(n, f, timeout_sec=15)
    assert f.done(), 'ROS response timeout'
    return f.result()
s = n.create_client(Trigger, '/driver/status')
assert s.wait_for_service(timeout_sec=15), 'raw driver undiscovered'
before = json.loads(wait(s.call_async(Trigger.Request())).message)
assert not before['busy'], 'body busy; do not test during an agent task'
assert not before['recovery_required']
a = ActionClient(n, Actuate, '/driver/actuate')
assert a.wait_for_server(timeout_sec=15)
# Invalid goal must be rejected, without executing movement.
goal = Actuate.Goal(session_id=before['session_id'], positions=[999.,0.,0.,0.,0.], duration=1.)
h = wait(a.send_goal_async(goal, goal_uuid=UUID(uuid=list(uuid.uuid4().bytes))))
assert not h.accepted
for positions, duration in [([float('nan'), 0., 0., 0., 0.], 1.),
                            ([0., 0., 0., 0., 0.], 0.),
                            ([0., 0., 0., 0., 0.], float('inf'))]:
    invalid = Actuate.Goal(session_id=before['session_id'], positions=positions, duration=duration)
    assert not wait(a.send_goal_async(invalid)).accepted
stale = Actuate.Goal(session_id='stale-session', positions=[0., -.25, .08, .04, .04], duration=1.)
assert not wait(a.send_goal_async(stale)).accepted
obs = n.create_client(Snapshot, '/driver/observe')
assert obs.wait_for_service(timeout_sec=15)
snapshot = json.loads(wait(obs.call_async(Snapshot.Request())).snapshot_json)
assert snapshot['session_id'] == before['session_id']
assert pathlib.Path(snapshot['rgb_path']).is_file()
assert pathlib.Path(snapshot['depth_path']).is_file()
try:
    pathlib.Path('/observations/should-not-write').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('observations mount writable')
after = json.loads(wait(s.call_async(Trigger.Request())).message)
assert before['session_id'] == after['session_id']
assert before['state']['sim_time'] == after['state']['sim_time'], 'invalid goal advanced physics'
assert before['receipts'] == after['receipts']
assert not n.get_service_names_and_types() == []
assert not any('evaluat' in name for name, _ in n.get_service_names_and_types())
print(json.dumps({'raw_ros': 'ok', 'snapshot': snapshot['snapshot_id'],
                  'invalid_goal': 'rejected without stepping', 'observations': 'read-only'}))
n.destroy_node(); rclpy.shutdown()
'''
config = json.loads(run('config', '--format', 'json', capture_output=True).stdout)
agent = config['services']['agenticros']
assert not agent.get('privileged', False)
for mount in agent.get('volumes', []):
    assert mount['target'] in ('/workspace', '/observations', '/home/agent/.codex', '/config/codex.toml')
    if mount['type'] == 'bind':
        assert mount['target'] == '/config/codex.toml' and mount.get('read_only')
# Inspect a real created container, including resolved mount paths and network settings.
name = 'rosbody-isolation-' + uuid.uuid4().hex[:8]
try:
    run('run', '-d', '--no-deps', '--name', name, 'agenticros', 'sleep', '180')
    actual = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
    assert actual['Config']['User'] == 'agent'
    assert not actual['HostConfig']['Privileged']
    assert actual['HostConfig']['CapDrop'] == ['ALL']
    assert actual['HostConfig']['NetworkMode'] not in ('host', 'container:body')
    assert {m['Destination'] for m in actual['Mounts']} == {
        '/workspace', '/observations', '/home/agent/.codex', '/config/codex.toml'}
    for mount in actual['Mounts']:
        if mount['Destination'] in ('/observations', '/config/codex.toml'):
            assert not mount['RW']
        if mount['Type'] == 'bind':
            assert mount['Destination'] == '/config/codex.toml'
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', actual['Image']], text=True))[0]
    assert image['Architecture'] == 'arm64'
    subprocess.run(['docker', 'exec', name, '/usr/local/bin/agent-entrypoint',
                    'python', '-c', probe], check=True)
    if '--motion' in sys.argv:
        subprocess.run(['docker', 'exec', '-i', name, '/usr/local/bin/agent-entrypoint',
                        'python', '-'], input=Path('scripts/check_raw_driver.py').read_text(),
                       text=True, check=True)
    print(json.dumps({'image': actual['Image'], 'architecture': image['Architecture'],
                      'mounts': [{k: m[k] for k in ('Type', 'Destination', 'RW')} for m in actual['Mounts']]}))
finally:
    subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
with urllib.request.urlopen('http://127.0.0.1:' + str(config['services']['simple_mujoco']['ports'][0]['published']) + '/frame.jpg') as response:
    assert response.headers['Content-Type'] == 'image/jpeg'
    assert response.read().startswith(b'\xff\xd8')
for path in ('/evaluate', '/opt/body/world.py', '/snapshots/'):
    try:
        urllib.request.urlopen('http://127.0.0.1:' + str(config['services']['simple_mujoco']['ports'][0]['published']) + path)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError('private HTTP path exposed: ' + path)
print('PASS: image/mount isolation and ROS smoke check; raw motion=' + str('--motion' in sys.argv))
