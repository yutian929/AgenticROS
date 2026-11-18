"""Preview or delete AgenticROS runtime data. Never run a global Docker prune."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def inspect(kind, ids):
    return json.loads(docker(kind, 'inspect', *ids)) if ids else []


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
        docker('container', 'rm', '-f', item['id'])
    for volume in plan['volumes']:
        docker('volume', 'rm', volume)
    for network in plan['networks']:
        docker('network', 'rm', network)
    # Recheck references immediately before deleting host data.
    for item in inspect('container', docker('ps', '-aq').split()):
        for mount in item.get('Mounts', []):
            if mount.get('Type') == 'bind' and any(
                    Path(mount['Source']) == Path(p) or Path(p) in Path(mount['Source']).parents
                    for p in plan['directories']):
                raise RuntimeError('Host data acquired a container consumer; refusing deletion')
    for directory in plan['directories']:
        path = Path(directory)
        if path.is_symlink():
            raise RuntimeError('Refusing symlink directory: '+directory)
        if path.exists():
            shutil.rmtree(path)
    for tag in plan['images']:
        docker('image', 'rm', tag)


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
    main()
