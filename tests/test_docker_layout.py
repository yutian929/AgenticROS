"""Host-side layout checks; standard library and Docker CLI only."""
import ast
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DockerLayout(unittest.TestCase):
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
            else:
                self.assertEqual(mount['type'], 'volume')
        for line in (ROOT / 'docker/agenticros/Dockerfile').read_text().splitlines():
            if line.startswith('COPY '):
                self.assertTrue(all(s.startswith(('docker/agenticros/', 'docker/interfaces', 'docker/shared/'))
                                    for s in line.split()[1:-1]))

    def test_environment_is_opt_in(self):
        config = json.loads(subprocess.check_output(
            ['docker', 'compose', '--profile', 'simulation', 'config', '--format', 'json'],
            cwd=ROOT, text=True))
        self.assertEqual(set(config['services']), {'simple_mujoco', 'agenticros'})
        self.assertEqual(config['services']['simple_mujoco']['profiles'], ['simulation'])
        self.assertNotIn('depends_on', config['services']['agenticros'])
        self.assertEqual(config['volumes']['workspace']['name'], 'rosbody_workspace')
        self.assertEqual(config['volumes']['codex-home']['name'], 'rosbody_codex-home')

    def test_no_supplied_adapter(self):
        tree = ast.parse((ROOT / 'docker/simple_mujoco/world.py').read_text())
        names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertFalse(names & {'move', 'grip', 'evaluate', 'locate_color'})
        self.assertFalse(list((ROOT / 'docker/agent').rglob('body.py')))
        self.assertIn('does not supply its implementation', (ROOT / 'docker/agenticros/contract.md').read_text())
        # Keep historical reference-code fingerprints even after removing its source.
        hashes = json.loads((ROOT / 'tests/forbidden_sources.json').read_text())
        self.assertTrue({'rosbody/client.py', 'examples/pick_place.py'} <= set(hashes.values()))
        self.assertTrue(all(len(h) == 64 for h in hashes))

    def test_build_context(self):
        self.assertEqual((ROOT / '.dockerignore').read_text().splitlines(), ['**', '!docker/', '!docker/**'])


if __name__ == '__main__':
    unittest.main()
