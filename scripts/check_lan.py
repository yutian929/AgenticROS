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
spec = importlib.util.spec_from_file_location('launcher', ROOT/'scripts/lan.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

BOOTSTRAP = r'''
import json,pathlib,subprocess,rclpy,os,urllib.request
from rcl_interfaces.srv import GetParameters
rclpy.init();n=rclpy.create_node('public_interface_bootstrap')
namespace='/robots/robot_a'
c=n.create_client(GetParameters,namespace+'/raw_body_driver/get_parameters')
assert c.wait_for_service(timeout_sec=25)
f=c.call_async(GetParameters.Request(names=['interface_sources']))
rclpy.spin_until_future_complete(n,f,timeout_sec=45)
assert f.done()
s=json.loads(f.result().values[0].string_value)
root=pathlib.Path('/workspace/ros_ws');pkg=root/'src'/s['package']
assert s['package']=='agenticros_driver_interfaces'
for name,content in s['files'].items():
 p=pkg/name;assert pkg in p.resolve().parents;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
n.destroy_node();rclpy.shutdown()
subprocess.run(['colcon','build','--executor','sequential'],cwd=root,check=True)
'''

PROBE = r'''
import hashlib,json,pathlib,time,uuid,urllib.request,os
import rclpy
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from std_msgs.msg import String
from agenticros_driver_interfaces.srv import Snapshot
from agenticros_driver_interfaces.action import Actuate
from unique_identifier_msgs.msg import UUID
rclpy.init();n=rclpy.create_node('isolated_lan_probe')
root=pathlib.Path('/workspace/evidence');root.mkdir(exist_ok=True)
namespaces={robot:'/robots/'+robot for robot in ('robot_a','robot_b')}
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
 if kind=='observe':request.session_id=status(robot)['session_id']
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
  shot=json.loads(call(robot,'observe',Snapshot).snapshot_json)
  assert shot['session_id']==s['session_id'] and shot['robot_id']==robot
  assert shot['state']['frame']==namespaces[robot].strip('/')+'/base'
  assert 'rgb_path' not in shot
  dest=root/robot;dest.mkdir(exist_ok=True)
  for name,key in [('rgb.png','rgb_url'),('depth.npy','depth_url')]:
   with urllib.request.urlopen(shot[key],timeout=5) as response:data=response.read()
   assert hashlib.sha256(data).hexdigest()==shot['artifacts'][name]['sha256']
   (dest/name).write_bytes(data)
  from PIL import Image
  import numpy as np
  assert Image.open(dest/'rgb.png').size==(shot['calibration']['width'],shot['calibration']['height'])
  depth=np.load(dest/'depth.npy');assert depth.shape==(shot['calibration']['height'],shot['calibration']['width']) and np.isfinite(depth).all()
  (dest/'snapshot.json').write_text(json.dumps(shot,indent=2))
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
 (root/'normal.json').write_text(json.dumps({'two_robots':True,'cross_session_rejected':True,'exclusive_actions':True,'cancel_reconciled':True,'remote_snapshots':True}))
else:
 s=status('robot_a');assert s['stop_confirmed'];q=s['state']['joint_positions'][:];q[2]+=.01
 latest={};last_state_at=[0.]
 def state_received(msg):
  latest.update(json.loads(msg.data));last_state_at[0]=time.monotonic()
 sub=n.create_subscription(String,namespaces['robot_a']+'/driver/state',state_received,10)
 deadline=time.monotonic()+10
 while not latest:
  assert time.monotonic()<deadline; rclpy.spin_once(n,timeout_sec=.1)
 started=time.monotonic();c,h,key=send('robot_a',q,30);assert h.accepted
 # Establish the result request before deliberately stopping discovery.
 future=h.get_result_async()
 rclpy.spin_until_future_complete(n,future,timeout_sec=1)
 (root/'disconnect-ready').write_text(key)
 # Existing state subscription distinguishes execution from result delivery.
 while not future.done() and time.monotonic()-started<120:
  rclpy.spin_until_future_complete(n,future,timeout_sec=.5)
  (root/'outage-progress.json').write_text(json.dumps(dict(elapsed_seconds=time.monotonic()-started,goal_id=key,result_received=future.done(),state_age_seconds=time.monotonic()-last_state_at[0],state=latest),indent=2))
 assert future.done(),'result timeout: never resend'
 r=json.loads(future.result().result.receipt_json)
 (root/(key+'-result.json')).write_text(json.dumps(r,indent=2))
 assert r['complete'] and r['stop_confirmed'];c.destroy()
 (root/'discovery-outage-completed').write_text(key)
 deadline=time.monotonic()+45
 while not (root/'discovery-restarted').exists():
  assert time.monotonic()<deadline;time.sleep(.2)
 # Graph caches can report a service before the restarted discovery service
 # has rebuilt both response endpoints. Sample that recovery window explicitly.
 # Only this read-only status probe is retried; no goal/result/cancel is replayed.
 rows=[]
 try:
  for robot in ['robot_a','robot_b']:
   endpoint=(robot,'status')
   if endpoint not in services:services[endpoint]=n.create_client(Trigger,namespaces[robot]+'/driver/status')
   client=services[endpoint];deadline=time.monotonic()+30
   while True:
    row=dict(layer='ros_data',phase='discovery_restart',operation='status',robot=robot,success=False)
    started=time.monotonic()
    available=client.wait_for_service(timeout_sec=2)
    row['discovery_ms']=(time.monotonic()-started)*1000
    if not available:row['error']='discovery_failure'
    else:
     started=time.monotonic();future=client.call_async(Trigger.Request())
     rclpy.spin_until_future_complete(n,future,timeout_sec=2)
     row['response_ms']=(time.monotonic()-started)*1000
     if not future.done():
      row['error']='request_timeout';client.remove_pending_request(future)
     else:
      response=future.result()
      row['success']=response.success
      if not response.success:row['error']='server_failure'
      else:
       s=json.loads(response.message);assert s['stop_confirmed'] and s['control_available']
    rows.append(row)
    (root/'bench-discovery-reconnect.json').write_text(json.dumps(rows,indent=2))
    if row['success']:break
    assert time.monotonic()<deadline,'cached status client did not recover'
    rclpy.spin_once(n,timeout_sec=.25)
 finally:(root/'bench-discovery-reconnect.json').write_text(json.dumps(rows,indent=2))
 (root/'disconnect.json').write_text(json.dumps({'accepted_action_completed_without_server':True,'control_not_lease_gated':True,'cached_status_clients_recovered':True}))
n.destroy_node();rclpy.shutdown()
print(phase+' PASS',flush=True)
'''


def docker(*args, **kwargs):
    return subprocess.run(['docker',*args],check=True,text=True,**kwargs)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-image',default='agenticros-server:single')
    parser.add_argument('--robot-image',help='diagnostic image override')
    parser.add_argument('--transport',choices=('tcp','udp'),default='tcp')
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
    docker('network','create','--internal',network,capture_output=True)
    info=json.loads(docker('network','inspect',network,capture_output=True).stdout)[0]
    subnet=ipaddress.IPv4Network(info['IPAM']['Config'][0]['Subnet'])
    addresses={k:str(subnet.network_address+i) for k,i in [('server',10),('robot_a',11),('robot_b',12)]}
    config=dict(AGENTICROS_DDS_HOST=addresses['server'],
                AGENTICROS_DDS_PORT=11811,ROS_DOMAIN_ID=74,AGENTICROS_DDS_TRANSPORT=args.transport,
                AGENTICROS_DDS_PREFIX=launcher.PREFIX,AGENTICROS_VIEWER_PORT=8080,
                AGENTICROS_VNC_PORT=5900,AGENTICROS_VIEWER_BIND='127.0.0.1',
                FASTRTPS_DEFAULT_PROFILES_FILE='/tmp/agenticros-lan.xml',
                RMW_FASTRTPS_PUBLICATION_MODE='ASYNCHRONOUS')
    agent=name+'-server';clients=[]
    def start(container,image,env,mounts):
        command=['run','-d','--name',container,'--network',network,'--ip',addresses['server' if container==agent else container.rsplit('-',1)[1]],
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
            'type=bind,src='+str(ROOT/'docker/agent_instance/agent/codex.toml')+',dst=/config/codex.toml,readonly',
            'type=volume,src='+name+'-codex,dst=/home/agent/.codex',
            'type=volume,src='+name+'-viewer,dst=/viewer-auth'])
        deadline=time.monotonic()+60
        while True:
            result=subprocess.run(['docker','exec',agent,'python3','/opt/instance/runtime.py','health'],capture_output=True)
            if result.returncode==0:break
            if time.monotonic()>deadline:raise RuntimeError('Server health failed: '+result.stderr.decode())
            time.sleep(1)
        for robot in ['robot_a','robot_b']:
            container=name+'-'+robot; clients.append(container)
            env=dict(AGENTICROS_ROBOT_ID=robot,
                     AGENTICROS_CLIENT_HOST=addresses[robot],AGENTICROS_OBSERVATION_PORT=8800,
                     RMW_FASTRTPS_PUBLICATION_MODE='ASYNCHRONOUS',ROS_DOMAIN_ID=74,AGENTICROS_DDS_HOST=addresses['server'],
                     AGENTICROS_DDS_PORT=11811,AGENTICROS_DDS_PREFIX=launcher.PREFIX,AGENTICROS_DDS_TRANSPORT=args.transport,
                     AGENTICROS_ADVERTISE_HOST=addresses[robot],FASTRTPS_DEFAULT_PROFILES_FILE='/tmp/agenticros-lan.xml')
            start(container,args.robot_image or 'agenticros-'+args.environment+':single',env,['type=volume,src='+name+'-'+robot+'-observations,dst=/observations'])
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
            transport=args.transport,addresses=addresses,images={k:i['Image'] for k,i in inspections.items()}),indent=2))
        actual=inspections['server']
        mounts={m['Destination']:m for m in actual['Mounts']}
        assert set(mounts)=={'/workspace','/home/agent/.codex','/config/codex.toml','/viewer-auth'}
        assert mounts['/workspace']['Source']==str(workspace) and mounts['/workspace']['Type']=='bind'
        assert not mounts['/config/codex.toml']['RW'] and actual['Config']['User']=='agent'
        (root/'mounts.json').write_text(json.dumps(actual['Mounts'],indent=2))
        if args.viewer_only:
            docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','python','-',input=(ROOT/'tests/viewer_probe.py').read_text())
            docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','python','-',input=(ROOT/'tests/test_viewer_ui.py').read_text())
            before=json.loads(docker('exec',agent,'cat','/tmp/viewer-status.json',capture_output=True).stdout)
            password=docker('exec',agent,'sha256sum','/viewer-auth/password',capture_output=True).stdout
            docker('exec',agent,'python3','/opt/instance/runtime.py','viewer-stop')
            docker('exec',agent,'python3','/opt/instance/runtime.py','viewer-start')
            docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','python','-',input=(ROOT/'tests/viewer_probe.py').read_text())
            after=json.loads(docker('exec',agent,'cat','/tmp/viewer-status.json',capture_output=True).stdout)
            assert before['sessions']==after['sessions']
            assert password==docker('exec',agent,'sha256sum','/viewer-auth/password',capture_output=True).stdout
            attach=['docker','exec',agent,'python3','/opt/instance/runtime.py','attach']
            brain=subprocess.Popen(attach+['sleep','4']);time.sleep(.5)
            duplicate=subprocess.run(attach+['true'],capture_output=True)
            assert duplicate.returncode!=0
            assert brain.wait(timeout=8)==0
            print('Viewer lifecycle and single Agent lock PASS',flush=True)
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
        # Stop fixed discovery during a finite simulated action; local execution continues.
        script=PROBE.replace('__PHASE__',repr('disconnect')).replace('__RESULT_TIMEOUT__','120')
        proc=subprocess.Popen(['docker','exec','-i',agent,'/usr/local/bin/agent-entrypoint','bash','-c',
                               'source /workspace/ros_ws/install/setup.bash && python -'],stdin=subprocess.PIPE,text=True)
        proc.stdin.write(script);proc.stdin.close()
        deadline=time.monotonic()+120
        while not (workspace/'evidence/disconnect-ready').exists():
            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('disconnect probe failed')
            time.sleep(.2)
        docker('exec',agent,'python3','/opt/instance/runtime.py','server-stop')
        # Simulated duration is not a wall-time execution deadline. Match the
        # bounded result wait, with a small allowance for saving evidence.
        deadline=time.monotonic()+125
        while not (workspace/'evidence/discovery-outage-completed').exists():
            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('action result missing during discovery outage; see outage-progress.json')
            time.sleep(.2)
        docker('exec',agent,'python3','/opt/instance/runtime.py','server-start')
        (workspace/'evidence/discovery-restarted').write_text('ready')
        assert proc.wait(timeout=120)==0
        (root/'result.json').write_text(json.dumps(dict(result='PASS',physical_hosts=1,containers=3,
            transport=args.transport,autonomous_adaptation=False,task_evaluation=False),indent=2))
        print('PASS: '+str(root),flush=True)
    finally:
        for artifact in ('viewer-status.json','current.rviz'):
            subprocess.run(['docker','cp',agent+':/tmp/'+artifact,str(root/artifact)],capture_output=True)
        for container in [agent,*clients]:
            log=subprocess.run(['docker','logs',container],text=True,capture_output=True)
            (root/(container+'.log')).write_text(log.stdout+log.stderr)
            if not args.keep:subprocess.run(['docker','rm','-f',container],capture_output=True)
        if not args.keep:subprocess.run(['docker','network','rm',network],capture_output=True)
        print('Evidence retained: '+str(root),flush=True)

if __name__=='__main__':main()
