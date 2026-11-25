"""Exercise both arms in a disposable, network-isolated simulation container."""
import argparse
import json
from pathlib import Path
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agenticros-aloha_tabletop:single')
    parser.add_argument('--cartesian', action='store_true', help='test pose/delta actions, frame semantics and cancellation')
    parser.add_argument('--alignment', action='store_true', help='test shared Quanta naming, frames and recovery fault injection')
    args = parser.parse_args()
    if args.cartesian and args.alignment:
        parser.error('--cartesian and --alignment are separate checks')
    test = 'aloha_alignment_integration.py' if args.alignment else ('aloha_cartesian_integration.py' if args.cartesian else 'aloha_integration.py')
    name = 'agenticros-aloha-check-' + uuid.uuid4().hex[:8]
    evidence = ROOT / 'runs' / 'checks' / name
    evidence.mkdir(parents=True)
    subprocess.run([
        'docker', 'create', '--name', name, '--network', 'none',
        '--entrypoint', '/bin/bash', args.image, '-lc',
        'source /opt/ros/humble/setup.bash && '
        'source /opt/driver_ws/install/setup.bash && '
        '/opt/body-venv/bin/python /tmp/aloha_integration.py',
    ], check=True, capture_output=True)
    try:
        subprocess.run(['docker', 'cp', str(ROOT / 'tests' / test),
                        name + ':/tmp/aloha_integration.py'], check=True)
        with (evidence / 'test.log').open('w') as log:
            subprocess.run(['docker', 'start', '-a', name], stdout=log,
                           stderr=subprocess.STDOUT, timeout=360 if args.cartesian else 180, check=True)
        status = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]['State']
        if status['ExitCode'] != 0:
            raise RuntimeError(f'Test failed; see {evidence / "test.log"}')
        subprocess.run(['docker', 'cp', name + ':/tmp/test-evidence/.', str(evidence)], check=True)
        assert (evidence / 'integration.json').is_file()
        print(f'PASS: {evidence / "integration.json"}')
    finally:
        if not (evidence / 'integration.json').exists():
            subprocess.run(['docker', 'cp', name + ':/tmp/test-evidence/.', str(evidence)], capture_output=True)
        subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)


if __name__ == '__main__':
    main()
