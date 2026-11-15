"""One instance, independent registry/desktop processes, one interactive brain."""
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import urllib.request

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
        commands = {'server': ['python3', '/opt/agenticros-server/registry.py'],
                    'viewer': ['bash', '/opt/viewer/entrypoint.sh']}
        env = os.environ.copy()
        if name == 'viewer':
            env.update(HOME='/tmp', ROS_LOG_DIR='/tmp/ros_logs')
            env.pop('AGENTICROS_JOIN_TOKEN', None)
        # Docker captures service logs; the interactive Codex terminal stays separate.
        self.processes[name] = subprocess.Popen(commands[name], env=env, start_new_session=True)

    def stop(self, name):
        self.enabled.discard(name)
        process = self.processes.get(name)
        if process and process.poll() is None:
            # Desktop supervises its own process groups and needs time to reap them.
            process.terminate()
            try:
                process.wait(timeout=45 if name == 'viewer' else 8)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                # A failed desktop shutdown must not leave orphaned graphical services.
                if name == 'viewer':
                    self.stopping = True
                    raise RuntimeError('desktop did not stop; stopping instance')

    def status(self):
        return {name: dict(enabled=name in self.enabled, running=p.poll() is None, pid=p.pid)
                for name, p in self.processes.items()}

    def command(self, command):
        if command == 'status':
            return self.status()
        if command in ('viewer-start', 'viewer-stop', 'server-start', 'server-stop'):
            name, operation = command.split('-')
            getattr(self, operation)(name)
            return self.status()
        raise ValueError('unknown instance command')

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
                self.start('server')
                self.start('viewer')
                while not self.stopping:
                    server = self.processes['server']
                    if 'server' in self.enabled and server.poll() is not None:
                        # Never silently change the registry epoch after a failure.
                        raise RuntimeError('registry exited; explicit instance restart required')
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
                try:
                    self.stop('viewer')
                finally:
                    self.stop('server')
                    Path(SOCKET).unlink(missing_ok=True)


def health():
    state = request('status')
    assert state['server']['running'] and state['server']['enabled']
    url = 'http://127.0.0.1:'+os.environ['AGENTICROS_REGISTRY_PORT']+'/health'
    with urllib.request.urlopen(url, timeout=2) as response:
        assert json.load(response)['instance_id'] == os.environ['AGENTICROS_INSTANCE_ID']
    if state['viewer']['enabled']:
        assert state['viewer']['running']
        subprocess.run(['python3', '/opt/viewer/healthcheck.py'], check=True, timeout=8)


def attach(command):
    # Keep the lock in this parent, independent of how Codex execs its subprocesses.
    with open('/tmp/agenticros-brain.lock', 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('此 instance 已有一个 Codex 会话；请先退出原会话。') from None
        env = os.environ.copy()
        env.pop('AGENTICROS_JOIN_TOKEN', None)
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
