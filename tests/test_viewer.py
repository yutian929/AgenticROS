"""Viewer discovery, session isolation and launch configuration; no robot commands."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


discovery = load('viewer_discovery', 'docker/agent_instance/viewer/discovery.py')
launcher = load('viewer_launcher', 'scripts/lan.py')


class Discovery(unittest.TestCase):
    def test_all_camera_views_are_enabled_inside_rviz(self):
        topics = [('/robot/'+side+'/camera/color/image_raw/compressed',
                   ['sensor_msgs/msg/CompressedImage']) for side in ('left', 'overhead', 'right')]
        view = discovery.configuration(topics, 'robot/base')
        images = [d for d in view['Visualization Manager']['Displays']
                  if d['Class'] == 'rviz_default_plugins/Image']
        self.assertEqual(len(images), 3)
        self.assertTrue(all(d['Enabled'] for d in images))
        self.assertEqual(view['Window Geometry']['Y'], 140)

    def test_roster_rejects_wrong_instance_and_tracks_live_sessions(self):
        namespace = '/robots/robot_a/s_'+'a'*12
        robot = dict(robot_id='robot_a', session_id='a'*12, namespace=namespace, connected=True)
        roster = dict(instance_id='instance-a', robots=[robot])
        self.assertEqual(discovery.robots(roster, 'instance-a'), {'robot_a':namespace})
        with self.assertRaises(ValueError):
            discovery.robots(roster, 'instance-b')
        robot['connected'] = False
        self.assertEqual(discovery.robots(roster, 'instance-a'), {})
        robot.update(connected=True, session_id='b'*12)
        with self.assertRaises(ValueError):
            discovery.robots(roster, 'instance-a')

    def test_frame_selection_never_connects_unrelated_robots(self):
        a, b = '/robots/robot_a/s_aaaaaaaaaaaa', '/robots/robot_b/s_bbbbbbbbbbbb'
        edges = [(a[1:]+'/base',a[1:]+'/camera'),(b[1:]+'/base',b[1:]+'/camera')]
        self.assertEqual(discovery.fixed_frame(a, edges, []), a[1:]+'/base')
        self.assertEqual(discovery.fixed_frame(b, [], [a[1:]+'/camera', b[1:]+'/camera']), b[1:]+'/camera')
        self.assertEqual(discovery.fixed_frame(b, edges[:1], [a[1:]+'/camera']), '')

    def test_views_use_discovered_sensor_topics_not_device_specific_paths(self):
        ns = '/robots/robot_a/s_aaaaaaaaaaaa'
        topics = [(ns+'/rgb_front', ['sensor_msgs/msg/Image']),
                  (ns+'/cloud', ['sensor_msgs/msg/PointCloud2']),
                  (ns+'/robot_description', ['std_msgs/msg/String']),
                  (ns+'/custom_status', ['custom/msg/State']),
                  (ns+'old/rgb', ['sensor_msgs/msg/Image'])]
        scoped = discovery.scoped_topics(topics, ns)
        self.assertEqual(len(scoped), 4)
        view = discovery.configuration(scoped, ns[1:]+'/base')
        manager = view['Visualization Manager']
        self.assertEqual(manager['Global Options']['Frame Rate'], 10)
        self.assertEqual(manager['Global Options']['Fixed Frame'], ns[1:]+'/base')
        display = {d['Class']:d for d in manager['Displays']}
        self.assertEqual(display['rviz_default_plugins/Image']['Topic']['Value'], ns+'/rgb_front')
        self.assertEqual(display['rviz_default_plugins/PointCloud2']['Topic']['Depth'], 1)
        self.assertIn('rviz_default_plugins/RobotModel', display)
        self.assertNotIn('custom_status', json.dumps(view))
        view = discovery.configuration(topics[:1]+[(ns+'/rgb_front/compressed', ['sensor_msgs/msg/CompressedImage']),
                                       (ns+'/depth', ['sensor_msgs/msg/Image'])], ns[1:]+'/base')
        images = [d for d in view['Visualization Manager']['Displays'] if d['Class']=='rviz_default_plugins/Image']
        self.assertEqual(sum(d['Enabled'] for d in images), 1)
        self.assertTrue(next(d for d in images if d['Enabled'])['Topic']['Value'].endswith('/compressed'))


class Configuration(unittest.TestCase):
    def test_viewers_have_distinct_ports_and_preserve_server_identity(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(launcher, 'ROOT', Path(tmp)):
            a = launcher.server_config('instance-a', '127.0.0.1')
            b = launcher.server_config('instance-b', '127.0.0.1')
            original = launcher.server_file('instance-a').read_bytes()
            first, second = launcher.viewer_config(a), launcher.viewer_config(b)
            self.assertNotEqual(first['AGENTICROS_VIEWER_PORT'], second['AGENTICROS_VIEWER_PORT'])
            self.assertNotEqual(first['AGENTICROS_VNC_PORT'], second['AGENTICROS_VNC_PORT'])
            self.assertEqual(launcher.viewer_config(a), first)
            self.assertEqual(launcher.server_file('instance-a').read_bytes(), original)
            self.assertEqual(first['AGENTICROS_VIEWER_BIND'], '127.0.0.1')

    def test_viewer_restart_is_a_process_command_within_existing_instance(self):
        config = dict(AGENTICROS_INSTANCE='example')
        with patch.object(launcher, 'viewer_config', return_value=config), patch.object(launcher, 'ensure_server'), patch.object(launcher, 'run') as run:
            launcher.ensure_viewer(config)
            run.assert_called_once_with(config, ['exec','-T','instance','python3','/opt/instance/runtime.py','viewer-start'])

    def test_combined_instance_only_mounts_dedicated_data_and_generic_config(self):
        config = dict(AGENTICROS_INSTANCE='test-viewer', AGENTICROS_INSTANCE_ID='i',
                      AGENTICROS_DDS_HOST='192.168.1.10', AGENTICROS_REGISTRY_PORT=17447,
                      AGENTICROS_DDS_PORT=12811, ROS_DOMAIN_ID=73,
                      AGENTICROS_DDS_PREFIX='01.02.03.04.05.06.07.08.09.0a.0b.0c', AGENTICROS_JOIN_TOKEN='secret')
        data = json.loads(subprocess.check_output(launcher.compose(config)+['config','--format','json'],
                          cwd=ROOT, env=launcher.environment(config), text=True))
        self.assertEqual(set(data['services']), {'instance'})
        instance = data['services']['instance']
        self.assertEqual(instance['network_mode'], 'host')
        self.assertEqual(instance['environment']['AGENTICROS_REGISTRY_URL'], 'http://127.0.0.1:17447')
        self.assertEqual(instance['environment']['ROS_DOMAIN_ID'], '73')
        self.assertEqual({m['target'] for m in instance['volumes']},
                         {'/viewer-auth', '/workspace', '/home/agent/.codex', '/config/codex.toml'})
        self.assertEqual(data['volumes']['viewer-auth']['name'], 'agenticros_viewer-auth-instance-test-viewer')
        dockerfile = (ROOT/'docker/agent_instance/Dockerfile').read_text()
        for forbidden in ('docker/robot_instances', 'mujoco', 'driver_ws', 'evaluator'):
            self.assertNotIn(forbidden, dockerfile)

    def test_lan_camera_publication_does_not_use_synchronous_mode(self):
        config = dict(AGENTICROS_INSTANCE='test-viewer', AGENTICROS_ROBOT_ID='robot_a',
                      AGENTICROS_SERVER='http://127.0.0.1:7447', AGENTICROS_CLIENT_HOST='127.0.0.1',
                      AGENTICROS_JOIN_TOKEN='secret', AGENTICROS_OBSERVATION_PORT=8800)
        data = json.loads(subprocess.check_output(launcher.compose(config, True)+['config','--format','json'],
                          cwd=ROOT, env=launcher.environment(config), text=True))
        self.assertEqual(data['services']['client']['environment']['RMW_FASTRTPS_PUBLICATION_MODE'], 'ASYNCHRONOUS')


if __name__ == '__main__':
    unittest.main()
