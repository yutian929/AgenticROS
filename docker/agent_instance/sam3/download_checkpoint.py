#!/usr/bin/env python3
"""Build-time pinned SAM3 checkpoint download. No host/runtime preparation."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request

MODEL_API = 'https://modelscope.cn/api/v1/models/facebook/sam3'

class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing non-HTTPS checkpoint redirect')
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download_ranges(fetch, url, partial, offset, size, chunk_size=8*1024*1024, workers=4):
    """Bounded parallel reads; publish only a contiguous verified-range prefix."""
    def read_range(bounds):
        start,end=bounds
        with fetch(url, {'Range':f'bytes={start}-{end}'}) as response:
            expected=f'bytes {start}-{end}/{size}'
            if response.status != 206 or response.headers.get('Content-Range') != expected:
                raise ValueError('Server did not honor exact checkpoint byte range')
            data=response.read(end-start+2)
            if len(data) != end-start+1:
                raise ValueError('Checkpoint range size mismatch')
            return data
    with partial.open('ab' if offset else 'wb') as output, ThreadPoolExecutor(max_workers=workers) as pool:
        while offset < size:
            ranges=[(start,min(start+chunk_size,size)-1)
                    for start in range(offset,min(offset+workers*chunk_size,size),chunk_size)]
            for data in pool.map(read_range,ranges):
                output.write(data)
                output.flush()
                offset+=len(data)
            os.fsync(output.fileno())
            print(f'Checkpoint: {offset}/{size} bytes ({offset/size:.1%})',flush=True)


def download(models, opener=None, manifest=None):
    """Resume only the same revision/hash; validate before atomic publication."""
    manifest = manifest or json.loads(Path(__file__).with_name("checkpoint.json").read_text())
    models.mkdir(parents=True, exist_ok=True)
    target = models/'sam3.pt'
    partial, state_file = models/'sam3.pt.part', models/'download.json'
    for path in (target, partial, state_file, models/'.download.lock'):
        if path.is_symlink():
            raise ValueError('Checkpoint paths must not be symlinks')
    if target.exists():
        if target.stat().st_size != manifest['size'] or sha256(target) != manifest['sha256']:
            raise ValueError('Existing checkpoint size/SHA256 mismatch; preserving it')
        print('Verified cached checkpoint: '+str(target))
        return
    opener = opener or urllib.request.build_opener(SafeRedirect())
    def fetch(url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        for attempt in range(3):
            try:
                return opener.open(request, timeout=60)
            except (TimeoutError, urllib.error.URLError):
                if attempt == 2: raise
                time.sleep(attempt+1)
    with (models/'.download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            if target.stat().st_size != manifest['size'] or sha256(target) != manifest['sha256']:
                raise ValueError('Existing checkpoint size/SHA256 mismatch; preserving it')
            return
        expected = dict(manifest)
        commit, digest, size = expected['revision'], expected['sha256'], expected['size']
        if not re.fullmatch('[0-9a-f]{40}',commit) or not re.fullmatch('[0-9a-f]{64}',digest) or not isinstance(size,int) or size <= 0:
            raise ValueError('Invalid pinned checkpoint manifest')
        if partial.exists() and state_file.exists() and json.loads(state_file.read_text()) != expected:
            raise ValueError('Partial download belongs to another model revision; preserve/move it before retrying')
        if partial.exists() and not state_file.exists():
            raise ValueError('Unidentified partial checkpoint; preserve/move it before retrying')
        state_file.write_text(json.dumps(expected, indent=2))
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > size:
            raise ValueError('Partial checkpoint is larger than expected; preserve/move it before retrying')
        if shutil.disk_usage(models).free < size-offset+256*1024*1024:
            raise ValueError('Insufficient disk space for checkpoint download')
        url = MODEL_API+'/repo?'+urllib.parse.urlencode(dict(Revision=commit, FilePath='sam3.pt'))
        if size-offset >= 128*1024*1024:
            download_ranges(fetch, url, partial, offset, size)
        elif offset < size:
            headers = {'Range':f'bytes={offset}-'} if offset else {}
            url = MODEL_API+'/repo?'+urllib.parse.urlencode(dict(Revision=commit, FilePath='sam3.pt'))
            with fetch(url, headers) as response:
                if response.status == 206:
                    content_range = response.headers.get('Content-Range','')
                    if not content_range.startswith(f'bytes {offset}-') or not content_range.endswith('/'+str(size)):
                        raise ValueError('Unexpected checkpoint byte range')
                    mode = 'ab' if offset else 'wb'
                elif response.status == 200:
                    mode, offset = 'wb', 0
                else:
                    raise ValueError('Unexpected checkpoint response status')
                last_report = 0.
                with partial.open(mode) as output:
                    for chunk in iter(lambda: response.read(8*1024*1024), b''):
                        offset += len(chunk)
                        if offset > size:
                            raise ValueError('Checkpoint response exceeded advertised size')
                        output.write(chunk)
                        if time.monotonic()-last_report >= 5:
                            print(f'Checkpoint: {offset}/{size} bytes ({offset/size:.1%})',flush=True)
                            last_report = time.monotonic()
                    output.flush()
                    os.fsync(output.fileno())
        if partial.stat().st_size != size or sha256(partial) != digest:
            raise ValueError('Checkpoint size/SHA256 mismatch; partial file retained, not activated')
        os.replace(partial,target)
        print('Verified checkpoint: '+str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name('checkpoint.json').read_text())
    cache = args.cache/manifest['sha256']
    download(cache, manifest=manifest)
    args.output.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(cache/'sam3.pt',args.output/'sam3.pt')
    (args.output/'checkpoint.json').write_text(json.dumps(manifest,indent=2)+'\n')
    (args.output/'sam3.pt').chmod(0o444)

if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit('Checkpoint download failed: HTTP '+str(exc.code))
    except urllib.error.URLError:
        raise SystemExit('Checkpoint download failed: network/TLS error; partial download retained')
