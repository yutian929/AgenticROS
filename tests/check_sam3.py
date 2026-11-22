#!/usr/bin/env python3
"""Isolated separate-container SAM3 protocol test, not real model/task evaluation.

Build transport target first. No live robot, GPU, weights or credentials used.
Evidence is retained under runs/checks; only this test's containers/network removed.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker',*args], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='agenticros-sam3:transport-test')
    parser.add_argument('--agent-image',help='also validate public IDL build and generic clients in the actual Agent image')
    parser.add_argument('--host-network', action='store_true', help='production host network, private service IPC and host Agent IPC')
    parser.add_argument('--domain-id', type=int, default=73, help='unused test domain; never use a live domain')
    args = parser.parse_args()
    if not 0 <= args.domain_id <= 101:
        parser.error('domain-id must be 0..101')
    name = 'agenticros-sam3-check-'+uuid.uuid4().hex[:8]
    network = name
    root = ROOT/'runs/checks'/name
    workspace, records = root/'workspace', root/'records'
    workspace.mkdir(parents=True)
    workspace.chmod(0o2777)
    records.mkdir()
    names = [name+'-service',name+'-agent']
    if not args.host_network:
        docker('network','create',name,capture_output=True)
    try:
        for container, mount in zip(names, [(records,'/records'),(workspace,'/workspace')]):
            actual_agent = container == names[1] and args.agent_image
            user = ['--group-add',str(os.getgid())] if actual_agent else ['--user',f'{os.getuid()}:{os.getgid()}']
            docker('run','-d','--name',container,'--network','host' if args.host_network else name,
                   '--ipc','host' if args.host_network and container == names[1] else 'private',
                   *user, '--cap-drop','ALL','--security-opt','no-new-privileges:true',
                   '-e','HOME=/tmp','-e','ROS_LOG_DIR=/tmp/ros_logs','-e',f'ROS_DOMAIN_ID={args.domain_id}',
                   '-e','RMW_FASTRTPS_PUBLICATION_MODE=ASYNCHRONOUS',
                   '--mount',f'type=bind,src={mount[0]},dst={mount[1]}','--entrypoint','/bin/bash',
                   args.agent_image if actual_agent else args.image,'-c','exec sleep infinity',capture_output=True)
        inspections = [json.loads(docker('inspect',name,capture_output=True).stdout)[0] for name in names]
        namespace_count = len({item['NetworkSettings']['SandboxKey'] for item in inspections})
        assert namespace_count == (1 if args.host_network else 2)
        assert inspections[0]['HostConfig']['IpcMode'] == 'private'
        assert inspections[1]['HostConfig']['IpcMode'] == ('host' if args.host_network else 'private')
        for item, destination in zip(inspections, ['/records','/workspace']):
            assert [m['Destination'] for m in item['Mounts']] == [destination]
        (root/'topology.json').write_text(json.dumps(dict(physical_hosts=1,network_namespaces=namespace_count,
            host_network=args.host_network,domain_id=args.domain_id,
            model_inference=False,mounts=[item['Mounts'] for item in inspections]),indent=2))
        docker('cp',str(ROOT/'tests/sam3_fixture.py'),names[0]+':/tmp/fixture.py')
        if args.agent_image:
            assert inspections[1]['Config']['User'] == 'agent'
            destination = workspace/'ros_ws/src/agenticros_perception_interfaces'
            shutil.copytree(ROOT/'docker/agent_instance/sam3/interfaces',destination)
            (workspace/'ros_ws').chmod(0o2777)
            docker('exec',names[1],'bash','-c',
                   'source /etc/profile.d/agenticros.sh; cd /workspace/ros_ws; colcon build --packages-select agenticros_perception_interfaces')
            client_command = ['bash','-c','source /etc/profile.d/agenticros.sh; source /workspace/ros_ws/install/setup.bash; PYTHONPATH=/opt/instance:$PYTHONPATH python3 -']
        else:
            docker('cp',str(ROOT/'docker/agent_instance/agent/ros_client.py'),names[1]+':/tmp/ros_client.py')
            client_command = ['/usr/local/bin/sam3-entrypoint','python3','-']
        docker('exec','-d','-e','PYTHONPATH=/opt/sam3',names[0],'/usr/local/bin/sam3-entrypoint',
               'bash','-c','exec python3 /tmp/fixture.py > /records/service.log 2>&1')
        docker('exec','-i','-e','PYTHONPATH=/tmp',names[1],*client_command,
               input=(ROOT/'tests/sam3_probe.py').read_text())
        docker('exec',names[0],'/usr/local/bin/sam3-entrypoint','python3','/opt/sam3/healthcheck.py')
        summary = json.loads((workspace/'evidence/summary.json').read_text())
        session = records/summary['session']
        success = session/summary['successful_goal']
        for filename in ['request.json','source.json','input.png','overlay.png','masks/0.png','result.json','events.jsonl']:
            assert (success/filename).is_file(), filename
        outcomes = [json.loads(path.read_text())['code'] for path in session.glob('*/result.json')]
        assert {'OK','CANCELLED','INFERENCE_FAILED','INVALID_REQUEST','FRAME_TIMEOUT','STALE_FRAME'} <= set(outcomes), outcomes
        (root/'result.json').write_text(json.dumps(dict(result='PASS',physical_hosts=1,
            separate_containers=True,agent_image=args.agent_image,real_sam3_inference=False),indent=2))
        print('Evidence: '+str(root))
    finally:
        for name in names:
            subprocess.run(['docker','rm','-f',name],capture_output=True)
        if not args.host_network:
            subprocess.run(['docker','network','rm',network],capture_output=True)
        print('Retained: '+str(root))

if __name__ == '__main__':
    main()
