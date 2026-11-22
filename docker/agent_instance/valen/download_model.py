#!/usr/bin/env python3
"""Build-time pinned Valen model download and image bundle. Runtime stays offline."""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import urllib.error
import urllib.parse
import urllib.request
import time
import http.client

MODEL_DIRECTORY = 'Valen-Decision-1.5-4B'
LOCK_FILE = Path(__file__).with_name('model.json')
PINNED = json.loads(LOCK_FILE.read_text())
REPO, REVISION = PINNED['repo'], PINNED['revision']
CHUNK_SIZE = 8*1024*1024


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != 'https':
            raise ValueError('Refusing non-HTTPS model redirect')
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def plain(path):
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Valen paths must not contain symlinks: '+str(path))


def digest_file(path, item):
    digest = hashlib.new(item['algorithm'])
    if item['algorithm'] == 'sha1':
        digest.update(f"blob {item['size']}\0".encode())
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def manifest(opener=None):
    """Use only repository-pinned checksums, including model Python sources."""
    value = json.loads(LOCK_FILE.read_text())
    if value['repo'] != REPO or value['revision'] != REVISION or not re.fullmatch('[0-9a-f]{40}', REVISION):
        raise ValueError('Invalid pinned model identity')
    names = set()
    for item in value['files']:
        name, algorithm, digest, size = (item[k] for k in ('path', 'algorithm', 'digest', 'size'))
        if (not re.fullmatch(r'[\w.-]+', name) or name in ('.', '..') or name in names
                or algorithm not in ('sha1', 'sha256')
                or not re.fullmatch('[0-9a-f]{'+str(64 if algorithm == 'sha256' else 40)+'}', digest)
                or type(size) is not int or size <= 0):
            raise ValueError('Invalid pinned model file')
        names.add(name)
    if not {'config.json', 'modeling_valen.py', 'tokenizer.json', 'LICENSE',
            'model.safetensors.index.json'} <= names or not any(name.endswith('.safetensors') for name in names):
        raise ValueError('Incomplete pinned model manifest')
    return value


def fetch(opener, request):
    for attempt in range(3):
        try:
            return opener.open(request, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 2:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
        time.sleep(attempt+1)


def download_file(folder, item, opener):
    target = folder/item['path']
    partial = folder/(item['path']+'.part')
    plain(target)
    plain(partial)
    if target.exists():
        if target.stat().st_size != item['size'] or digest_file(target, item) != item['digest']:
            raise ValueError('Existing model file differs; preserved: '+str(target))
        return
    offset = partial.stat().st_size if partial.exists() else 0
    if offset > item['size']:
        raise ValueError('Partial file exceeds expected size; preserved: '+str(partial))
    if item['size'] > CHUNK_SIZE and offset < item['size']:
        download_chunks(partial, item, offset, opener)
        offset = partial.stat().st_size
    if offset < item['size']:
        url = 'https://huggingface.co/'+REPO+'/resolve/'+REVISION+'/'+item['path']
        request = urllib.request.Request(url, headers={'Range': f'bytes={offset}-'} if offset else {})
        with fetch(opener, request) as response:
            if response.status == 206:
                value = response.headers.get('Content-Range', '')
                if not value.startswith(f'bytes {offset}-') or not value.endswith('/'+str(item['size'])):
                    raise ValueError('Unexpected model byte range')
                mode = 'ab'
            elif response.status == 200:
                mode, offset = 'wb', 0
            else:
                raise ValueError('Unexpected download status')
            reported = offset
            with partial.open(mode) as stream:
                for block in iter(lambda: response.read(8*1024*1024), b''):
                    offset += len(block)
                    if offset > item['size']:
                        raise ValueError('Model download exceeded expected size')
                    stream.write(block)
                    if offset-reported >= 256*1024*1024:
                        print(f"{item['path']}: {offset}/{item['size']} bytes", flush=True)
                        reported = offset
                stream.flush()
                os.fsync(stream.fileno())
    if partial.stat().st_size != item['size'] or digest_file(partial, item) != item['digest']:
        raise ValueError('Model size/checksum mismatch; partial file retained: '+str(partial))
    os.replace(partial, target)
    print('Verified: '+item['path'], flush=True)


def download_chunks(partial, item, offset, opener):
    """Bounded reads, appending only a contiguous prefix, as in SAM3."""
    url = 'https://huggingface.co/'+REPO+'/resolve/'+REVISION+'/'+item['path']
    size = item['size']

    def read_range(bounds):
        start, end = bounds
        request = urllib.request.Request(url, headers={'Range':f'bytes={start}-{end}'})
        for attempt in range(3):
            try:
                with fetch(opener, request) as response:
                    if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{size}':
                        raise ValueError('Server did not honor exact model byte range')
                    data = response.read(end-start+2)
                    if len(data) != end-start+1:
                        raise http.client.IncompleteRead(data)
                    return data
            except urllib.error.HTTPError:
                raise
            except (TimeoutError, OSError, http.client.IncompleteRead):
                if attempt == 2:
                    raise
                time.sleep(attempt+1)

    with partial.open('ab') as stream, concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while offset < size:
            ranges = [(start, min(start+CHUNK_SIZE, size)-1)
                      for start in range(offset, min(offset+4*CHUNK_SIZE, size), CHUNK_SIZE)]
            for data in pool.map(read_range, ranges):
                stream.write(data)
                stream.flush()
                offset += len(data)
            os.fsync(stream.fileno())
            print(f"{item['path']}: {offset}/{size} bytes", flush=True)


def download(folder, opener=None):
    opener = opener or urllib.request.build_opener(SafeRedirect())
    for path in (folder, folder/'.download.lock', folder/'download.json', folder/'manifest.json'):
        plain(path)
    with (folder/'.download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        expected = manifest(opener)
        state_file = folder/'download.json'
        if state_file.exists() and json.loads(state_file.read_text()) != expected:
            raise ValueError('Existing download belongs to another revision; preserve and reconcile explicitly')
        if not state_file.exists() and list(folder.glob('*.part')):
            raise ValueError('Unidentified partial download; preserve before retrying')
        state_file.write_text(json.dumps(expected, indent=2))
        needed = sum(item['size'] for item in expected['files'] if not (folder/item['path']).exists())
        if shutil.disk_usage(folder).free < needed+256*1024*1024:
            raise ValueError('Insufficient disk space for model download')
        # Independent immutable shards can download concurrently. Each file keeps
        # its own resumable .part; the manifest is published only when all pass.
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(download_file, folder, item, opener) for item in expected['files']]
            for future in futures:
                future.result()
        temporary = folder/'manifest.json.tmp'
        plain(temporary)
        temporary.write_text(json.dumps(expected, indent=2))
        os.replace(temporary, folder/'manifest.json')


def bundle(cache, output, expected):
    """Only validated model files enter /models; partials and tools stay in cache."""
    plain(output)
    output.mkdir(parents=True, exist_ok=True)
    for item in expected['files']:
        source, target = cache/item['path'], output/item['path']
        plain(target)
        if source.stat().st_size != item['size'] or digest_file(source, item) != item['digest']:
            raise ValueError('Cached model checksum changed before bundling')
        if target.exists():
            if target.stat().st_size != item['size'] or digest_file(target, item) != item['digest']:
                raise ValueError('Existing output differs; preserving: '+str(target))
        else:
            shutil.copyfile(source, target)
        target.chmod(0o444)
    destination = output/'manifest.json'
    plain(destination)
    destination.write_text(json.dumps(expected, indent=2)+'\n')
    destination.chmod(0o444)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    expected = manifest()
    cache = args.cache/expected['revision']
    plain(cache)
    cache.mkdir(parents=True, exist_ok=True)
    download(cache)
    bundle(cache, args.output/MODEL_DIRECTORY, expected)


if __name__ == '__main__':
    try:
        main()
    except urllib.error.HTTPError as exc:
        raise SystemExit('Model download failed: HTTP '+str(exc.code))
    except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead):
        raise SystemExit('Model download failed: network/TLS error; partial download retained')
