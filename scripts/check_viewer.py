#!/usr/bin/env python3
"""Isolated Docker viewer integration; no robot motion commands.

Creates two fresh simulations, verifies live discovery, VNC authentication,
repeat startup and session replacement. Stops its fixtures, preserving evidence.
"""
import json
from pathlib import Path
import secrets
import socket
import subprocess
import time
from types import SimpleNamespace
import urllib.request

import lan


def wait_for(check, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(1)
    raise RuntimeError('Viewer readiness condition timed out')


def main():
    instance = 'viewer-test-' + secrets.token_hex(4)
    config = lan.server_config(instance, '127.0.0.1')
    root = lan.folder(instance)
    clients = []

    def container():
        return subprocess.check_output(lan.compose(config) + ['ps', '-q', 'instance'],
                                       env=lan.environment(config), cwd=lan.ROOT, text=True).strip()

    def state():
        result = subprocess.run(['docker', 'exec', container(), 'cat', '/tmp/viewer-status.json'],
                                text=True, capture_output=True)
        return json.loads(result.stdout) if result.returncode == 0 else {}

    def ready():
        current = state()
        return current if len(current.get('robots', {})) == 2 and current.get('rviz_pid') else None

    def roster():
        with urllib.request.urlopen(lan.health(config) + '/robots', timeout=3) as response:
            return json.load(response)

    try:
        lan.ensure_server(config)
        for robot in ('robot_a', 'robot_b'):
            args = SimpleNamespace(join=lan.join_code(config), instance=None, robot_id=robot,
                                   server=None, host='127.0.0.1', observation_port=None, stop=False)
            client = lan.client_config(args)
            clients.append(client)
            lan.ensure_image(client, 'client', client=True)
            lan.run(client, ['up', '-d', '--no-build', '--pull', 'never', '--no-recreate', '--wait', 'client'], True)
        config = lan.ensure_viewer(config)
        current = wait_for(ready)
        with urllib.request.urlopen('http://127.0.0.1:' + str(config['AGENTICROS_VIEWER_PORT']) + '/vnc.html', timeout=3) as response:
            assert b'noVNC' in response.read()
        with socket.create_connection(('127.0.0.1', config['AGENTICROS_VNC_PORT']), timeout=3) as stream:
            banner = stream.makefile('rb')
            version = banner.read(12)
            assert version.startswith(b'RFB ')
            stream.sendall(version)
            count = banner.read(1)[0]
            security_types = banner.read(count)
            assert 2 in security_types and 1 not in security_types, 'VNC must require authentication'
        viewer = container()
        rviz_config = subprocess.check_output(['docker', 'exec', viewer, 'cat', '/tmp/current.rviz'], text=True)
        assert '/compressed' in rviz_config, 'Current client image must expose the compressed preview'
        password = subprocess.check_output(['docker', 'exec', viewer, 'cat', '/viewer-auth/password'])
        before = roster()
        lan.ensure_viewer(config)
        assert container() == viewer
        assert subprocess.check_output(['docker', 'exec', viewer, 'cat', '/viewer-auth/password']) == password
        assert roster()['epoch'] == before['epoch']
        # Restart only the desktop; the registry epoch, brain lock and client sessions survive.
        lan.control(config, 'viewer-stop')
        assert roster()['epoch'] == before['epoch']
        assert all(r['connected'] for r in roster()['robots'])
        lan.ensure_viewer(config)
        wait_for(lambda: ready() if state().get('rviz_pid') != current['rviz_pid'] else None)
        assert container() == viewer
        assert roster()['epoch'] == before['epoch']
        assert subprocess.check_output(['docker', 'exec', viewer, 'cat', '/viewer-auth/password']) == password
        # Exiting an interactive brain does not terminate communication or visualization.
        attach = ['docker', 'exec', viewer, '/usr/local/bin/agent-entrypoint', 'python3',
                  '/opt/instance/runtime.py', 'attach']
        brain = subprocess.Popen(attach + ['sleep', '6'])
        time.sleep(1)
        duplicate = subprocess.run(attach + ['true'], capture_output=True, text=True)
        assert duplicate.returncode != 0 and 'Codex' in duplicate.stderr
        assert brain.wait(timeout=10) == 0
        assert subprocess.run(attach + ['true']).returncode == 0
        assert roster()['epoch'] == before['epoch']
        wait_for(ready)
        old = current['robots']['robot_a']
        # Only restart this test's idle fixture, never an existing user client.
        lan.run(clients[0], ['stop', 'client'], True)
        wait_for(lambda: 'robot_a' not in state().get('robots', {}), 20)
        lan.run(clients[0], ['up', '-d', '--no-build', '--pull', 'never', '--no-recreate', '--wait', 'client'], True)
        wait_for(lambda: state().get('robots', {}).get('robot_a') not in (None, old))
        result = dict(instance=instance, web_port=config['AGENTICROS_VIEWER_PORT'],
                      checks='PASS', motion_test=False, compressed_preview=True, state=state())
        (root / 'viewer-check.json').write_text(json.dumps(result, indent=2))
        print('PASS:', root / 'viewer-check.json')
    finally:
        for client in clients:
            lan.run(client, ['stop', 'client'], True)
            lan.run(client, ['rm', '-f', 'client'], True)
        lan.run(config, ['stop', 'instance'])
        lan.run(config, ['rm', '-f', 'instance'])


if __name__ == '__main__':
    main()
