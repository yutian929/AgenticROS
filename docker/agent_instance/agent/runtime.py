"""One interactive Agent and one persistent ROS client worker."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys

SOCKET = '/tmp/agenticros-instance.sock'


def request(command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(60)
        stream.connect(SOCKET)
        stream.sendall((command+'\n').encode())
        data = stream.makefile('rb').readline(65536)
    response = json.loads(data)
    if 'error' in response:
        raise RuntimeError(response['error'])
    return response


class Runtime:
    def __init__(self):
        self.processes = {}
        self.enabled = set()
        self.stopping = False

    def start(self, name):
        process = self.processes.get(name)
        if process and process.poll() is None:
            return
        self.enabled.add(name)
        commands = {'clients': ['bash', '-c', 'source /etc/profile.d/agenticros.sh; if [ -f /workspace/ros_ws/install/setup.bash ]; then source /workspace/ros_ws/install/setup.bash; fi; exec python3 /opt/instance/ros_client.py serve']}
        env = os.environ.copy()
        # Docker captures service logs; the interactive Codex terminal stays separate.
        self.processes[name] = subprocess.Popen(commands[name], env=env, start_new_session=True)

    def stop(self, name):
        self.enabled.discard(name)
        process = self.processes.get(name)
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def status(self):
        return {name: dict(enabled=name in self.enabled, running=p.poll() is None, pid=p.pid)
                for name, p in self.processes.items()}

    def command(self, command):
        if command == 'clients-reload':
            self.stop('clients')
            self.start('clients')
            return self.status()
        if command == 'status':
            return self.status()
        raise ValueError('unknown Agent instance command')

    def serve(self):
        def stopping(*_):
            self.stopping = True
        signal.signal(signal.SIGTERM, stopping)
        signal.signal(signal.SIGINT, stopping)
        # Container restart may retain /tmp; the previous supervisor is already gone.
        Path(SOCKET).unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(SOCKET)
            os.chmod(SOCKET, 0o600)
            listener.listen(4)
            listener.settimeout(.5)
            try:
                self.start('clients')
                while not self.stopping:
                    if self.processes['clients'].poll() is not None:
                        self.start('clients')
                    try:
                        stream, _ = listener.accept()
                    except socket.timeout:
                        continue
                    with stream:
                        stream.settimeout(2)
                        try:
                            command = stream.makefile('rb').readline(256).decode().strip()
                            response = self.command(command)
                        except (OSError, ValueError, RuntimeError) as exc:
                            response = {'error': str(exc)}
                        try:
                            stream.sendall((json.dumps(response)+'\n').encode())
                        except OSError:
                            pass
            finally:
                self.stop('clients')
                Path(SOCKET).unlink(missing_ok=True)


def health():
    state = request('status')
    assert state['clients']['running']
    from ros_client import request as client_request
    assert 'topics' in client_request(dict(op='graph',timeout=1))


def attach(command):
    # Keep the lock in this parent, independent of how Codex execs its subprocesses.
    with open('/tmp/agenticros-brain.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('此 Agent instance 已有一个 Codex 会话；请先退出原会话。') from None
        env = os.environ.copy()
        with subprocess.Popen(command, env=env) as process:
            signal.signal(signal.SIGINT, lambda *_: None)  # terminal also signals child
            signal.signal(signal.SIGTERM, lambda *_: process.terminate())
            return process.wait()


def main():
    command = sys.argv[1]
    if command == 'serve':
        Runtime().serve()
    elif command == 'health':
        health()
    elif command == 'attach':
        return attach(sys.argv[2:])
    else:
        response = request(command)
        if command == 'status':
            print(json.dumps(response))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
