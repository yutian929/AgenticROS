"""Fail closed before starting a disposable, memory-limited Isaac instance."""
import json
import subprocess
from pathlib import Path

GPU_MEMORY_ARGS = ['--memory=16g', '--memory-swap=16g']


def require_idle_gpu():
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
    if ids:
        containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids]))
        busy = []
        for container in containers:
            config = container['HostConfig']
            requests = config.get('DeviceRequests') or []
            env = container.get('Config', {}).get('Env') or []
            if (any('gpu' in group for request in requests for group in request.get('Capabilities', []))
                    or config.get('Runtime') == 'nvidia'
                    or any('/dev/nvidia' in device.get('PathOnHost', '') for device in config.get('Devices') or [])
                    or any(value.startswith('NVIDIA_VISIBLE_DEVICES=') and value.split('=', 1)[1] not in ('', 'none', 'void') for value in env)):
                busy.append(container['Name'].lstrip('/'))
        if busy:
            raise RuntimeError('GPU test requires idle containers; leave existing bodies untouched: ' + ', '.join(busy))
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    available_kib = int(memory['MemAvailable'].split()[0])
    if available_kib < 18 * 1024 * 1024:
        raise RuntimeError(f'GPU test requires at least 18 GiB available RAM; found {available_kib / 1024**2:.1f} GiB')
