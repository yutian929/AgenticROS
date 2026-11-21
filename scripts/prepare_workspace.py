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
    destination = ROOT/'runs'/str(int(os.environ.get('ROS_DOMAIN_ID', '42')))/'agent_instance/agent/workspace'
    for path in [destination, *destination.parents]:
        if path == ROOT: break
        if path.is_symlink():
            raise RuntimeError('Workspace paths must be real directories, not symlinks')
    destination.mkdir(parents=True, exist_ok=True)
    destination.chmod(stat.S_IMODE(destination.stat().st_mode) | 0o2770)
    print('Workspace: '+str(destination))


if __name__ == '__main__':
    try:
        prepare()
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
