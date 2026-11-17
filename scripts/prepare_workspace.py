"""Prepare the ignored host workspace; import a stopped legacy volume without deleting it."""
import json
import os
import re
from pathlib import Path
import stat
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]

# Runs in a disposable maintenance container, without credentials or networking.
IMPORT = r'''
import hashlib, json, os, shutil, stat, sys
from pathlib import Path
source, destination = Path('/source'), Path('/destination')
def manifest(root):
    result = {}
    for path in root.rglob('*'):
        name = str(path.relative_to(root))
        if path.is_symlink():
            result[name] = ['link', os.readlink(path)]
        elif path.is_dir():
            result[name] = ['directory']
        elif path.is_file():
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            result[name] = ['file', digest.hexdigest()]
        else:
            raise RuntimeError('Unsupported special file in workspace')
    return result
before = manifest(source)
shutil.copytree(source, destination, symlinks=True, dirs_exist_ok=True)
assert before == manifest(source) == manifest(destination), 'Workspace changed or copy verification failed'
uid, gid = map(int, sys.argv[1:])
for path in [destination, *destination.rglob('*')]:
    os.lchown(path, uid, gid)
    if not path.is_symlink():
        mode = stat.S_IMODE(path.stat().st_mode) | stat.S_IRGRP | stat.S_IWGRP
        if path.is_dir():
            mode |= stat.S_IXGRP | stat.S_ISGID
        path.chmod(mode)
print(json.dumps({'copied_entries': len(before), 'content_verified': True}))
'''


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], cwd=ROOT, check=True, text=True, **kwargs)


def prepare():
    runs = ROOT / 'runs'
    instance = os.environ.get('AGENTICROS_INSTANCE', 'default')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,47}', instance):
        raise RuntimeError('Invalid Agent instance name')
    destination = (runs / 'workspace' if instance == 'default' else
                   runs / 'instances' / instance / 'workspace')
    for path in [destination, *destination.parents]:
        if path == ROOT:
            break
        if path.is_symlink():
            raise RuntimeError('Workspace paths must be real directories, not symlinks')
    if instance != 'default':
        # New identities never import the default workspace or legacy volumes.
        destination.mkdir(parents=True, exist_ok=True)
        destination.chmod(stat.S_IMODE(destination.stat().st_mode) | 0o2770)
        print('Workspace: ' + str(destination))
        return
    config = json.loads(docker('compose', 'config', '--format', 'json', capture_output=True).stdout)
    legacy = config['name'] + '_workspace'
    exists = subprocess.run(['docker', 'volume', 'inspect', legacy], capture_output=True).returncode == 0
    if destination.exists() and any(destination.iterdir()):
        print('Workspace: ' + str(destination) + ' (existing files retained; no automatic merge)')
        return
    if exists:
        active = docker('ps', '-q', '--filter', 'volume=' + legacy, capture_output=True).stdout.strip()
        if active:
            raise RuntimeError('旧工作区仍被运行中的 Agent 使用。请先退出旧 Agent，再重新启动；仿真无需停止。')
    runs.mkdir(exist_ok=True)
    destination.mkdir(exist_ok=True)
    destination.chmod(stat.S_IMODE(destination.stat().st_mode) | 0o2770)
    if exists:
        # Import into a sibling directory; only replace the empty destination after verification.
        staging = Path(tempfile.mkdtemp(prefix='.workspace-import-', dir=runs))
        image = config['services']['agenticros'].get('image', config['name'] + '-agenticros')
        docker('run', '--rm', '--network', 'none', '--read-only', '--user', '0',
               '--mount', 'type=volume,src=' + legacy + ',dst=/source,readonly',
               '--mount', 'type=bind,src=' + str(staging) + ',dst=/destination',
               '--entrypoint', '/opt/agent-venv/bin/python', image, '-c', IMPORT,
               str(os.getuid()), str(os.getgid()))
        # A concurrently populated target is never overwritten by directory replacement.
        staging.replace(destination)
        print('Legacy workspace copied and verified; original volume retained: ' + legacy)
    print('Workspace: ' + str(destination))


if __name__ == '__main__':
    try:
        prepare()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
