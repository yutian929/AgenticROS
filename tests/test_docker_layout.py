"""Host-side layout checks; standard library and Docker CLI only."""
import ast
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerLayout(unittest.TestCase):
    def instance_config(self, name):
        return json.loads(subprocess.check_output([
            'bash', '-c', 'source scripts/instance.sh >/dev/null; '
            'docker compose --profile simulation config --format json',
            'instance-test', '--instance', name], cwd=ROOT, text=True))

    def test_named_instances_isolate_data_but_share_body(self):
        first = self.instance_config('account-a')
        second = self.instance_config('account-b')
        for name, config in [('account-a', first), ('account-b', second)]:
            mounts = {m['target']: m for m in config['services']['agenticros']['volumes']}
            self.assertEqual(Path(mounts['/workspace']['source']),
                             ROOT / 'runs/instances' / name / 'workspace')
            self.assertEqual(config['volumes']['codex-home']['name'],
                             'agenticros_codex-home-instance-' + name)
            self.assertEqual(set(mounts),
                             {'/workspace', '/home/agent/.codex', '/observations', '/config/codex.toml'})
            self.assertTrue(mounts['/observations']['read_only'])
        self.assertEqual(first['services']['simple_mujoco'], second['services']['simple_mujoco'])
        self.assertEqual(first['networks'], second['networks'])
        default = self.instance_config('default')
        self.assertEqual(default['volumes']['codex-home']['name'], 'agenticros_codex-home')

    def test_instance_option_preserves_codex_arguments(self):
        result = subprocess.check_output([
            'bash', '-c', 'source scripts/instance.sh >/dev/null; printf "%s\\n" "$@"',
            'instance-test', '--instance', 'account-a', 'resume', '--last'], cwd=ROOT, text=True)
        self.assertEqual(result.splitlines(), ['resume', '--last'])

    def test_invalid_instances_fail_before_starting_containers(self):
        for name in ('../escape', '/tmp', '', '-bad', 'a' * 49):
            result = subprocess.run(['bash', 'scripts/agenticros.sh', '--instance', name],
                                    cwd=ROOT, capture_output=True)
            self.assertEqual(result.returncode, 2)
        result = subprocess.run(['bash', 'scripts/docker-login.sh', '--instance'],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 2)

    def test_agent_boundary(self):
        config = json.loads(subprocess.check_output(
            ['docker', 'compose', '--profile', 'simulation', 'config', '--format', 'json'], cwd=ROOT, text=True))
        agent = config['services']['agenticros']
        self.assertFalse(agent.get('privileged', False))
        self.assertEqual(agent['cap_drop'], ['ALL'])
        mounts = {m['target']: m for m in agent['volumes']}
        self.assertEqual(set(mounts), {'/workspace', '/observations', '/home/agent/.codex', '/config/codex.toml'})
        self.assertTrue(mounts['/observations']['read_only'])
        for target, mount in mounts.items():
            if target == '/config/codex.toml':
                self.assertEqual(mount['type'], 'bind')
                self.assertEqual(Path(mount['source']), ROOT / 'docker/agenticros/codex.toml')
                self.assertTrue(mount['read_only'])
            elif target == '/workspace':
                self.assertEqual(mount['type'], 'bind')
                self.assertEqual(Path(mount['source']), ROOT / 'runs/workspace')
                self.assertFalse(mount.get('read_only', False))
                self.assertFalse(mount['bind']['create_host_path'])
            else:
                self.assertEqual(mount['type'], 'volume')
        for line in (ROOT / 'docker/agenticros/Dockerfile').read_text().splitlines():
            if line.startswith('COPY '):
                self.assertTrue(all(s.startswith(('docker/agenticros/', 'docker/shared/'))
                                    for s in line.split()[1:-1]))

    def test_environment_is_opt_in(self):
        config = json.loads(subprocess.check_output(
            ['docker', 'compose', '--profile', 'simulation', 'config', '--format', 'json'],
            cwd=ROOT, text=True))
        self.assertEqual(set(config['services']), {'simple_mujoco', 'agenticros'})
        self.assertEqual(config['services']['simple_mujoco']['profiles'], ['simulation'])
        body = config['services']['simple_mujoco']
        self.assertEqual({m['target'] for m in body['volumes']}, {'/observations', '/viewer-auth'})
        self.assertEqual(len(body['ports']), 1)
        self.assertEqual(body['ports'][0]['target'], 8080)
        self.assertEqual(body['ports'][0]['host_ip'], '127.0.0.1')
        self.assertFalse(any(m['type'] == 'bind' for m in body['volumes']))
        self.assertNotIn('depends_on', config['services']['agenticros'])
        self.assertEqual(config['name'], 'agenticros')
        self.assertNotIn('workspace', config['volumes'])
        self.assertEqual(config['volumes']['codex-home']['name'], 'agenticros_codex-home')
        self.assertEqual(config['volumes']['viewer-auth']['name'], 'agenticros_viewer-auth')
        self.assertEqual(config['networks']['robot']['name'], 'agenticros_robot')

    def test_no_supplied_adapter(self):
        tree = ast.parse((ROOT / 'docker/environments/simple_mujoco/world.py').read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertFalse(names & {'move', 'grip', 'evaluate', 'locate_color'})
        self.assertFalse(list((ROOT / 'docker/agenticros').rglob('body.py')))
        # Keep historical reference-code fingerprints even after removing its source.
        hashes = json.loads((ROOT / 'tests/forbidden_sources.json').read_text())
        self.assertIn('examples/pick_place.py', hashes.values())
        self.assertTrue(hashes['3325d566e064286589ca146e0a3208dedfb6eb28bf84847da936f09ca2a8eaa0'].endswith('/client.py'))
        self.assertTrue(all(len(h) == 64 for h in hashes))

    def test_agent_starts_without_device_knowledge(self):
        dockerfile = (ROOT / 'docker/agenticros/Dockerfile').read_text()
        self.assertNotIn('/opt/driver_ws', dockerfile)
        self.assertNotIn('colcon build', dockerfile)
        self.assertFalse((ROOT / 'docker/interfaces').exists())
        self.assertFalse((ROOT / 'docker/agenticros/body.md').exists())
        for path in (ROOT / 'docker/agenticros').rglob('*'):
            if path.is_file() and '__pycache__' not in path.parts:
                text = path.read_text()
                for private_hint in ('/driver/', '/camera/', 'agenticros_driver_interfaces',
                                     '/opt/driver_ws', 'simple_mujoco'):
                    self.assertNotIn(private_hint, text, str(path))
        bodyfile = (ROOT / 'docker/environments/simple_mujoco/Dockerfile').read_text()
        self.assertIn('COPY docker/environments/simple_mujoco/interfaces ', bodyfile)
        self.assertIn('COPY docker/environments/simple_mujoco/body.md ', bodyfile)

    def test_build_context(self):
        self.assertEqual((ROOT / '.dockerignore').read_text().splitlines(), ['**', '!docker/', '!docker/**'])


if __name__ == '__main__':
    unittest.main()
