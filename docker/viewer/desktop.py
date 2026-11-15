"""Supervise an instance's visualization only. Never launch or restart a robot."""
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import threading
import time

STOP = threading.Event()
PROCESSES = {}
AUTH = Path('/viewer-auth')


def prepare_password():
    AUTH.mkdir(exist_ok=True)
    AUTH.chmod(0o700)
    password = AUTH/'password'
    if not password.exists():
        with os.fdopen(os.open(password, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'w') as stream:
            stream.write(secrets.token_urlsafe(6)+'\n')
    password.chmod(0o600)
    value = password.read_text().strip()
    if len(value) != 8 or not value.isascii() or not value.isprintable():
        raise ValueError('Invalid viewer password')
    subprocess.run(['x11vnc', '-storepasswd', value, str(AUTH/'passwd')], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (AUTH/'passwd').chmod(0o600)


def spawn(name, command):
    PROCESSES[name] = subprocess.Popen(command, start_new_session=True)


def main():
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    Path(os.environ['XDG_RUNTIME_DIR']).mkdir(mode=0o700, exist_ok=True)
    prepare_password()
    vnc = os.environ['AGENTICROS_VNC_PORT']
    web = os.environ['AGENTICROS_VIEWER_BIND']+':'+os.environ['AGENTICROS_VIEWER_PORT']
    try:
        spawn('display', ['Xvfb', os.environ['DISPLAY'], '-screen', '0', '1440x900x24',
                          '-nolisten', 'tcp', '+extension', 'GLX', '+render', '-noreset'])
        for _ in range(150):
            if subprocess.run(['xdpyinfo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
                break
            if STOP.wait(.1) or PROCESSES['display'].poll() is not None:
                raise RuntimeError('Display failed')
        else:
            raise RuntimeError('Display readiness timeout')
        spawn('window-manager', ['openbox', '--config-file', '/opt/viewer/openbox.xml'])
        spawn('vnc', ['x11vnc', '-display', os.environ['DISPLAY'], '-rfbport', vnc,
                      '-localhost', '-rfbauth', str(AUTH/'passwd'), '-forever', '-shared',
                      '-noxdamage', '-noshm', '-nolookup', '-quiet'])
        spawn('web', ['websockify', '--web', '/usr/share/novnc', web, '127.0.0.1:'+vnc])
        spawn('viewer', ['python3', '/opt/viewer/viewer.py'])
        while not STOP.wait(.5):
            for name, process in PROCESSES.items():
                if process.poll() is not None:
                    raise RuntimeError(name+' exited')
            path = Path('/tmp/desktop-status.tmp')
            path.write_text(json.dumps(dict(updated=time.time(), pids={n:p.pid for n,p in PROCESSES.items()})))
            path.replace('/tmp/desktop-status.json')
    finally:
        for name in ('viewer','web','vnc','window-manager','display'):
            process = PROCESSES.get(name)
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                except ProcessLookupError:
                    pass


if __name__ == '__main__':
    main()
