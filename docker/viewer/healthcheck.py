import json
import os
from pathlib import Path
import socket
import time
import urllib.request

status = json.loads(Path('/tmp/desktop-status.json').read_text())
assert time.time()-status['updated'] < 5
for pid in status['pids'].values():
    os.kill(pid, 0)
assert time.time()-Path('/tmp/viewer-status.json').stat().st_mtime < 10
with socket.create_connection(('127.0.0.1', int(os.environ['AGENTICROS_VNC_PORT'])), timeout=3) as stream:
    assert stream.recv(12).startswith(b'RFB ')
host = os.environ['AGENTICROS_VIEWER_BIND']
with urllib.request.urlopen('http://'+host+':'+os.environ['AGENTICROS_VIEWER_PORT']+'/vnc.html', timeout=3) as response:
    assert b'noVNC' in response.read()
