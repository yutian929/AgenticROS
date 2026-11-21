"""Host-side layout checks; standard library and Docker CLI only."""
import ast
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerLayout(unittest.TestCase):
    def test_single_server_boundary(self):
        import os
        config = json.loads(subprocess.check_output(['docker','compose','-f','compose.agent.yaml','--env-file','/dev/null','config','--format','json'],
            cwd=ROOT,env=dict(os.environ,ROS_DOMAIN_ID='42'),text=True))
        self.assertEqual(set(config['services']), {'server'})
        server=config['services']['server']
        self.assertEqual(server['network_mode'],'host')
        self.assertEqual(server['cap_drop'],['ALL'])
        self.assertNotIn('container_name',server)
        mounts={m['target']:m for m in server['volumes']}
        self.assertEqual(set(mounts),{'/workspace','/workspace/robot_docs','/home/agent/.codex','/config/codex.toml'})
        self.assertEqual(Path(mounts['/workspace']['source']),ROOT/'runs/42/agent_instance/agent/workspace')
        self.assertTrue(mounts['/config/codex.toml']['read_only'])
        self.assertTrue(mounts['/workspace/robot_docs']['read_only'])
        self.assertEqual(Path(mounts['/workspace/robot_docs']['source']),ROOT/'docker/robot_instances/robot_docs')
        self.assertTrue(mounts['/workspace']['bind'].get('create_host_path', True))
        self.assertEqual(config['volumes']['codex-home']['name'],'agenticros-42_codex-home')
        self.assertEqual(server['environment']['ROS_DOMAIN_ID'],'42')
        self.assertNotIn('AGENTICROS_JOIN_TOKEN',server['environment'])
        self.assertNotIn('AGENTICROS_INSTANCE',server['environment'])

    def test_robot_needs_no_observation_mount_or_http_address(self):
        import os
        config=json.loads(subprocess.check_output(['docker','compose','-f','compose.robot.yaml','config','--format','json'],
                          cwd=ROOT,env=dict(os.environ,AGENTICROS_ROBOT_ID='test_robot',AGENTICROS_ENVIRONMENT='cartesian_gripper',ROS_DOMAIN_ID='42'),text=True))
        client=config['services']['client']
        self.assertFalse(client.get('volumes'))
        self.assertNotIn('healthcheck',client)
        self.assertNotIn('AGENTICROS_CLIENT_HOST',client['environment'])
        self.assertNotIn('AGENTICROS_OBSERVATION_PORT',client['environment'])
        self.assertFalse(list((ROOT/'docker/robot_instances').rglob('Snapshot.srv')))

    def test_no_supplied_adapter(self):
        tree = ast.parse((ROOT / 'docker/robot_instances/cartesian_gripper/world.py').read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertFalse(names & {'move', 'grip', 'evaluate', 'locate_color'})
        self.assertFalse(list((ROOT / 'docker/agent_instance/agent').rglob('body.py')))
        # Keep historical reference-code fingerprints even after removing its source.
        hashes = json.loads((ROOT / 'tests/forbidden_sources.json').read_text())
        self.assertIn('examples/pick_place.py', hashes.values())
        self.assertTrue(hashes['3325d566e064286589ca146e0a3208dedfb6eb28bf84847da936f09ca2a8eaa0'].endswith('/client.py'))
        self.assertTrue(all(len(h) == 64 for h in hashes))

    def test_agent_starts_without_device_knowledge(self):
        dockerfile = (ROOT / 'docker/agent_instance/agent/Dockerfile').read_text()
        self.assertNotIn('/opt/driver_ws', dockerfile)
        self.assertNotIn('knowledge_ws', dockerfile)
        self.assertNotIn('robot_docs', dockerfile)
        self.assertFalse((ROOT/'docker/interfaces').exists())
        self.assertFalse((ROOT / 'docker/agent_instance/agent/body.md').exists())
        for path in (ROOT / 'docker/agent_instance/agent').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                text = path.read_text()
                for private_hint in ('/driver/', '/camera/', 'agenticros_driver_interfaces',
                                     '/opt/driver_ws', 'cartesian_gripper'):
                    self.assertNotIn(private_hint, text, str(path))
        bodyfile = (ROOT / 'docker/robot_instances/cartesian_gripper/Dockerfile').read_text()
        self.assertIn('COPY docker/robot_instances/robot_docs/cartesian_gripper/interfaces ', bodyfile)
        self.assertNotIn('/opt/device-public', bodyfile)

    def test_build_context(self):
        rules=[line for line in (ROOT / '.dockerignore').read_text().splitlines() if not line.startswith('#')]
        self.assertEqual(rules, ['**', '!docker/', '!docker/**'])


if __name__ == '__main__':
    unittest.main()
