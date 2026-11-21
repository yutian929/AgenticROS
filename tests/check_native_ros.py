"""Native ROS CLI acceptance in three isolated containers; no transport proxy calls."""
import argparse
import concurrent.futures
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
import threading
import re
import math

ROOT=Path(__file__).resolve().parents[1]
def docker(*args,**kwargs):return subprocess.run(['docker',*args],check=True,text=True,**kwargs)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--samples',type=int,default=10)
    p.add_argument('--image-suffix',default='single')
    p.add_argument('--environment',choices=('cartesian_gripper','aloha_tabletop'),default='cartesian_gripper')
    p.add_argument('--restart',action='store_true',help='test a native subscription across robot restart and fresh clients after Agent restart')
    p.add_argument('--strict-default',action='store_true',help='also fail if the default 0.5 s cold CLI snapshot is incomplete')
    p.add_argument('--keep',action='store_true',help='retain this disposable fixture for diagnostics')
    args=p.parse_args()
    name='agenticros-native-'+secrets.token_hex(4);root=ROOT/'runs/checks'/name
    workspace=root/'workspace';workspace.mkdir(parents=True);workspace.chmod(0o2777)
    docker('network','create',name,capture_output=True)
    net=json.loads(docker('network','inspect',name,capture_output=True).stdout)[0]
    subnet=ipaddress.ip_network(net['IPAM']['Config'][0]['Subnet'])
    addresses={key:str(subnet.network_address+number) for key,number in [('server',10),('robot_a',11),('robot_b',12)]}
    containers=[];rows=[];row_lock=threading.Lock()
    arm='/left' if args.environment=='aloha_tabletop' else ''
    common=dict(ROS_DOMAIN_ID=76,ROS_LOCALHOST_ONLY=0,RMW_FASTRTPS_PUBLICATION_MODE='ASYNCHRONOUS')
    def start(role,image,env,mounts):
        container=name+'-'+role;containers.append(container)
        command=['run','-d','--name',container,'--network',name,'--ip',addresses[role],
                 '--ipc','private','--shm-size','512m','--init','--cap-drop','ALL',
                 '--security-opt','no-new-privileges:true','--group-add',str(os.getgid())]
        for key,value in env.items():command+=['-e',key+'='+str(value)]
        for mount in mounts:command+=['--mount',mount]
        docker(*command,image,capture_output=True);return container
    server=None
    def cli(arguments,phase,operation,robot=None,budget=12,expected_session=None):
        before=time.monotonic()
        command=['docker','exec','-e','PYTHONUNBUFFERED=1',server,'/usr/local/bin/agent-entrypoint','timeout','-k','2',str(budget),'ros2',*arguments]
        proc=subprocess.Popen(command,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        output='';sent_at=None;response_at=None
        for line in proc.stdout:
            output+=line
            if 'requester: making request:' in line: sent_at=time.monotonic()
            if line.strip()=='response:': response_at=time.monotonic()
        rc=proc.wait(timeout=5)
        expected=[f'/robot/{r}{arm}/driver/state' for r in ('robot_a','robot_b')]
        if args.environment=='aloha_tabletop':
            expected += [f'/robot/{r}/{camera}/camera/color/image_raw/compressed' for r in ('robot_a','robot_b') for camera in ('left','right','overhead')]
        success=rc==0
        if operation=='list':success=success and all(x in output for x in expected)
        elif operation=='service':success=success and 'success=True' in output and 'session_id' in output
        elif operation=='state':success=success and 'session_id' in output
        elif operation=='image':success=success and 'frame_id:' in output
        if expected_session:success=success and expected_session in output
        error=None if success else ('discovery_failure' if operation=='list' or 'waiting for service' in output and 'requester:' not in output else 'request_timeout' if rc in (124,137) else 'server_failure')
        row=dict(phase=phase,operation=operation,robot=robot,success=success,error=error,
                 total_ms=(time.monotonic()-before)*1000,returncode=rc,
                 discovery_ms=(sent_at-before)*1000 if sent_at else None,
                 response_ms=(response_at-sent_at)*1000 if response_at and sent_at else None)
        with row_lock:
            rows.append(row)
            (root/f'{len(rows):03d}-{phase}-{operation}.log').write_text(output)
            (root/'samples.json').write_text(json.dumps(rows,indent=2))
            print(json.dumps(row),flush=True)
        return output
    try:
        server=start('server','agenticros-server:'+args.image_suffix,dict(common,
            AGENTICROS_VIEWER_PORT=8080,AGENTICROS_VNC_PORT=5900,AGENTICROS_VIEWER_BIND='127.0.0.1'),[
            f'type=bind,src={workspace},dst=/workspace',
            'type=bind,src='+str(ROOT/'docker/robot_instances/robot_docs')+',dst=/workspace/robot_docs,readonly',
            f'type=bind,src={ROOT}/docker/agent_instance/agent/codex.toml,dst=/config/codex.toml,readonly',
            f'type=volume,src={name}-codex,dst=/home/agent/.codex'])
        deadline=time.monotonic()+40
        while subprocess.run(['docker','exec',server,'python3','/opt/instance/runtime.py','health'],capture_output=True).returncode:
            assert time.monotonic()<deadline,'server health timeout';time.sleep(.5)
        for robot in ('robot_a','robot_b'):
            start(robot,'agenticros-'+args.environment+':'+args.image_suffix,dict(common,
                AGENTICROS_ROBOT_ID=robot),
                [])
        deadline=time.monotonic()+45
        for c in containers[1:]:
            while subprocess.run(['docker','exec',c,'test','-f','/tmp/body-camera-ready'],capture_output=True).returncode:
                assert time.monotonic()<deadline,'robot startup timeout';time.sleep(.5)
        inspections=[json.loads(docker('inspect',c,capture_output=True).stdout)[0] for c in containers]
        assert len({i['NetworkSettings']['SandboxKey'] for i in inspections})==3
        (root/'topology.json').write_text(json.dumps(dict(physical_hosts=1,containers=3,network_namespaces=3,discovery='native',images=[i['Image'] for i in inspections]),indent=2))
        for _ in range(args.samples):
            # Ordinary default CLI, with its own newly started ROS daemon.
            docker('exec',server,'/usr/local/bin/agent-entrypoint','ros2','daemon','stop',capture_output=True)
            cli(['topic','list'],'cold_daemon','list')
            # A fresh participant each invocation, independent of the daemon.
            cli(['topic','list','--no-daemon','--spin-time','3'],'cold_direct','list')
            cli(['topic','echo',f'/robot/robot_a{arm}/driver/state','std_msgs/msg/String','--once','--no-daemon','--field','data'],'cold','state','robot_a')
            cli(['topic','echo',f'/robot/robot_a{arm}/camera/color/image_raw/compressed','sensor_msgs/msg/CompressedImage','--once','--no-daemon','--field','header'],'cold','image','robot_a')
            cli(['service','call',f'/robot/robot_a{arm}/driver/status','std_srvs/srv/Trigger','{}'],'cold','service','robot_a')
        for _ in range(args.samples):cli(['topic','list'],'warm_daemon','list')
        for _ in range(args.samples):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda r:cli(['service','call',f'/robot/{r}{arm}/driver/status','std_srvs/srv/Trigger','{}'],'concurrent','service',r),['robot_a','robot_b']))
        if args.restart:
            state_topic=f'/robot/robot_a{arm}/driver/state'
            streaming=root/'restart-stream.log'
            with streaming.open('w') as output_file:
                stream=subprocess.Popen(['docker','exec','-e','PYTHONUNBUFFERED=1',server,
                    '/usr/local/bin/agent-entrypoint','timeout','-k','2','90','ros2','topic','echo',
                    state_topic,'std_msgs/msg/String','--no-daemon','--field','data'],stdout=output_file,stderr=subprocess.STDOUT)
                def sessions():
                    return set(re.findall(r'"session_id": "([a-f0-9]+)"',streaming.read_text()))
                try:
                    deadline=time.monotonic()+20
                    while not sessions():
                        assert time.monotonic()<deadline,'native stream initial discovery failed';time.sleep(.1)
                    previous=sessions();started=time.monotonic()
                    docker('restart','-t','5',containers[1],capture_output=True)
                    deadline=time.monotonic()+40
                    while not sessions()-previous:
                        assert time.monotonic()<deadline,'native existing subscription did not reconnect';time.sleep(.1)
                    rows.append(dict(phase='robot_restart',operation='existing_subscription',success=True,error=None,
                        total_ms=(time.monotonic()-started)*1000,previous_sessions=sorted(previous),new_sessions=sorted(sessions()-previous)))
                    for _ in range(args.samples):
                        cli(['topic','list','--no-daemon','--spin-time','3'],'robot_restart','list')
                        cli(['service','call',f'/robot/robot_a{arm}/driver/status','std_srvs/srv/Trigger','{}'],'robot_restart','service','robot_a',expected_session=next(iter(sessions()-previous)))
                finally:
                    # Stop only the fixture's echo process; timeout is also bounded.
                    docker('exec',server,'pkill','-TERM','-f','ros2 topic echo '+state_topic,capture_output=True)
                    stream.wait(timeout=8)
            # Restart the entire Agent container while both robots stay alive.
            docker('restart','-t','5',server,capture_output=True)
            deadline=time.monotonic()+40
            while subprocess.run(['docker','exec',server,'python3','/opt/instance/runtime.py','health'],capture_output=True).returncode:
                assert time.monotonic()<deadline,'Agent restart health timeout';time.sleep(.5)
            for _ in range(args.samples):
                cli(['topic','list','--no-daemon','--spin-time','3'],'agent_restart','list')
                cli(['service','call',f'/robot/robot_b{arm}/driver/status','std_srvs/srv/Trigger','{}'],'agent_restart','service','robot_b')
        summary=[]
        for key in sorted({(r['phase'],r['operation']) for r in rows}):
            selected=[r for r in rows if (r['phase'],r['operation'])==key]
            entry=dict(phase=key[0],operation=key[1],samples=len(selected),successes=sum(r['success'] for r in selected))
            entry['errors']={error:sum(r.get('error')==error for r in selected) for error in ('discovery_failure','request_timeout','server_failure')}
            for metric in ('total_ms','discovery_ms','response_ms'):
                values=sorted(r[metric] for r in selected if r.get(metric) is not None)
                entry[metric]=dict(n=len(values),p50=values[math.ceil(len(values)*.5)-1],p95=values[math.ceil(len(values)*.95)-1],maximum=max(values)) if values else None
            summary.append(entry)
        (root/'samples.json').write_text(json.dumps(rows,indent=2))
        (root/'result.json').write_text(json.dumps(summary,indent=2))
        print('Evidence: '+str(root),flush=True)
        # ROS graph enumeration is a timed snapshot, not a completeness barrier.
        # Retain and report every incomplete stock 0.5 s cold snapshot. Acceptance
        # requires the bounded 3 s fresh-participant check, subscriptions and RPCs.
        required=[r for r in rows if args.strict_default or r['phase']!='cold_daemon']
        assert all(r['success'] for r in required),'native ROS failures; see samples.json'
        print('Default cold snapshot misses: '+str(sum(not r['success'] for r in rows if r['phase']=='cold_daemon')),flush=True)
    finally:
        for c in containers:
            result=subprocess.run(['docker','logs',c],capture_output=True,text=True)
            (root/(c+'.log')).write_text(result.stdout+result.stderr)
            if not args.keep:subprocess.run(['docker','rm','-f',c],capture_output=True)
        if not args.keep:subprocess.run(['docker','network','rm',name],capture_output=True)
        print('Fixture: '+name,flush=True)

if __name__=='__main__':main()
