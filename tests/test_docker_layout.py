"""Guard the agent/private-body boundary and adaptation starting point."""
from pathlib import Path
import ast
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_agent_has_no_private_sources_or_host_mounts():
    data = yaml.safe_load((ROOT / 'compose.yaml').read_text())
    agent = data['services']['agent']
    assert not agent.get('privileged', False)
    assert agent['cap_drop'] == ['ALL']
    mounts = agent['volumes']
    assert 'observations:/observations:ro' in mounts
    assert all('docker.sock' not in m and '/Users/' not in m for m in mounts)
    assert [m for m in mounts if m.startswith('.')] == ['./docker/agent/codex.toml:/config/codex.toml:ro']
    dockerfile = (ROOT / 'docker/agent/Dockerfile').read_text()
    for line in dockerfile.splitlines():
        if line.startswith('COPY '):
            sources = line.split()[1:-1]
            assert all(s.startswith(('docker/agent/', 'docker/interfaces', 'docker/shared/')) for s in sources)
    assert 'rosbody.client' not in dockerfile


def test_private_world_does_not_supply_standard_adapter():
    tree = ast.parse((ROOT / 'docker/body/world.py').read_text())
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert not names & {'move', 'grip', 'evaluate', 'locate_color'}
    assert not list((ROOT / 'docker/agent').rglob('body.py'))
    contract = (ROOT / 'docker/agent/contract.md').read_text()
    assert 'does not supply its implementation' in contract


def test_image_only_build_context():
    assert (ROOT / '.dockerignore').read_text().splitlines() == ['**', '!docker/', '!docker/**']
