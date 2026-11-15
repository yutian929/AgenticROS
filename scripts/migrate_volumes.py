"""Copy a stopped Compose project's data into the current namespace; retain originals.

No host credentials are imported. Only the three dedicated application volumes are copied.
The helper is the simulator image, never the Agent image, with networking disabled.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
VERIFY = r'''
import hashlib, json, os, stat
from pathlib import Path

def manifest(root):
    result = {}
    for path in [root, *sorted(root.rglob('*'))]:
        s = path.lstat()
        name = str(path.relative_to(root))
        value = [stat.S_IFMT(s.st_mode), stat.S_IMODE(s.st_mode), s.st_uid, s.st_gid]
        if path.is_symlink():
            value.append(os.readlink(path))
        elif path.is_file():
            h = hashlib.sha256()
            with path.open('rb') as f:
                for chunk in iter(lambda: f.read(1024*1024), b''):
                    h.update(chunk)
            value.extend([s.st_size, h.hexdigest()])
        elif not path.is_dir():
            raise RuntimeError('Unsupported special file in application volume')
        result[name] = value
    return result
source, target = manifest(Path('/source')), manifest(Path('/destination'))
assert source == target, 'Volume verification failed; original retained'
print(json.dumps({'verified': True, 'entries': len(source)}))
'''


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], cwd=ROOT, text=True, check=True, **kwargs)


def exists(name):
    return subprocess.run(['docker', 'volume', 'inspect', name], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-project', required=True, help='Original Compose project name')
    parser.add_argument('--apply', action='store_true', help='Copy and verify; otherwise print the plan')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', args.from_project):
        parser.error('Invalid source project name')
    config = json.loads(docker('compose', '--profile', 'simulation', 'config', '--format', 'json', capture_output=True).stdout)
    if args.from_project == config['name']:
        parser.error('Source and destination projects must differ')
    image = config['services']['simple_mujoco'].get('image', config['name'] + '-simple_mujoco')
    # Workspace is staged in a legacy volume, then prepare_workspace imports it to runs/.
    plan = [(key, args.from_project + '_' + key,
             config['name'] + '_workspace' if key == 'workspace' else config['volumes'][key]['name'])
            for key in ('workspace', 'codex-home', 'observations')]
    print(json.dumps({'source_project': args.from_project, 'target_project': config['name'],
                      'volumes': [{'source': s, 'destination': t} for _, s, t in plan]}, indent=2), flush=True)
    if not args.apply:
        return
    # Preflight every source and target before copying any data.
    for _, source, target in plan:
        if not exists(source):
            raise RuntimeError('Source volume does not exist: ' + source)
        for volume in (source, target):
            if docker('ps', '-q', '--filter', 'volume=' + volume, capture_output=True).stdout.strip():
                raise RuntimeError('Stop all containers using volume before migration: ' + volume)
        if exists(target):
            docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0',
                   '-v', target + ':/destination:ro', '--entrypoint', 'python3', image,
                   '-c', "from pathlib import Path; assert not any(Path('/destination').iterdir()), 'Destination is not empty; refusing to overwrite'")
    for key, source, target in plan:
        docker('volume', 'create', '--label', 'com.docker.compose.project=' + config['name'],
               '--label', 'com.docker.compose.volume=' + key, target, capture_output=True)
        helper = ['run', '--rm', '--network', 'none', '--read-only', '--user', '0',
                  '-v', source + ':/source:ro', '-v', target + ':/destination']
        docker(*helper, '--entrypoint', '/bin/bash', image, '-o', 'pipefail', '-c',
               'tar --numeric-owner -C /source -cpf - . | tar --numeric-owner -C /destination -xpf -')
        verified = docker(*helper, '--entrypoint', 'python3', image, '-c', VERIFY, capture_output=True)
        print(target + ': ' + verified.stdout.strip(), flush=True)
    print('Migration verified. Original volumes retained; start the current Compose project explicitly.')


if __name__ == '__main__':
    main()
