"""Inspect the actual agent image filesystem without attaching any user volumes."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]


def main():
    config = json.loads(subprocess.check_output(['docker', 'compose', 'config', '--format', 'json'], cwd=ROOT, text=True))
    image_name = config['services']['agent'].get('image', config['name'] + '-agent')
    image_id = subprocess.check_output(['docker', 'image', 'inspect', image_name,
                                       '--format', '{{.Id}}'], text=True).strip()
    private = list((ROOT / 'docker/body').glob('*.py')) + list((ROOT / 'docker/body').glob('*.xml'))
    forbidden_hashes = json.loads((ROOT / 'tests/forbidden_sources.json').read_text())
    forbidden_hashes.update({hashlib.sha256(p.read_bytes()).hexdigest(): str(p.relative_to(ROOT))
                             for p in private if p.stat().st_size})
    name = 'rosbody-image-audit-' + uuid.uuid4().hex[:8]
    subprocess.run(['docker', 'create', '--name', name, '--entrypoint', '/bin/true', image_id], check=True, capture_output=True)
    count = 0
    try:
        image = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]['Image']
        process = subprocess.Popen(['docker', 'export', name], stdout=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=process.stdout, mode='r|') as archive:
                for member in archive:
                    path = member.name.lstrip('./')
                    assert not path.startswith(('opt/body/', 'var/run/docker.sock')), path
                    assert not path.endswith(('/tabletop.xml', '/rosbody/client.py', '/adapter/body.py')), path
                    if member.isfile():
                        count += 1
                        if 0 < member.size < 2_000_000:
                            digest = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                            assert digest not in forbidden_hashes, (path, forbidden_hashes.get(digest))
            assert process.wait() == 0, 'docker export failed'
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.terminate(); process.wait()
        print(json.dumps({'image_filesystem_audit': 'PASS', 'image': image,
                          'regular_files_scanned': count, 'private_source_hashes': len(forbidden_hashes)}))
    finally:
        subprocess.run(['docker', 'rm', name], check=True, capture_output=True)


if __name__ == '__main__':
    main()
