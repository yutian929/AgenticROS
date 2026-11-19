"""Stage repository rules on the host, then install them just before Agent startup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def plain_path(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError('Rule paths must not contain symlinks: ' + str(parent))


def bundle(source):
    files = {}
    for name in ('AGENTS.md', 'contract.md', 'entrypoint.sh', 'rules.py'):
        path = source / name
        plain_path(path)
        files[name] = path.read_bytes()
    for directory in ('skills', 'agents'):
        plain_path(source / directory)
        for path in sorted((source / directory).rglob('*')):
            plain_path(path)
            if '__pycache__' in path.parts:
                continue
            if path.is_file():
                files[path.relative_to(source).as_posix()] = path.read_bytes()
    return files


def stage(source, workspace):
    """Publish an immutable snapshot; do not change a running Agent's rules."""
    plain_path(workspace)
    if not workspace.is_dir():
        raise ValueError('Prepare the Agent workspace before staging rules')
    files = bundle(source)
    version = digest(json.dumps({k: digest(v) for k, v in files.items()},
                                sort_keys=True).encode())
    root = workspace / '.agenticros/rules'
    plain_path(root)
    root.mkdir(parents=True, exist_ok=True)
    root.parent.chmod(root.parent.stat().st_mode | 0o2770)
    destination = root / version
    plain_path(destination)
    if destination.exists():
        if bundle(destination) != files:
            raise ValueError('Staged rules were modified: ' + str(destination))
        return destination
    temporary = Path(tempfile.mkdtemp(prefix='.stage-', dir=root))
    try:
        temporary.chmod(0o755)
        for name, data in files.items():
            target = temporary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o644)
        try:
            temporary.rename(destination)
        except OSError:
            if not destination.is_dir() or bundle(destination) != files:
                raise
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return destination


def target_name(name):
    if name in ('AGENTS.md', 'contract.md'):
        return name
    if name.startswith('skills/'):
        return '.agents/' + name
    if name.startswith('agents/'):
        return '.codex/' + name
    return None


def managed_name(name):
    path = Path(name)
    return (not path.is_absolute() and '..' not in path.parts and
            (name in ('AGENTS.md', 'contract.md') or
             name.startswith(('.agents/skills/', '.codex/agents/'))))


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.chmod(name, 0o664)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(source, workspace, defaults=False):
    files = {target_name(k): v for k, v in bundle(source).items() if target_name(k)}
    manifest = workspace / '.agenticros/managed-rules.json'
    plain_path(manifest)
    previous = json.loads(manifest.read_text()) if manifest.exists() else {}
    if not isinstance(previous, dict) or not all(managed_name(k) for k in previous):
        raise ValueError('Invalid managed rule manifest')
    # Check all destinations before writing. Generated code and custom skills
    # outside these exact files are never copied over or recursively removed.
    for name in files.keys() | previous.keys():
        target = workspace / name
        plain_path(target)
        if target.exists() and not target.is_file():
            raise ValueError('Rule destination is not a file: ' + str(target))
    for name, data in files.items():
        target = workspace / name
        if defaults and target.exists():
            continue
        write_atomic(target, data)
    if defaults:
        return  # Image defaults never supersede host-synchronized ownership.
    for name in previous.keys() - files.keys():
        target = workspace / name
        if target.exists() and digest(target.read_bytes()) == previous[name]:
            target.unlink()  # Preserve locally edited, formerly managed files.
    write_atomic(manifest, json.dumps({k: digest(v) for k, v in files.items()},
                                    indent=2, sort_keys=True).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('stage', 'install'))
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--defaults', action='store_true')
    args = parser.parse_args()
    source = Path(__file__).absolute().parent
    workspace = args.workspace.absolute()
    plain_path(workspace)
    workspace = workspace.resolve()
    if args.operation == 'stage':
        runs = source.parents[2] / 'runs'
        if runs not in workspace.parents:
            raise ValueError('Agent workspace must be under repository runs/')
        result = stage(source, workspace)
        print('/workspace/' + (result / 'entrypoint.sh').relative_to(workspace).as_posix())
    else:
        install(source, workspace, defaults=args.defaults)


if __name__ == '__main__':
    main()
