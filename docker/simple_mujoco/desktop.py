"""Run the simulator and standard ROS/RViz/noVNC processes as a non-root user.

A GUI close/crash restarts only RViz. Any other process failure stops the container;
this supervisor never restarts the physical driver or silently resets its session.
"""
import json
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import threading
import time

STOP = threading.Event()
PROCESSES = {}
ROOT = Path('/opt/agenticros')
AUTH = Path('/viewer-auth')


def spawn(name, command):
    print('Starting ' + name, flush=True)
    process = subprocess.Popen(command, start_new_session=True)
    PROCESSES[name] = process
    return process


def wait_display(timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not STOP.is_set():
        if PROCESSES['display'].poll() is not None:
            raise RuntimeError('Virtual display exited')
        check = subprocess.run(['xdpyinfo'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if check.returncode == 0:
            return
        STOP.wait(.1)
    raise RuntimeError('Virtual display did not become ready')


def prepare_password():
    AUTH.mkdir(exist_ok=True)
    AUTH.chmod(0o700)
    password = AUTH / 'password'
    if not password.exists():
        # VNC authentication uses at most eight bytes. No secret is logged or put in a URL.
        with os.fdopen(os.open(password, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            f.write(secrets.token_urlsafe(6) + '\n')
    password.chmod(0o600)
    value = password.read_text().strip()
    if len(value) != 8 or not value.isascii() or not value.isprintable():
        raise RuntimeError('Viewer password must contain eight printable ASCII characters')
    subprocess.run(['x11vnc', '-storepasswd', value, str(AUTH / 'passwd')],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (AUTH / 'passwd').chmod(0o600)


def shutdown():
    # Stop the driver first, allowing it to reconcile an accepted action and hold.
    order = ['driver', 'rviz', 'model', 'web', 'vnc', 'window-manager', 'display']
    for name in order:
        process = PROCESSES.get(name)
        if process is None or process.poll() is not None:
            continue
        try:
            os.killpg(process.pid, signal.SIGINT if name in ('driver', 'rviz', 'model') else signal.SIGTERM)
            process.wait(timeout=12 if name == 'driver' else 5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            pass


def main():
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    runtime = Path(os.environ['XDG_RUNTIME_DIR'])
    runtime.mkdir(mode=0o700, exist_ok=True)
    runtime.chmod(0o700)
    size = os.getenv('AGENTICROS_DESKTOP_SIZE', '1440x900')
    if not re.fullmatch(r'[0-9]{3,4}x[0-9]{3,4}', size):
        raise ValueError('Invalid AGENTICROS_DESKTOP_SIZE')
    width, height = map(int, size.split('x'))
    if not (640 <= width <= 2560 and 480 <= height <= 1440):
        raise ValueError('Desktop size must be between 640x480 and 2560x1440')
    prepare_password()
    rviz_command = ['rviz2', '-d', str(ROOT / 'observation.rviz')]
    try:
        spawn('display', ['Xvfb', os.environ['DISPLAY'], '-screen', '0', size + 'x24',
                          '-nolisten', 'tcp', '+extension', 'GLX', '+render', '-noreset'])
        wait_display()
        spawn('window-manager', ['openbox', '--config-file', str(ROOT / 'openbox.xml')])
        spawn('driver', ['/opt/body-venv/bin/python', '/opt/body/driver.py'])
        spawn('model', ['ros2', 'run', 'robot_state_publisher', 'robot_state_publisher',
                        '--ros-args', '-p', 'robot_description:=' + (ROOT / 'robot.urdf').read_text(),
                        '-r', 'joint_states:=/driver/joint_states'])
        spawn('vnc', ['x11vnc', '-display', os.environ['DISPLAY'], '-rfbport', '5900',
                      '-localhost', '-rfbauth', str(AUTH / 'passwd'), '-forever', '-shared',
                      '-noxdamage', '-noshm', '-nolookup', '-quiet'])
        spawn('web', ['websockify', '--web', '/usr/share/novnc', '8080', '127.0.0.1:5900'])
        spawn('rviz', rviz_command)
        last_rviz_restart = time.monotonic()
        while not STOP.wait(.5):
            for name, process in list(PROCESSES.items()):
                if process.poll() is None:
                    continue
                if name == 'rviz' and time.monotonic()-last_rviz_restart > 3:
                    print('RViz exited; reopening the viewer without restarting the driver', flush=True)
                    spawn('rviz', rviz_command)
                    last_rviz_restart = time.monotonic()
                else:
                    raise RuntimeError(name + ' exited with code ' + str(process.returncode))
            status = dict(updated=time.time(), pids={name: p.pid for name, p in PROCESSES.items()})
            temporary = Path('/tmp/desktop-status.tmp')
            temporary.write_text(json.dumps(status))
            temporary.replace('/tmp/desktop-status.json')
    finally:
        shutdown()


if __name__ == '__main__':
    main()
