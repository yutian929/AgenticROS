"""Preview or delete AgenticROS runtime data. Never run a global Docker prune."""
import argparse
import errno
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).strip()


def missing_resource(error):
    message = error.stderr or error.output or ''
    if isinstance(message, bytes):
        message = message.decode(errors='replace')
    return bool(re.search(r'No such (?:container|image|volume|network|object):|'
                          r'(?:network|volume|container|image) \S+ not found', message))


def inspect(kind, ids):
    if not ids:
        return []
    try:
        return json.loads(docker(kind, 'inspect', *ids))
    except subprocess.CalledProcessError as error:
        if not missing_resource(error):
            raise
        if len(ids) == 1:
            return []
        # A resource can disappear between listing and inspection.
        return [item for identity in ids for item in inspect(kind, [identity])]


def remove_resource(kind, identity, force=False):
    try:
        docker(kind, 'rm', *(['-f'] if force else []), identity)
    except subprocess.CalledProcessError as error:
        if not missing_resource(error):
            raise


def check_consumers(directories):
    for item in inspect('container', docker('ps', '-aq').split()):
        for mount in item.get('Mounts', []):
            if mount.get('Type') == 'bind' and any(
                    Path(mount['Source']) == Path(p) or Path(p) in Path(mount['Source']).parents
                    for p in directories):
                raise RuntimeError('Host data acquired a container consumer; refusing deletion')


def remove_directory(path):
    # Docker bind mounts or another cleaner may briefly change directory entries.
    # Retry only those races, and never retry past a newly attached container.
    for attempt in range(3):
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise RuntimeError('Refusing symlink directory: '+str(path))
        check_consumers([str(path)])
        try:
            shutil.rmtree(path)
            return
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.ENOTEMPTY):
                raise
            if not path.exists():
                return
            if attempt == 2:
                raise RuntimeError('Directory keeps changing during cleanup: '+str(path)+
                                   '; stop tests/writers before retrying; remaining data retained') from error
            time.sleep(.1 * (attempt+1))


def owned(name):
    return name.lstrip('/').startswith(('agenticros-', 'agenticros_', 'rosbody-', 'rosbody_'))


def inventory(images=False):
    runs = ROOT/'runs'
    if runs.is_symlink():
        raise RuntimeError('Refusing cleanup: runs is a symlink')
    containers = inspect('container', docker('ps', '-aq').split())
    selected = []
    for item in containers:
        labels = item['Config'].get('Labels') or {}
        directory = labels.get('com.docker.compose.project.working_dir')
        bound = any(m.get('Type') == 'bind' and
                    (Path(m['Source']) == runs or runs in Path(m['Source']).parents)
                    for m in item.get('Mounts', []))
        if bound or directory == str(ROOT) or (not directory and owned(item['Name'])):
            selected.append(item)
    ids = {item['Id'] for item in selected}
    projects = {(item['Config'].get('Labels') or {}).get('com.docker.compose.project')
                for item in selected} - {None}
    volumes = inspect('volume', docker('volume', 'ls', '-q').split())
    volumes = [v['Name'] for v in volumes if owned(v['Name']) or
               (v.get('Labels') or {}).get('com.docker.compose.project') in projects]
    # Refuse before any mutation if a resource is also used outside this plan.
    for item in containers:
        if item['Id'] not in ids and any(m.get('Name') in volumes for m in item.get('Mounts', [])):
            raise RuntimeError('Volume also used by an unselected container: '+item['Name'])
    networks = inspect('network', docker('network', 'ls', '-q').split())
    networks = [n['Name'] for n in networks if
                (owned(n['Name']) or (n.get('Labels') or {}).get('com.docker.compose.project') in projects)
                and set(n.get('Containers', {})) <= ids]
    tags = docker('image', 'ls', '--format', '{{.Repository}}:{{.Tag}}').splitlines() if images else []
    tags = sorted({tag for tag in tags if owned(tag) and not tag.endswith(':<none>')})
    caches = [str(p) for p in ROOT.rglob('__pycache__') if runs not in p.parents and '.git' not in p.parts]
    return dict(containers=[dict(id=c['Id'], name=c['Name'].lstrip('/')) for c in selected],
                volumes=volumes, networks=networks, images=tags,
                directories=([str(runs)] if runs.exists() else [])+caches)


def clean(plan):
    for item in plan['containers']:
        remove_resource('container', item['id'], force=True)
    for volume in plan['volumes']:
        remove_resource('volume', volume)
    for network in plan['networks']:
        remove_resource('network', network)
    # Recheck references immediately before deleting host data.
    check_consumers(plan['directories'])
    for directory in plan['directories']:
        path = Path(directory)
        remove_directory(path)
    for tag in plan['images']:
        remove_resource('image', tag)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes', action='store_true', help='delete the listed containers, ALL data volumes (including login), runs and Python caches')
    parser.add_argument('--images', action='store_true', help='also remove project image tags; shared Docker build cache is retained')
    args = parser.parse_args()
    plan = inventory(args.images)
    print(json.dumps(plan, indent=2), flush=True)
    if args.yes:
        clean(plan)
        print('Runtime cleanup complete. Source and Git history retained.')
    else:
        print('Preview only. Add --yes to delete, including credentials, histories and all runs data.')


if __name__ == '__main__':
    try:
        main()
    except subprocess.CalledProcessError as error:
        print('Cleanup stopped: '+str(error.stderr or error).strip(), file=sys.stderr)
        sys.exit(1)
    except (OSError, RuntimeError) as error:
        print('Cleanup stopped: '+str(error), file=sys.stderr)
        sys.exit(1)
