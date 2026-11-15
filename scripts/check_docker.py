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


raw_probe = r'''
import json, pathlib, time, uuid
import rclpy
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from unique_identifier_msgs.msg import UUID
from agenticros_driver_interfaces.action import Actuate
from agenticros_driver_interfaces.srv import Snapshot
assert not pathlib.Path('/opt/body').exists(), 'private body source leaked'
assert not pathlib.Path('/var/run/docker.sock').exists(), 'Docker socket leaked'
assert not pathlib.Path('/viewer-auth').exists(), 'Viewer credentials leaked'
for root in ('/opt', '/workspace', '/home/agent'):
    for filename in ('tabletop.xml', 'world.py', 'evaluator.py', 'client.py'):
        # Python dependencies can have client.py; private adapter packages are forbidden.
        if filename != 'client.py':
            assert not list(pathlib.Path(root).rglob(filename)), (root, filename)
import importlib.util
for package in __PRIVATE_PACKAGES__:
    assert importlib.util.find_spec(package) is None, 'private adapter package leaked: ' + package
    assert not pathlib.Path('/tmp/' + package + '-evaluator.sock').exists(), 'private evaluator socket leaked'
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
# Preserve old-namespace isolation checks without using old names as runtime configuration.
fingerprints = json.loads((Path(__file__).resolve().parents[1] / 'tests/forbidden_sources.json').read_text())
private_packages = {'agenticros'} | {Path(path).parts[0] for path in fingerprints.values()
                                    if path.endswith('/__init__.py')}
raw_probe = raw_probe.replace('__PRIVATE_PACKAGES__', repr(sorted(private_packages)))
if '--motion' in sys.argv:
    raw_probe += '\n' + (Path(__file__).resolve().parent / 'check_raw_driver.py').read_text()
# Maintainer-only regression probe: rebuild the interface from live ROS parameters.
# This code is streamed into a temporary directory; it is not installed in the Agent image.
bootstrap = r'''
import importlib.util, json, pathlib, subprocess, tempfile, uuid
import rclpy
from rcl_interfaces.srv import ListParameters, GetParameters
assert importlib.util.find_spec('agenticros_driver_interfaces') is None, 'device types preinstalled or sourced'
assert not pathlib.Path('/opt/driver_ws').exists()
assert not pathlib.Path('/opt/agenticros/body.md').exists()
rclpy.init()
n = rclpy.create_node('interface_discovery_check_' + uuid.uuid4().hex[:8])
def wait(f):
    rclpy.spin_until_future_complete(n, f, timeout_sec=15)
    assert f.done(), 'metadata response timeout'
    return f.result()
# Graph discovery must work before any custom binding is available.
from rclpy.action.graph import get_action_names_and_types
for _ in range(100):
    actions = dict(get_action_names_and_types(n))
    if '/driver/actuate' in actions:
        break
    rclpy.spin_once(n, timeout_sec=.1)
assert actions.get('/driver/actuate') == ['agenticros_driver_interfaces/action/Actuate']
# Find metadata through standard services; no custom binding is needed.
metadata = None
for _ in range(100):
    nodes = n.get_node_names_and_namespaces()
    for name, namespace in nodes:
        prefix = namespace.rstrip('/') + '/' + name
        listing = n.create_client(ListParameters, prefix + '/list_parameters')
        if not listing.wait_for_service(timeout_sec=.1):
            n.destroy_client(listing)
            continue
        names = wait(listing.call_async(ListParameters.Request())).result.names
        n.destroy_client(listing)
        if 'interface_sources' not in names or 'device_documentation' not in names:
            continue
        reader = n.create_client(GetParameters, prefix + '/get_parameters')
        assert reader.wait_for_service(timeout_sec=5)
        values = wait(reader.call_async(GetParameters.Request(names=['interface_sources', 'device_documentation']))).values
        metadata = json.loads(values[0].string_value)
        assert '/driver/actuate' in values[1].string_value
        n.destroy_client(reader)
        break
    if metadata is not None:
        break
    rclpy.spin_once(n, timeout_sec=.1)
assert metadata is not None, 'public device interfaces not discoverable'
n.destroy_node(); rclpy.shutdown()
assert metadata['package'] == 'agenticros_driver_interfaces'
assert set(metadata['files']) == {'package.xml', 'CMakeLists.txt', 'action/Actuate.action', 'srv/Snapshot.srv'}
with tempfile.TemporaryDirectory(prefix='ros-discovery-') as directory:
    root = pathlib.Path(directory)
    package = root / 'src' / metadata['package']
    for name, content in metadata['files'].items():
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    subprocess.run(['colcon', 'build', '--executor', 'sequential'], cwd=root, check=True)
    import shlex
    command = 'source ' + shlex.quote(str(root / 'install/setup.bash')) + ' && python -c ' + shlex.quote(__RAW_PROBE__)
    subprocess.run(['bash', '-c', command], check=True)
'''
probe = bootstrap.replace('__RAW_PROBE__', repr(raw_probe))

config = json.loads(run('config', '--format', 'json', capture_output=True).stdout)
agent = config['services']['agenticros']
assert not agent.get('privileged', False)
for mount in agent.get('volumes', []):
    assert mount['target'] in ('/workspace', '/observations', '/home/agent/.codex', '/config/codex.toml')
    if mount['type'] == 'bind':
        assert mount['target'] in ('/config/codex.toml', '/workspace')
        if mount['target'] == '/config/codex.toml':
            assert mount.get('read_only')
workspace = next(m for m in agent['volumes'] if m['target'] == '/workspace')
assert workspace['type'] == 'bind' and not workspace.get('read_only')
# Inspect a real created container, including resolved mount paths and network settings.
name = 'agenticros-isolation-' + uuid.uuid4().hex[:8]
try:
    run('run', '-d', '--no-deps', '--name', name, 'agenticros', 'sleep', '600')
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
            assert mount['Destination'] in ('/config/codex.toml', '/workspace')
        if mount['Destination'] == '/workspace':
            assert mount['Type'] == 'bind' and mount['RW']
            assert mount['Source'] == workspace['source']
    image = json.loads(subprocess.check_output(['docker', 'image', 'inspect', actual['Image']], text=True))[0]
    assert image['Architecture'] in ('amd64', 'arm64'), image['Architecture']
    subprocess.run(['docker', 'exec', name, '/usr/local/bin/agent-entrypoint',
                    'python', '-c', probe], check=True)
    print(json.dumps({'image': actual['Image'], 'architecture': image['Architecture'],
                      'mounts': [{k: m[k] for k in ('Type', 'Destination', 'RW')} for m in actual['Mounts']]}))
finally:
    subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
with urllib.request.urlopen('http://127.0.0.1:' + str(config['services']['simple_mujoco']['ports'][0]['published']) + '/vnc.html') as response:
    assert b'noVNC' in response.read()
for path in ('/evaluate', '/opt/body/world.py', '/snapshots/', '/api/state', '/api/cloud', '/viewer-auth/password'):
    try:
        urllib.request.urlopen('http://127.0.0.1:' + str(config['services']['simple_mujoco']['ports'][0]['published']) + path)
    except urllib.error.HTTPError as exc:
        assert exc.code == 404
    else:
        raise AssertionError('private HTTP path exposed: ' + path)
print('PASS: image/mount isolation and ROS smoke check; raw motion=' + str('--motion' in sys.argv))
