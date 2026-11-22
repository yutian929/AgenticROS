#!/usr/bin/env python3
"""Isolated DDS/action/image test with two containers and a synthetic backend.

No weights, GPU, live body or credentials. Evidence remains under runs/checks.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='agenticros-valen:transport-test')
    parser.add_argument('--agent-image', help='test public IDL build with the actual generic Agent image')
    parser.add_argument('--host-network', action='store_true')
    parser.add_argument('--domain-id', type=int, default=74)
    args = parser.parse_args()
    if not 0 <= args.domain_id <= 101:
        parser.error('domain-id must be 0..101')
    name = 'agenticros-valen-check-'+uuid.uuid4().hex[:8]
    root = ROOT/'runs/checks'/name
    workspace, records = root/'workspace', root/'records'
    workspace.mkdir(parents=True)
    workspace.chmod(0o2777)
    records.mkdir()
    names = [name+'-service', name+'-agent']
    if not args.host_network:
        docker('network', 'create', name, capture_output=True)
    try:
        for container, mount in zip(names, [(records, '/records'), (workspace, '/workspace')]):
            actual_agent = container == names[1] and args.agent_image
            user = ['--group-add', str(os.getgid())] if actual_agent else ['--user', f'{os.getuid()}:{os.getgid()}']
            service_mount = ['--read-only', '--tmpfs', '/tmp'] if container == names[0] else []
            docker('run', '-d', '--name', container, '--network', 'host' if args.host_network else name,
                '--ipc', 'host' if args.host_network and container == names[1] else 'private', *user,
                *service_mount,
                '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
                '-e', 'HOME=/tmp', '-e', 'ROS_LOG_DIR=/tmp/ros_logs', '-e', f'ROS_DOMAIN_ID={args.domain_id}',
                '-e', 'RMW_FASTRTPS_PUBLICATION_MODE=ASYNCHRONOUS',
                '--mount', f'type=bind,src={mount[0]},dst={mount[1]}', '--entrypoint', '/bin/bash',
                args.agent_image if actual_agent else args.image, '-c', 'exec sleep infinity', capture_output=True)
        inspections = [json.loads(docker('inspect', item, capture_output=True).stdout)[0] for item in names]
        assert len({x['NetworkSettings']['SandboxKey'] for x in inspections}) == (1 if args.host_network else 2)
        assert inspections[0]['HostConfig']['IpcMode'] == 'private'
        assert inspections[0]['HostConfig']['ReadonlyRootfs']
        for item, destination in zip(inspections, ['/records', '/workspace']):
            mounts = {m['Destination']:m for m in item['Mounts']}
            assert set(mounts) == {destination}
            assert mounts[destination]['RW']
        (root/'topology.json').write_text(json.dumps(dict(physical_hosts=1, host_network=args.host_network,
            domain_id=args.domain_id, model_inference=False, mounts=[x['Mounts'] for x in inspections]), indent=2))
        docker('exec', '-i', names[0], 'python3', '-c',
               'import sys; from pathlib import Path; Path("/tmp/fixture.py").write_text(sys.stdin.read())',
               input=(ROOT/'tests/valen_fixture.py').read_text())
        if args.agent_image:
            destination = workspace/'ros_ws/src/agenticros_decision_interfaces'
            shutil.copytree(ROOT/'docker/agent_instance/valen/interfaces', destination)
            (workspace/'ros_ws').chmod(0o2777)
            docker('exec', names[1], 'bash', '-c',
                'source /etc/profile.d/agenticros.sh; cd /workspace/ros_ws; colcon build --packages-select agenticros_decision_interfaces')
            client_command = ['bash', '-c', 'source /etc/profile.d/agenticros.sh; source /workspace/ros_ws/install/setup.bash; PYTHONPATH=/opt/instance:$PYTHONPATH python3 -']
        else:
            docker('cp', str(ROOT/'docker/agent_instance/agent/ros_client.py'), names[1]+':/tmp/ros_client.py')
            client_command = ['/usr/local/bin/valen-entrypoint', 'python3', '-']
        docker('exec', '-d', '-e', 'PYTHONPATH=/opt/valen', names[0], '/usr/local/bin/valen-entrypoint',
            'bash', '-c', 'exec python3 /tmp/fixture.py > /records/service.log 2>&1')
        docker('exec', '-i', '-e', 'PYTHONPATH=/tmp', names[1], *client_command,
            input=(ROOT/'tests/valen_probe.py').read_text())
        docker('exec', names[0], '/usr/local/bin/valen-entrypoint', 'python3', '/opt/valen/healthcheck.py')
        summary = json.loads((workspace/'evidence/summary.json').read_text())
        session = records/summary['session']
        success = session/summary['successful_goal']
        for filename in ('request.json', 'model.json', 'input.png', 'source.json', 'result.json', 'events.jsonl'):
            assert (success/filename).is_file(), filename
        outcomes = [json.loads(path.read_text())['code'] for path in session.glob('*/result.json')]
        assert {'OK', 'CANCELLED', 'INFERENCE_FAILED', 'INVALID_REQUEST', 'FRAME_TIMEOUT', 'STALE_FRAME'} <= set(outcomes), outcomes
        (root/'result.json').write_text(json.dumps(dict(result='PASS', physical_hosts=1,
            separate_containers=True, agent_image=args.agent_image, real_valen_inference=False), indent=2))
        print('Evidence: '+str(root))
    finally:
        for container in names:
            subprocess.run(['docker', 'rm', '-f', container], capture_output=True)
        if not args.host_network:
            subprocess.run(['docker', 'network', 'rm', name], capture_output=True)
        print('Retained: '+str(root))


if __name__ == '__main__':
    main()
