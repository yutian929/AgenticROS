"""Check live sensor production, GUI processes, noVNC and the authenticated RFB server."""
import json
import os
from pathlib import Path
import socket
import time
import urllib.request

ready = Path('/tmp/body-camera-ready')
assert ready.exists() and time.time()-ready.stat().st_mtime < 15, 'Camera stalled'
status = json.loads(Path('/tmp/desktop-status.json').read_text())
assert time.time()-status['updated'] < 5, 'Supervisor stalled'
for pid in status['pids'].values():
    os.kill(pid, 0)
with socket.create_connection(('127.0.0.1', 5900), timeout=3) as client:
    assert client.recv(12).startswith(b'RFB ')
with urllib.request.urlopen('http://127.0.0.1:8080/vnc.html', timeout=3) as response:
    assert b'noVNC' in response.read()
