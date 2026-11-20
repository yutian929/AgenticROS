"""Exercise launcher + actual host-network Compose with isolated names/data/ports.

Only the Compose project name is overridden in this fixture. Production startup
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
    name='agenticros-startup-check-'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name;root.mkdir(parents=True)
    for filename in ('compose.yaml','compose.client.yaml'):
        shutil.copyfile(ROOT/filename,root/filename)
    (root/'scripts').mkdir()
    for filename in ('lan.py','prepare_workspace.py'):
        shutil.copyfile(ROOT/'scripts'/filename,root/'scripts'/filename)
    shutil.copytree(ROOT/'docker/agent_instance/agent',root/'docker/agent_instance/agent',ignore=shutil.ignore_patterns('__pycache__'))
    spec=importlib.util.spec_from_file_location('startup_fixture',root/'scripts/lan.py')
    lan=importlib.util.module_from_spec(spec);spec.loader.exec_module(lan)
    original=lan.compose
    def compose(config,client=False):
        command=original(config,client);command[3]=name+('-'+config['AGENTICROS_ROBOT_ID'] if client else '-server');return command
    lan.compose=compose
    reserved=[]
    def port():
        # DDS TCP needs a non-ephemeral fixed port below 32768.
        for number in range(18000,32000):
            if number in reserved:continue
            with socket.socket() as s:
                try:s.bind(('127.0.0.1',number))
                except OSError:continue
            reserved.append(number);return number
        raise RuntimeError('no fixture port')
    config=lan.server_config('127.0.0.1',port(),port(),75,'tcp')
    config.update(AGENTICROS_VIEWER_PORT=port(),AGENTICROS_VNC_PORT=port(),
                  AGENTICROS_CODEX_VOLUME=name+'-codex',AGENTICROS_VIEWER_VOLUME=name+'-viewer')
    lan.server_file().write_text(json.dumps(config))
    robots=[]
    def cid(config,client=False):
        return subprocess.check_output(lan.compose(config,client)+['ps','-q'],cwd=root,env=lan.environment(config),text=True).strip()
    def call(args):
        previous=sys.argv;sys.argv=['lan.py',*args]
        try:lan.main()
        finally:sys.argv=previous
    try:
        call(['server']);server_id=cid(config)
        call(['server']);assert cid(config)==server_id
        for robot_id in ('startup_a','startup_b'):
            args=['robot','--robot-id',robot_id,'--server',f'http://127.0.0.1:{config["AGENTICROS_REGISTRY_PORT"]}',
                  '--host','127.0.0.1','--observation-port',str(port())]
            robot=lan.client_config(argparse.Namespace(robot_id=robot_id,server=args[4],host='127.0.0.1',observation_port=int(args[-1]),environment=None,stop=False))
            robots.append(robot)
            call(args);first=cid(robot,True);call(args);assert cid(robot,True)==first
        def request(data):
            raw=subprocess.check_output(['docker','exec',server_id,'agenticros-ros','request',json.dumps(data)],text=True)
            return json.loads(raw)
        end=time.monotonic()+30
        while True:
            status=request({'op':'status'})
            if len(status['robots'])==2 and all(r['observation_fresh'] for r in status['robots']):break
            assert time.monotonic()<end,status
            time.sleep(.5)
        for robot_id in ('startup_a','startup_b'):
            reply=request(dict(op='service',endpoint=f'/robots/{robot_id}/driver/status',type='std_srvs/srv/Trigger'))
            assert reply['response']['success']
        rules=root/'docker/agent_instance/agent/AGENTS.md'
        marker='\nFixture hot-sync marker '+name+'\n';rules.write_text(rules.read_text()+marker)
        command=lan.agent_command(config,['true'],[],False)
        subprocess.run(command,cwd=root,env=lan.environment(config),check=True)
        installed=subprocess.check_output(['docker','exec',server_id,'cat','/workspace/AGENTS.md'],text=True)
        assert marker in installed
        inspections=json.loads(subprocess.check_output(['docker','inspect',server_id,*[cid(r,True) for r in robots]],text=True))
        result=dict(result='PASS',physical_hosts=1,containers=3,network_mode='host',ipc='host',
                    repeat_start_keeps_containers=True,hot_rules_without_rebuild=True,robot_count=2,
                    images=[i['Image'] for i in inspections])
        (root/'result.json').write_text(json.dumps(result,indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        for robot in robots:
            lan.run(robot,['stop','client'],True);lan.run(robot,['rm','-f','client'],True)
        lan.run(config,['stop','server']);lan.run(config,['rm','-f','server'])
        print('Evidence and volumes retained: '+str(root),flush=True)

if __name__=='__main__':main()
