#!/usr/bin/env python3
"""Fetch only the pinned Quanta X1 G USD and referenced sensor asset from its public distribution shard."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

REVISION = '3155121a6d6d2460aae48caa83c26af41fc0b28a'
URL = f'https://huggingface.co/datasets/x-square-robot/x2real-assets/resolve/{REVISION}/shards/assets-107-of-107.tar'
SHARD_SHA = '6d5663c81093425d89902b753e3448aca0b142dfbb6963038d4688f81a3b6e67'
MODEL_SHA = '8affd2eebf7dd4c7dad984add4d0289a006ef844f34b3694370c38589be38efa'
MEMBER = 'simready/robots/ex001/v00.05.05/ex001.usd'
ASSETS = {'ex001.usd': (378820095, MODEL_SHA),
          'sensors/Example_Rotary_2D.usda': (6172, 'cc2d463efe853293307d1ee72e0009f8043a9b00bfd25c675f52e7a49218ad3a')}


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    args = parser.parse_args()
    target = args.output
    target.mkdir(parents=True, exist_ok=True)
    model = target/'ex001.usd'
    for relative, (_, sha) in ASSETS.items():
        path = target/relative
        if path.exists() and digest(path) != sha:
            raise RuntimeError(f'Existing asset differs; preserving it: {path}')
    if all((target/name).is_file() for name in ASSETS):
        print('Verified Quanta X1 G model and dependencies: '+str(model))
        return
    cache = args.cache/REVISION
    cache.mkdir(parents=True, exist_ok=True)
    shard = cache/'assets-107-of-107.tar'
    if not shard.exists():
        partial = shard.with_suffix('.tar.part')
        offset = partial.stat().st_size if partial.exists() else 0
        request = urllib.request.Request(URL, headers={'Range': f'bytes={offset}-'} if offset else {})
        print('Downloading the 478 MB robot shard (not the 44 GB scene bundle).', flush=True)
        with urllib.request.urlopen(request, timeout=60) as response:
            resumed = offset and response.status == 206
            if resumed and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                raise RuntimeError('Unexpected download range; preserving partial file')
            with partial.open('ab' if resumed else 'wb') as output:
                while block := response.read(1024*1024):
                    output.write(block)
        if digest(partial) != SHARD_SHA:
            raise RuntimeError('Shard checksum mismatch; partial download preserved')
        partial.rename(shard)
    if digest(shard) != SHARD_SHA:
        raise RuntimeError('Existing shard checksum mismatch; file preserved')
    with tarfile.open(shard) as archive:
        for relative, (size, sha) in ASSETS.items():
            path = target/relative
            if path.exists():
                continue
            member = archive.getmember('simready/robots/ex001/v00.05.05/'+relative)
            if not member.isfile() or member.size != size:
                raise RuntimeError('Unexpected robot asset: '+relative)
            data = archive.extractfile(member).read()
            if hashlib.sha256(data).hexdigest() != sha:
                raise RuntimeError('Asset checksum mismatch: '+relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as output:
                output.write(data)
    (target/'provenance.json').write_text(json.dumps(dict(revision=REVISION, source=URL,
        member=MEMBER, sha256=MODEL_SHA, variant='G', license='Apache-2.0'), indent=2))
    print('Prepared G-gripper model: '+str(model))


if __name__ == '__main__':
    main()
