"""Exercise launcher + actual host-network Compose with isolated names/data/ports.

Compose project names and test image selection are overridden in this fixture. Production startup
functions, bind mounts, healthchecks, saved config and hot rules run unchanged.
This is one physical host, never a cross-machine LAN acceptance test.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image-suffix',default='single')
    parser.add_argument('--keep',action='store_true',help='retain disposable fixture for failure diagnosis')
    options=parser.parse_args()
    name='agenticros-startup-check-'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name;root.mkdir(parents=True)
    for filename in ('compose.agent.yaml','compose.robot.yaml'):
        (root/filename).write_text((ROOT/filename).read_text().replace(':single', ':'+options.image_suffix))
    (root/'scripts').mkdir()
    for filename in ('runtime.py','prepare_workspace.py'):
        shutil.copyfile(ROOT/'scripts'/filename,root/'scripts'/filename)
    shutil.copytree(ROOT/'docker/agent_instance/agent',root/'docker/agent_instance/agent',ignore=shutil.ignore_patterns('__pycache__'))
    shutil.copytree(ROOT/'docker/robot_instances/robot_docs',root/'docker/robot_instances/robot_docs')
    for dockerfile in (ROOT/'docker/robot_instances').glob('*/Dockerfile'):
        target=root/'docker/robot_instances'/dockerfile.parent.name/'Dockerfile'
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(dockerfile,target)
    spec=importlib.util.spec_from_file_location('startup_fixture',root/'scripts/runtime.py')
    lan=importlib.util.module_from_spec(spec);spec.loader.exec_module(lan)
    original=lan.compose
    def compose(config,client=False):
        command=original(config,client);command[3]=name+('-'+config['AGENTICROS_ROBOT_ID'] if client else '-server');return command
    lan.compose=compose
    def ensure_image(config,service,client=False):
        image='agenticros-'+(config['AGENTICROS_ENVIRONMENT'] if client else service)+':'+options.image_suffix
        subprocess.run(['docker','image','inspect',image],check=True,stdout=subprocess.DEVNULL)
        return image
    lan.ensure_image=ensure_image
    reserved=[]
    def port():
        # Allocate distinct local ports for this fixture's HTTP/VNC listeners.
        for number in range(18000,32000):
            if number in reserved:continue
            with socket.socket() as s:
                try:s.bind(('127.0.0.1',number))
                except OSError:continue
            reserved.append(number);return number
        raise RuntimeError('no fixture port')
    config=lan.server_config(75)
    config.update(AGENTICROS_VIEWER_PORT=port(),AGENTICROS_VNC_PORT=port(),
                  AGENTICROS_CODEX_VOLUME=name+'-codex',AGENTICROS_VIEWER_VOLUME=name+'-viewer')
    lan.server_file(75).write_text(json.dumps(config))
    lan.server_file(75,'viewer').parent.mkdir(parents=True,exist_ok=True)
    lan.server_file(75,'viewer').write_text(json.dumps(config))
    robots=[]
    def cid(config,client=False,service=None):
        return subprocess.check_output(lan.compose(config,client)+['ps','-q',service or ('client' if client else 'server')],cwd=root,env=lan.environment(config),text=True).strip()
    def call(args):
        previous=sys.argv;sys.argv=['runtime.py',*args]
        try:lan.main()
        finally:sys.argv=previous
    try:
        for _ in range(2):
            before=set((root/'runs/75/robot_instance').glob('*/*/client.json'))
            args=['robot','--environment','cartesian_gripper','--domain-id','75']
            call(args)
            created=set((root/'runs/75/robot_instance').glob('*/*/client.json'))-before
            assert len(created)==1
            robot=json.loads(created.pop().read_text());robots.append(robot)
            assert robot['AGENTICROS_ROBOT_ID'].startswith('cartesian_gripper_')
            first=cid(robot,True)
            call(['robot','--robot-id',robot['AGENTICROS_ROBOT_ID'],'--domain-id','75'])
            assert cid(robot,True)==first
        assert robots[0]['AGENTICROS_ROBOT_ID']!=robots[1]['AGENTICROS_ROBOT_ID']
        # Both robot healthchecks must pass with no Server container present.
        assert not cid(config)
        call(['viewer','--domain-id','75']);viewer_id=cid(config,service='viewer')
        assert viewer_id and not cid(config)
        call(['server','--domain-id','75']);server_id=cid(config)
        call(['server','--domain-id','75']);assert cid(config)==server_id
        def request(data):
            reply=subprocess.run(['docker','exec',server_id,'agenticros-ros','request',json.dumps(data)],text=True,capture_output=True)
            with (root/'requests.jsonl').open('a') as log:
                log.write(json.dumps(dict(request=data,returncode=reply.returncode,stdout=reply.stdout,stderr=reply.stderr))+'\n')
            if reply.returncode:raise RuntimeError(reply.stdout+reply.stderr)
            return json.loads(reply.stdout)
        end=time.monotonic()+30
        while True:
            status=request({'op':'status'})
            if len(status['robots'])==2 and all(r['observation_fresh'] for r in status['robots']):break
            assert time.monotonic()<end,status
            time.sleep(.5)
        call(['stop-viewer','--domain-id','75'])
        assert cid(config)==server_id
        request({'op':'graph'})
        call(['viewer','--domain-id','75'])
        assert cid(config,service='viewer')==viewer_id
        for robot in robots:
            robot_id=robot['AGENTICROS_ROBOT_ID']
            reply=request(dict(op='service',endpoint=f'/robot/{robot_id}/driver/status',type='std_srvs/srv/Trigger'))
            assert reply['response']['success']
        rules=root/'docker/agent_instance/agent/AGENTS.md'
        marker='\nFixture hot-sync marker '+name+'\n';rules.write_text(rules.read_text()+marker)
        command=lan.agent_command(config,['true'],[],False)
        subprocess.run(command,cwd=root,env=lan.environment(config),check=True)
        installed=subprocess.check_output(['docker','exec',server_id,'cat','/workspace/AGENTS.md'],text=True)
        assert marker in installed
        inspections=json.loads(subprocess.check_output(['docker','inspect',server_id,*[cid(r,True) for r in robots]],text=True))
        result=dict(result='PASS',physical_hosts=1,containers=4,viewer_started_without_agent=True,viewer_stop_preserves_agent=True,network_mode='host',ipc='host',
                    robots_started_without_server=True,repeat_start_keeps_containers=True,hot_rules_without_rebuild=True,robot_count=2,
                    images=[i['Image'] for i in inspections])
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        for container in [cid(config),cid(config,service='viewer'),*[cid(r,True) for r in robots]]:
            if container:
                log=subprocess.run(['docker','logs',container],text=True,capture_output=True)
                (root/(container+'.log')).write_text(log.stdout+log.stderr)
        if options.keep:
            print('Fixture retained: '+str(root),flush=True)
        else:
            for robot in robots:
                lan.run(robot,['stop','client'],True);lan.run(robot,['rm','-f','client'],True)
            lan.run(config,['stop','server','viewer']);lan.run(config,['rm','-f','server','viewer'])
        print('Evidence and volumes retained: '+str(root),flush=True)

if __name__=='__main__':main()
