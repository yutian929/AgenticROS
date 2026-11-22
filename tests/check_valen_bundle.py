"""Verify image-bundled model files offline, without GPU or host model mounts.

--fixture builds tiny synthetic weights through the real weights-stage recipe.
It tests cache reuse and packaging, not real Valen loading or model quality.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True, **kwargs)


def fixture(root, name):
    directory = root/'docker/agent_instance/valen'
    directory.mkdir(parents=True)
    shutil.copyfile(ROOT/'docker/agent_instance/valen/download_model.py', directory/'download_model.py')
    expected = json.loads((ROOT/'docker/agent_instance/valen/model.json').read_text())
    expected['revision'] = 'f'*40
    files = root/'fixture'
    files.mkdir()
    for item in expected['files']:
        data = ('synthetic bundle test: '+item['path']).encode()
        item.update(size=len(data), algorithm='sha256', digest=hashlib.sha256(data).hexdigest())
        (files/item['path']).write_bytes(data)
    (directory/'model.json').write_text(json.dumps(expected))
    (files/'download.json').write_text(json.dumps(expected))
    (files/'unfinished.part').write_bytes(b'unfinished; must stay outside image model directory')
    (files/'seed.py').write_text('''import json, pathlib, shutil
source=pathlib.Path('/fixture')
expected=json.loads((source/'download.json').read_text())
cache=pathlib.Path('/var/cache/valen')/expected['revision']
cache.mkdir(parents=True,exist_ok=True)
for path in source.iterdir():
    if path.name!='seed.py': shutil.copyfile(path,cache/path.name)
''')
    recipe = (ROOT/'docker/agent_instance/valen/Dockerfile').read_text()
    recipe = recipe.replace('id=agenticros-valen-model,', 'id='+name+',')
    recipe = recipe.replace('RUN --mount=type=cache,id='+name+',', 'RUN --network=none --mount=type=cache,id='+name+',')
    recipe += f'''
FROM ros-dependencies AS fixture-cache
COPY fixture/ /fixture/
RUN --network=none --mount=type=cache,id={name},target=/var/cache/valen,sharing=locked python3 /fixture/seed.py
'''
    (root/'Dockerfile').write_text(recipe)
    docker('build', '--target', 'fixture-cache', '-t', 'agenticros-valen:'+name+'-cache', str(root))
    image = 'agenticros-valen:'+name
    # The weights RUN has no network. A hidden runtime/metadata fetch fails.
    docker('build', '--target', 'weights', '-t', image, str(root))
    return image, expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agenticros-valen:single')
    parser.add_argument('--fixture', action='store_true')
    args = parser.parse_args()
    name = 'agenticros-valen-bundle-'+uuid.uuid4().hex[:8]
    root = ROOT/'runs/checks'/name
    root.mkdir(parents=True)
    expected = json.loads((ROOT/'docker/agent_instance/valen/model.json').read_text())
    image = args.image
    if args.fixture:
        image, expected = fixture(root, name)
    code = '''import hashlib,json,pathlib
root=pathlib.Path('/models/Valen-Decision-1.5-4B')
manifest=json.loads((root/'manifest.json').read_text())
lock=pathlib.Path('/opt/valen/model.json')
if not lock.exists(): lock=pathlib.Path('/opt/download/model.json')
assert manifest==json.loads(lock.read_text())
assert {p.name for p in root.iterdir()}=={f['path'] for f in manifest['files']}|{'manifest.json'}
for item in manifest['files']:
    path=root/item['path']
    assert path.stat().st_size==item['size'] and not path.is_symlink()
    assert path.stat().st_mode & 0o222==0
    digest=hashlib.new(item['algorithm'])
    if item['algorithm']=='sha1': digest.update(f"blob {item['size']}\\0".encode())
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(8*1024*1024),b''): digest.update(block)
    assert digest.hexdigest()==item['digest'],item['path']
print(json.dumps(manifest))
'''
    docker('create', '--name', name, '--network', 'none', '--read-only', '--no-healthcheck',
           '--user', '1000:1000', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
           '--entrypoint', 'python3', image, '-c', code, capture_output=True)
    try:
        info = json.loads(docker('inspect', name, capture_output=True).stdout)[0]
        assert not info['Mounts'] and not info['HostConfig']['DeviceRequests']
        result = docker('start', '-a', name, capture_output=True)
        state = json.loads(docker('inspect', name, capture_output=True).stdout)[0]['State']
        assert state['ExitCode'] == 0, result.stderr
        assert json.loads(result.stdout) == expected
        (root/'result.json').write_text(json.dumps(dict(result='PASS', image=image, image_id=info['Image'],
            fixture=args.fixture, real_model_files=not args.fixture, real_inference=False,
            network='none', mounts=info['Mounts'], model_revision=expected['revision']), indent=2))
        print('PASS: '+str(root/'result.json'))
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)


if __name__ == '__main__':
    main()
