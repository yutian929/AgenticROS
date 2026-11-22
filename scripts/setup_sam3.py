#!/usr/bin/env python3
"""Prepare optional SAM3 infrastructure using host Python 3 and Docker only.

Default: initialize dedicated directories and check prerequisites.
--download: fetch the public ModelScope facebook/sam3 checkpoint.
--build: build the image and probe CUDA inside a disposable container.
Never installs host packages, restarts Docker, or starts/stops live services.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Also importable by host unit tests without changing the global module path.
import importlib.util
_spec = importlib.util.spec_from_file_location('sam3_runtime', Path(__file__).with_name('runtime.py'))
runtime = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runtime)
ROOT = runtime.ROOT
MODEL_PAGE = 'https://modelscope.cn/models/facebook/sam3'
MODEL_API = 'https://modelscope.cn/api/v1/models/facebook/sam3'
TOOLKIT_PAGE = 'https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html'


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


def directories(domain):
    root = ROOT/'runs'/str(runtime.domain_id(domain))/'agent_instance/sam3'
    for path in (root/'models', root/'records'):
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('SAM3 directories must not contain symlinks')
        path.mkdir(parents=True, exist_ok=True)
    return root


def download(models, opener=None):
    """Resume only the same revision/hash; validate before atomic publication."""
    target = models/'sam3.pt'
    partial, state_file = models/'sam3.pt.part', models/'download.json'
    for path in (target, partial, state_file, models/'.download.lock'):
        if path.is_symlink():
            raise ValueError('Checkpoint paths must not be symlinks')
    if target.exists():
        print('Checkpoint already exists; preserving: '+str(target))
        return
    opener = opener or urllib.request.build_opener(SafeRedirect())
    def fetch(url, headers=None):
        request = urllib.request.Request(url, headers=headers or {})
        return opener.open(request, timeout=60)
    with (models/'.download.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists():
            return
        with fetch(MODEL_API+'/repo/files?Revision=master&Recursive=true') as response:
            metadata = json.load(response)
        if metadata.get('Code') != 200 or not metadata.get('Success'):
            raise ValueError('ModelScope did not return a successful file listing')
        weight = next((item for item in metadata.get('Data', {}).get('Files', [])
                       if item.get('Path') == 'sam3.pt' and item.get('Type') == 'blob'), {})
        commit = weight.get('Revision', '')
        digest, size = weight.get('Sha256', ''), weight.get('Size', 0)
        if not re.fullmatch('[0-9a-f]{40}',commit) or not re.fullmatch('[0-9a-f]{64}',digest) or not isinstance(size,int) or size <= 0:
            raise ValueError('ModelScope did not provide sam3.pt with a pinned revision, SHA256 and size')
        expected = dict(source='modelscope', repo='facebook/sam3', revision=commit,
                        filename='sam3.pt', sha256=digest, size=size)
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
        if offset < size:
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


def command(*args):
    return subprocess.run(args,cwd=ROOT,text=True,capture_output=True)


def check(root, probe=True):
    failures = []
    def report(name, ok, detail):
        print(('OK   ' if ok else 'NEED ')+name+': '+detail)
        if not ok:
            failures.append(name)
    try:
        result = command('docker','info','--format','{{json .Runtimes}}')
        available = result.returncode == 0
        runtimes = json.loads(result.stdout) if available else {}
        report('Docker',available,'available' if available else 'start/configure Docker first')
        toolkit = 'nvidia' in runtimes
        report('NVIDIA Docker runtime',toolkit,'configured' if toolkit else TOOLKIT_PAGE)
        result = command('docker','compose','version')
        report('Docker Compose',result.returncode == 0,'available' if result.returncode == 0 else 'install the Docker Compose plugin')
        image = command('docker','image','inspect','agenticros-sam3:single').returncode == 0
        report('SAM3 image',image,'agenticros-sam3:single' if image else 'run bash scripts/prepare_sam3.sh')
        if image and toolkit and probe:
            result = command('docker','run','--rm','--network','none','--gpus','all','--cap-drop','ALL',
                '--security-opt','no-new-privileges:true','--entrypoint','/opt/inference/bin/python',
                'agenticros-sam3:single','-c',
                'import torch; assert torch.cuda.is_available(); print(torch.cuda.get_device_name(0)); x=torch.ones(1,device="cuda"); print((x+x).item())')
            report('Container CUDA',result.returncode == 0,result.stdout.strip() if result.returncode == 0 else
                   'GPU probe failed; verify driver/toolkit compatibility (no model inference performed)')
    except (OSError,ValueError):
        report('Docker',False,'Docker CLI/daemon unavailable or returned invalid data')
    checkpoint = root/'models/sam3.pt'
    present = checkpoint.is_file() and checkpoint.stat().st_size > 0
    report('SAM3 checkpoint',present,str(checkpoint) if present else 'download from '+MODEL_PAGE+' with bash scripts/prepare_sam3.sh')
    if present and (root/'models/download.json').is_file():
        metadata = json.loads((root/'models/download.json').read_text())
        matches = sha256(checkpoint) == metadata['sha256']
        report('Checkpoint SHA256',matches,'matches saved Hub metadata' if matches else 'differs from saved Hub metadata')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--domain-id',type=int,default=42)
    parser.add_argument('--download',action='store_true',help='download and verify sam3.pt from ModelScope facebook/sam3')
    parser.add_argument('--build',action='store_true',help='build the SAM3 Docker image; never start a live service')
    args = parser.parse_args()
    root = directories(args.domain_id)
    print('SAM3 data: '+str(root))
    if args.download:
        download(root/'models')
    if args.build:
        subprocess.run(['docker','compose','-f','compose.agent.yaml','build','sam3'],cwd=ROOT,check=True)
    failures = check(root)
    if failures:
        print('Setup incomplete: '+', '.join(failures))
        return 1
    print('Ready to start: bash agent.sh sam3 --domain-id '+str(args.domain_id))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except urllib.error.HTTPError as exc:
        # Do not print signed redirect URLs, response bodies or credentials.
        print('Checkpoint download failed: HTTP '+str(exc.code)+'. Check ModelScope availability/access at '+MODEL_PAGE,file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError:
        print('Checkpoint download failed: network/TLS error; partial data retained for retry',file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError:
        print('SAM3 image build failed; see build output above',file=sys.stderr)
        sys.exit(1)
    except (OSError,ValueError,KeyError) as exc:
        print('SAM3 setup failed: '+str(exc),file=sys.stderr)
        sys.exit(1)
