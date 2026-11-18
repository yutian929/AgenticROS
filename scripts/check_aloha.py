"""Exercise both arms in a disposable, network-isolated simulation container."""
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    name = 'agenticros-aloha-check-' + uuid.uuid4().hex[:8]
    evidence = ROOT / 'runs' / name
    evidence.mkdir(parents=True)
    subprocess.run([
        'docker', 'create', '--name', name, '--network', 'none',
        '--entrypoint', '/bin/bash', 'agenticros-aloha_tabletop:lan', '-lc',
        'source /opt/ros/humble/setup.bash && '
        'source /opt/driver_ws/install/setup.bash && '
        '/opt/body-venv/bin/python /tmp/aloha_integration.py',
    ], check=True, capture_output=True)
    try:
        subprocess.run(['docker', 'cp', str(ROOT / 'tests/aloha_integration.py'),
                        name + ':/tmp/aloha_integration.py'], check=True)
        with (evidence / 'test.log').open('w') as log:
            subprocess.run(['docker', 'start', '-a', name], stdout=log,
                           stderr=subprocess.STDOUT, timeout=180, check=True)
        status = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]['State']
        if status['ExitCode'] != 0:
            raise RuntimeError(f'Test failed; see {evidence / "test.log"}')
        subprocess.run(['docker', 'cp', name + ':/observations/.', str(evidence)], check=True)
        assert (evidence / 'integration.json').is_file()
        print(f'PASS: {evidence / "integration.json"}')
    finally:
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)


if __name__ == '__main__':
    main()
