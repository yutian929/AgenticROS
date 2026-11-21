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


launcher = load('viewer_launcher', 'scripts/runtime.py')


class Configuration(unittest.TestCase):
    def test_server_only_mounts_dedicated_data_and_generic_config(self):
        config = dict(ROS_DOMAIN_ID=73)
        data = json.loads(subprocess.check_output(launcher.compose(config)+['config','--format','json'],
                          cwd=ROOT, env=launcher.environment(config), text=True))
        self.assertEqual(set(data['services']), {'server'})
        instance = data['services']['server']
        self.assertEqual(instance['network_mode'], 'host')
        self.assertNotIn('AGENTICROS_REGISTRY_URL',instance['environment'])
        self.assertEqual(instance['environment']['ROS_DOMAIN_ID'], '73')
        self.assertEqual({m['target'] for m in instance['volumes']},
                         {'/workspace', '/workspace/robot_docs', '/home/agent/.codex', '/config/codex.toml'})
        dockerfile = (ROOT/'docker/agent_instance/agent/Dockerfile').read_text()
        for forbidden in ('docker/robot_instances', 'mujoco', 'driver_ws', 'evaluator'):
            self.assertNotIn(forbidden, dockerfile)

    def test_viewer_has_independent_service_and_only_auth_volume(self):
        data=json.loads(subprocess.check_output(['docker','compose','-f','compose.agent.yaml','--profile','viewer','config','--format','json'],cwd=ROOT,env=launcher.environment({'ROS_DOMAIN_ID':73}),text=True))
        viewer=data['services']['viewer']
        self.assertEqual(viewer['environment']['ROS_DOMAIN_ID'],'73')
        self.assertEqual({m['target'] for m in viewer['volumes']},{'/viewer-auth'})
        self.assertNotIn('depends_on',viewer)
        self.assertNotEqual(viewer['image'],data['services']['server']['image'])
        self.assertEqual(data['volumes']['viewer-auth']['name'],'agenticros-73_viewer-auth')

    def test_lan_camera_publication_does_not_use_synchronous_mode(self):
        config = dict(ROS_DOMAIN_ID=42, AGENTICROS_ROBOT_ID='robot_a',AGENTICROS_ENVIRONMENT='cartesian_gripper')
        data = json.loads(subprocess.check_output(launcher.compose(config, True)+['config','--format','json'],
                          cwd=ROOT, env=launcher.environment(config), text=True))
        self.assertEqual(data['services']['client']['environment']['RMW_FASTRTPS_PUBLICATION_MODE'], 'ASYNCHRONOUS')


if __name__ == '__main__':
    unittest.main()
