"""Disposable scanner physics/render and separate-container native ROS checks."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *map(str, args)], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agenticros-aloha_tabletop:scanner-test')
    parser.add_argument('--agent-image', default='agenticros-server:single')
    parser.add_argument('--alignment', action='store_true', help='also compile public IDL and check aligned actions/frames from the peer')
    parser.add_argument('--layout', action='store_true', help='also verify parallel mounts, neutral collision clearance and head-camera projection')
    args = parser.parse_args()
    name = 'agenticros-aloha-scanner-'+secrets.token_hex(4)
    evidence = ROOT/'runs/checks'/name
    evidence.mkdir(parents=True)
    containers = []
    network_created = False
    try:
        physics = name+'-physics'
        command = '/opt/body-venv/bin/python /tmp/scanner_physics.py'
        if args.layout:
            command += ' && /opt/body-venv/bin/python /tmp/layout_physics.py'
        docker('create', '--name', physics, '--network', 'none', '--entrypoint', '/bin/bash',
               args.image, '-c', command, capture_output=True)
        containers.append(physics)
        docker('cp', ROOT/'tests/aloha_scanner_physics.py', physics+':/tmp/scanner_physics.py')
        if args.layout:
            docker('cp', ROOT/'tests/aloha_layout_physics.py', physics+':/tmp/layout_physics.py')
        with (evidence/'physics.log').open('w') as log:
            result = subprocess.run(['docker', 'start', '-a', physics], stdout=log, stderr=subprocess.STDOUT, timeout=120)
        docker('cp', physics+':/tmp/test-evidence/.', evidence, capture_output=True)
        assert result.returncode == 0, 'physics failed; see '+str(evidence/'physics.log')
        docker('network', 'create', name, capture_output=True)
        network_created = True
        robot, peer = name+'-body', name+'-peer'
        workspace = evidence/'workspace'
        workspace.mkdir(mode=0o2777)
        workspace.chmod(0o2777)
        shutil.copyfile(ROOT/'tests/aloha_scanner_ros_probe.py', workspace/'scanner_ros_probe.py')
        common = ['--network', name, '--ipc', 'private', '--shm-size', '512m', '--cap-drop', 'ALL',
                  '--security-opt', 'no-new-privileges:true', '-e', 'ROS_DOMAIN_ID=87',
                  '-e', 'ROS_LOCALHOST_ONLY=0', '-e', 'FASTDDS_BUILTIN_TRANSPORTS=UDPv4']
        docker('create', '--name', robot, *common, '-e', 'AGENTICROS_ROBOT_ID=scanner_test', args.image,
               capture_output=True)
        containers.append(robot)
        docker('start', robot, capture_output=True)
        docker('create', '--name', peer, *common, '--group-add', str(os.getgid()),
               '-e', 'TEST_ROBOT_ID=scanner_test',
               '-e', 'TEST_ALIGNMENT='+('1' if args.alignment else '0'),
               '--mount', f'type=bind,src={workspace},dst=/workspace',
               '--mount', f'type=bind,src={ROOT}/docker/robot_instances/robot_docs,dst=/workspace/robot_docs,readonly',
               '--entrypoint', '/bin/bash', args.agent_image, '-c',
               'source /etc/profile.d/agenticros.sh && '+
               ('mkdir -p /workspace/ros_ws/src && '
                'cp -r /workspace/robot_docs/aloha_tabletop/interfaces /workspace/ros_ws/src/aloha_driver_interfaces && '
                'cd /workspace/ros_ws && colcon build --merge-install && source install/setup.bash && '
                if args.alignment else '')+
               'exec python3 /workspace/scanner_ros_probe.py',
               capture_output=True)
        containers.append(peer)
        with (evidence/'ros.log').open('w') as log:
            result = subprocess.run(['docker', 'start', '-a', peer], stdout=log, stderr=subprocess.STDOUT, timeout=180)
        assert result.returncode == 0, 'ROS failed; see '+str(evidence/'ros.log')
        inspections = json.loads(subprocess.check_output(['docker', 'inspect', robot, peer], text=True))
        assert not inspections[0]['Mounts'], 'robot must not mount test workspace or host sources'
        mounts = {m['Destination']: m for m in inspections[1]['Mounts']}
        assert set(mounts) == {'/workspace', '/workspace/robot_docs'}
        assert mounts['/workspace']['Source'] == str(workspace) and not mounts['/workspace/robot_docs']['RW']
        assert len({i['NetworkSettings']['SandboxKey'] for i in inspections}) == 2
        docker('exec', robot, 'test', '!', '-d', '/workspace', capture_output=True)
        (evidence/'topology.json').write_text(json.dumps(inspections, indent=2))
        shutil.copyfile(workspace/'ros.json', evidence/'ros.json')
        result = dict(result='PASS', physics=json.loads((evidence/'physics.json').read_text()),
                      communication=json.loads((evidence/'ros.json').read_text()),
                      scope='asset/contact physics and native ROS integration; no autonomous grasp or task evaluation')
        if args.layout:
            result['layout'] = json.loads((evidence/'layout.json').read_text())
        (evidence/'result.json').write_text(json.dumps(result, indent=2))
        print('PASS: '+str(evidence), flush=True)
    finally:
        for container in reversed(containers):
            with (evidence/(container.rsplit('-', 1)[-1]+'.log')).open('w') as log:
                subprocess.run(['docker', 'logs', container], stdout=log, stderr=subprocess.STDOUT)
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
        if network_created:
            subprocess.run(['docker', 'network', 'rm', name], capture_output=True)


if __name__ == '__main__':
    main()
