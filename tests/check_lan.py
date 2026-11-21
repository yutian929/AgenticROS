#!/usr/bin/env python3
"""Isolated two-client Docker integration, not autonomous adaptation or task evaluation.
Creates fresh simulations and workspace, exercises only those bodies, preserves evidence/volumes.
Requires images agenticros-{server,cartesian_gripper}:single already built.
"""
import argparse
import ipaddress
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('launcher', ROOT/'scripts/runtime.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

BOOTSTRAP = r'''
from pathlib import Path
import shutil, subprocess
root = Path('/workspace/ros_ws')
shutil.copytree('/workspace/robot_docs/cartesian_gripper/interfaces', root/'src/agenticros_driver_interfaces')
subprocess.run(['colcon','build','--executor','sequential'],cwd=root,check=True)
'''

PROBE = r'''
import hashlib,json,pathlib,time,uuid,urllib.request,os
import rclpy
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from std_msgs.msg import String
from agenticros_driver_interfaces.action import Actuate
from unique_identifier_msgs.msg import UUID
rclpy.init();n=rclpy.create_node('isolated_lan_probe')
root=pathlib.Path('/workspace/evidence');root.mkdir(exist_ok=True)
namespaces={robot:'/robot/'+robot for robot in ('robot_a','robot_b')}
def wait(f,seconds=15):
 rclpy.spin_until_future_complete(n,f,timeout_sec=seconds)
 assert f.done(),'timeout: never resend';return f.result()
services={}
def call(robot,kind,typ):
 # Keep query endpoints alive like a normal ROS client node; repeated polling
 # must not generate unrelated discovery churn during the action under test.
 key=(robot,kind)
 if key not in services:services[key]=n.create_client(typ,namespaces[robot]+'/driver/'+kind)
 c=services[key]
 assert c.wait_for_service(timeout_sec=20),(robot,kind)
 request=typ.Request()
 return wait(c.call_async(request),45)
def status(robot):return json.loads(call(robot,'status',Trigger).message)
def send(robot,q,duration,session=None):
 c=ActionClient(n,Actuate,namespaces[robot]+'/driver/actuate');assert c.wait_for_server(timeout_sec=20)
 s=status(robot);key=uuid.uuid4();(root/(key.hex+'-request.json')).write_text(json.dumps(dict(robot=robot,session=session or s['session_id'],positions=q,duration=duration,uuid=key.hex)))
 h=wait(c.send_goal_async(Actuate.Goal(session_id=session or s['session_id'],positions=[float(x) for x in q],duration=float(duration)),goal_uuid=UUID(uuid=list(key.bytes))))
 return c,h,key.hex
def result(h):
 r=json.loads(wait(h.get_result_async(),__RESULT_TIMEOUT__).result.receipt_json)
 (root/(r['goal_id']+'-result.json')).write_text(json.dumps(r,indent=2));return r
phase=__PHASE__
if phase=='normal':
 states={robot:status(robot) for robot in ['robot_a','robot_b']}
 assert states['robot_a']['session_id']!=states['robot_b']['session_id']
 for robot,s in states.items():
  assert s['robot_id']==robot and s['stop_confirmed'] and not s['busy']
 # Reject a goal carrying the other robot's session.
 a=states['robot_a'];b=states['robot_b']
 c,h,_=send('robot_a',a['state']['joint_positions'],.5,b['session_id']);assert not h.accepted;c.destroy()
 # Each idle body can execute concurrently, while each rejects a second raw action.
 pending=[]
 for robot,s in states.items():
  q=s['state']['joint_positions'][:];q[2]+=.01
  c,h,key=send(robot,q,12);assert h.accepted;pending.append((robot,c,h,key))
 c,h,_=send('robot_a',a['state']['joint_positions'],.5);assert not h.accepted;c.destroy()
 for robot,c,h,key in pending:
  r=result(h);assert r['complete'] and r['stop_confirmed'] and r['goal_id']==key
  assert r['session_id']==states[robot]['session_id'] and r['robot_id']==robot;c.destroy()
 # Cancellation is reconciled by UUID and stop receipt.
 s=status('robot_a');q=s['state']['joint_positions'][:];q[2]+=.01
 c,h,key=send('robot_a',q,20);assert h.accepted
 assert wait(h.cancel_goal_async()).goals_canceling
 r=result(h);assert r['reason']=='cancelled' and r['stop_confirmed'];c.destroy()
 assert status('robot_b')['stop_confirmed']
 (root/'normal.json').write_text(json.dumps({'two_robots':True,'cross_session_rejected':True,'exclusive_actions':True,'cancel_reconciled':True}))
n.destroy_node();rclpy.shutdown()
print(phase+' PASS',flush=True)
'''


def docker(*args, **kwargs):
    return subprocess.run(['docker',*args],check=True,text=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--viewer-image',default='agenticros-viewer:single')
    parser.add_argument('--server-image',default='agenticros-server:single')
    parser.add_argument('--robot-image',help='diagnostic image override')
    parser.add_argument('--environment',choices=('cartesian_gripper','aloha_tabletop'),default='cartesian_gripper')
    parser.add_argument('--viewer-only',action='store_true')
    parser.add_argument('--keep',action='store_true',help='retain disposable fixture for diagnostics')
    parser.add_argument('--samples',type=int,default=50)
    parser.add_argument('--cold-samples',type=int,default=10)
    args=parser.parse_args()
    name='agenticros-check-'+secrets.token_hex(4)
    root=ROOT/'runs/checks'/name
    workspace=root/'workspace';workspace.mkdir(parents=True)
    workspace.chmod(0o2777)
    network=name
    docker('network','create',network,capture_output=True)
    info=json.loads(docker('network','inspect',network,capture_output=True).stdout)[0]
    subnet=ipaddress.IPv4Network(info['IPAM']['Config'][0]['Subnet'])
    addresses={k:str(subnet.network_address+i) for k,i in [('server',10),('robot_a',11),('robot_b',12),('viewer',13)]}
    config=dict(ROS_DOMAIN_ID=74,ROS_LOCALHOST_ONLY=0,AGENTICROS_VIEWER_PORT=8080,AGENTICROS_VNC_PORT=5900,AGENTICROS_VIEWER_BIND='127.0.0.1',RMW_FASTRTPS_PUBLICATION_MODE='ASYNCHRONOUS')
    agent=name+'-server';viewer=name+'-viewer';clients=[]
    def start(container,image,env,mounts):
        command=['run','-d','--name',container,'--network',network,'--ip',addresses['server' if container==agent else 'viewer' if container==viewer else container.rsplit('-',1)[1]],
                 '--ipc','private','--shm-size','512m','--cap-drop','ALL','--security-opt','no-new-privileges:true',
                 '--init','--group-add',str(os.getgid())]
        for k,v in env.items(): command+=['-e',k+'='+str(v)]
        for mount in mounts: command+=['--mount',mount]
        docker(*command,image,capture_output=True)
    def run_script(source,arguments=''):
        command='source /workspace/ros_ws/install/setup.bash && python - '+arguments
        return docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','bash','-c',command,input=source)
    try:
        start(agent,args.server_image,config,[
            'type=bind,src='+str(workspace)+',dst=/workspace',
            'type=bind,src='+str(ROOT/'docker/robot_instances/robot_docs')+',dst=/workspace/robot_docs,readonly',
            'type=bind,src='+str(ROOT/'docker/agent_instance/agent/codex.toml')+',dst=/config/codex.toml,readonly',
            'type=volume,src='+name+'-codex,dst=/home/agent/.codex'])
        deadline=time.monotonic()+60
        while True:
            result=subprocess.run(['docker','exec',agent,'python3','/opt/instance/runtime.py','health'],capture_output=True)
            if result.returncode==0:break
            if time.monotonic()>deadline:raise RuntimeError('Server health failed: '+result.stderr.decode())
            time.sleep(1)
        for robot in ['robot_a','robot_b']:
            container=name+'-'+robot; clients.append(container)
            env=dict(AGENTICROS_ROBOT_ID=robot,ROS_DOMAIN_ID=74,ROS_LOCALHOST_ONLY=0,RMW_FASTRTPS_PUBLICATION_MODE='ASYNCHRONOUS')
            start(container,args.robot_image or 'agenticros-'+args.environment+':single',env,[])
        deadline=time.monotonic()+60
        for container in clients:
            while True:
                result=subprocess.run(['docker','exec',container,'python3','-c',
                    "import pathlib; assert pathlib.Path('/tmp/body-camera-ready').exists()"],capture_output=True)
                if result.returncode==0:break
                if time.monotonic()>deadline:raise RuntimeError('robot startup failed')
                time.sleep(1)
        inspections={key:json.loads(docker('inspect',container,capture_output=True).stdout)[0]
                     for key,container in [('server',agent),*zip(['robot_a','robot_b'],clients)]}
        assert len({i['NetworkSettings']['SandboxKey'] for i in inspections.values()})==3
        (root/'topology.json').write_text(json.dumps(dict(physical_hosts=1,containers=3,network_namespaces=3,
            discovery="native",addresses=addresses,images={k:i['Image'] for k,i in inspections.items()}),indent=2))
        actual=inspections['server']
        mounts={m['Destination']:m for m in actual['Mounts']}
        assert set(mounts)=={'/workspace','/workspace/robot_docs','/home/agent/.codex','/config/codex.toml'}
        assert mounts['/workspace']['Source']==str(workspace) and mounts['/workspace']['Type']=='bind'
        assert not mounts['/config/codex.toml']['RW'] and actual['Config']['User']=='agent'
        (root/'mounts.json').write_text(json.dumps(actual['Mounts'],indent=2))
        if args.viewer_only:
            start(viewer,args.viewer_image,config,['type=volume,src='+name+'-viewer,dst=/viewer-auth'])
            def viewer_probe(source):
                docker('exec','-i',viewer,'bash','-c','source /opt/ros/humble/setup.bash; python3 -',input=source)
            viewer_probe((ROOT/'tests/viewer_probe.py').read_text())
            viewer_probe((ROOT/'tests/test_viewer_ui.py').read_text())
            before=json.loads(docker('exec',viewer,'cat','/tmp/viewer-status.json',capture_output=True).stdout)
            password=docker('exec',viewer,'sha256sum','/viewer-auth/password',capture_output=True).stdout
            # Agent goes offline; Viewer still observes robots through ROS.
            docker('stop','-t','10',agent,capture_output=True)
            viewer_probe((ROOT/'tests/viewer_probe.py').read_text())
            docker('restart','-t','45',viewer,capture_output=True)
            viewer_probe((ROOT/'tests/viewer_probe.py').read_text())
            after=json.loads(docker('exec',viewer,'cat','/tmp/viewer-status.json',capture_output=True).stdout)
            assert before['robots']==after['robots']
            assert password==docker('exec',viewer,'sha256sum','/viewer-auth/password',capture_output=True).stdout
            view_mounts=json.loads(docker('inspect',viewer,capture_output=True).stdout)[0]['Mounts']
            assert {m['Destination'] for m in view_mounts}=={'/viewer-auth'}
            docker('cp',viewer+':/tmp/viewer-evidence',str(root/'viewer-evidence'),capture_output=True)
            (root/'result.json').write_text(json.dumps(dict(result='PASS',physical_hosts=1,containers=4,viewer_independent=True,agent_stopped=True),indent=2))
            print('Independent Viewer lifecycle PASS',flush=True)
            return
        docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','python','-',input=BOOTSTRAP)
        docker('exec',agent,'python3','/opt/instance/runtime.py','clients-reload')
        # Give the worker time to bind its local socket; ROS discovery is measured below.
        time.sleep(1)
        probe=(ROOT/'tests/network_probe.py').read_text()
        for mode,count,label in [('warm',args.samples,'warm'),('concurrent',args.samples//2,'concurrent')]+[('cold',1,'cold-'+str(i)) for i in range(args.cold_samples)]:
            run_script(probe,f'{mode} {count} {label}')
        resident=(ROOT/'tests/resident_probe.py').read_text()
        run_script(resident, str(args.samples))
        run_script(PROBE.replace('__PHASE__',repr('normal')).replace('__RESULT_TIMEOUT__','120'))
        # Warm client spans a robot process restart; namespace must remain stable.
        reconnect=(ROOT/'tests/reconnect_probe.py').read_text()
        proc=subprocess.Popen(['docker','exec','-i',agent,'/usr/local/bin/agent-entrypoint','bash','-c',
                               'source /workspace/ros_ws/install/setup.bash && python -'],stdin=subprocess.PIPE,text=True)
        proc.stdin.write(reconnect);proc.stdin.close()
        deadline=time.monotonic()+40
        while not (workspace/'evidence/restart-ready').exists():
            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('restart probe failed')
            time.sleep(.2)
        docker('stop','-t','10',clients[0],capture_output=True)
        docker('start',clients[0],capture_output=True)
        assert proc.wait(timeout=90)==0
        # Restart only the communication worker: journal blocks UUID replay.
        docker('exec',agent,'python3','/opt/instance/runtime.py','clients-reload')
        time.sleep(1)
        run_script(resident, '5 restart')
        (root/'result.json').write_text(json.dumps(dict(result='PASS',physical_hosts=1,containers=3,
            discovery="native",autonomous_adaptation=False,task_evaluation=False),indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        for artifact in ('viewer-status.json','current.rviz'):
            subprocess.run(['docker','cp',(viewer if args.viewer_only else agent)+':/tmp/'+artifact,str(root/artifact)],capture_output=True)
        for container in [agent,*clients,*([viewer] if args.viewer_only else [])]:
            log=subprocess.run(['docker','logs',container],text=True,capture_output=True)
            (root/(container+'.log')).write_text(log.stdout+log.stderr)
            if not args.keep:subprocess.run(['docker','rm','-f',container],capture_output=True)
        if not args.keep:subprocess.run(['docker','network','rm',network],capture_output=True)
        print('Evidence retained: '+str(root),flush=True)

if __name__=='__main__':main()
