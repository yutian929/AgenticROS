#!/usr/bin/env python3
"""Isolated two-client Docker integration, not autonomous adaptation or task evaluation.
Creates fresh simulations and workspace, exercises only those bodies, preserves evidence/volumes.
Requires images agenticros-{server,agenticros,simple_mujoco}:lan already built.
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
with urllib.request.urlopen(os.environ['AGENTICROS_REGISTRY_URL']+'/robots') as response: roster=json.load(response)
namespace=next(x['namespace'] for x in roster['robots'] if x['robot_id']=='robot_a' and x['connected'])
c=n.create_client(GetParameters,namespace+'/raw_body_driver/get_parameters')
assert c.wait_for_service(timeout_sec=25)
f=c.call_async(GetParameters.Request(names=['interface_sources']))
rclpy.spin_until_future_complete(n,f,timeout_sec=10)
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
from agenticros_driver_interfaces.srv import Snapshot
from agenticros_driver_interfaces.action import Actuate
from unique_identifier_msgs.msg import UUID
rclpy.init();n=rclpy.create_node('isolated_lan_probe')
root=pathlib.Path('/workspace/evidence');root.mkdir(exist_ok=True)
with urllib.request.urlopen(os.environ['AGENTICROS_REGISTRY_URL']+'/robots') as response: roster=json.load(response)
namespaces={x['robot_id']:x['namespace'] for x in roster['robots'] if x['connected']}
def wait(f,seconds=15):
 rclpy.spin_until_future_complete(n,f,timeout_sec=seconds)
 assert f.done(),'timeout: never resend';return f.result()
def call(robot,kind,typ):
 c=n.create_client(typ,namespaces[robot]+'/driver/'+kind)
 assert c.wait_for_service(timeout_sec=20),kind
 try:return wait(c.call_async(typ.Request()))
 finally:n.destroy_client(c)
def status(robot):return json.loads(call(robot,'status',Trigger).message)
def send(robot,q,duration,session=None):
 c=ActionClient(n,Actuate,namespaces[robot]+'/driver/actuate');assert c.wait_for_server(timeout_sec=20)
 s=status(robot);key=uuid.uuid4();(root/(key.hex+'-request.json')).write_text(json.dumps(dict(robot=robot,session=session or s['session_id'],positions=q,duration=duration,uuid=key.hex)))
 h=wait(c.send_goal_async(Actuate.Goal(session_id=session or s['session_id'],positions=[float(x) for x in q],duration=float(duration)),goal_uuid=UUID(uuid=list(key.bytes))))
 return c,h,key.hex
def result(h):
 r=json.loads(wait(h.get_result_async(),40).result.receipt_json)
 (root/(r['goal_id']+'-result.json')).write_text(json.dumps(r,indent=2));return r
phase=__PHASE__
if phase=='normal':
 states={robot:status(robot) for robot in ['robot_a','robot_b']}
 assert states['robot_a']['session_id']!=states['robot_b']['session_id']
 for robot,s in states.items():
  assert s['robot_id']==robot and s['connected'] and s['stop_confirmed'] and not s['busy']
  assert s['instance_id']==roster['instance_id']
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
 c,h,key=send('robot_a',q,30);assert h.accepted
 (root/'disconnect-ready').write_text(key)
 r=result(h);assert not r['complete'] and r['reason']=='lease_lost' and r['stop_confirmed'];c.destroy()
 (root/'lease-lost-confirmed').write_text('stopped')
 deadline=time.monotonic()+30
 while not (root/'registry-restarted').exists():
  assert time.monotonic()<deadline;time.sleep(.2)
 for robot in ['robot_a','robot_b']:
  s=status(robot);assert not s['connected'] and s['stop_confirmed']
  c,h,_=send(robot,s['state']['joint_positions'],.5);assert not h.accepted;c.destroy()
 (root/'disconnect.json').write_text(json.dumps({'lease_loss_stopped':True,'new_goals_rejected':True}))
n.destroy_node();rclpy.shutdown()
print(phase+' PASS',flush=True)
'''


def free_port(udp=False):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if udp else socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], check=True, text=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--separate-ips', action='store_true',
                        help='give server, Agent and both clients separate network namespaces and IPs')
    args = parser.parse_args()
    instance = 'lan-test-'+secrets.token_hex(4)
    root = ROOT/'runs/instances'/instance
    root.mkdir(parents=True)
    network = None
    addresses = {}
    if args.separate_ips:
        network = 'agenticros-'+instance
        docker('network','create','--internal',network,capture_output=True)
        info = json.loads(docker('network','inspect',network,capture_output=True).stdout)[0]
        subnet = ipaddress.IPv4Network(info['IPAM']['Config'][0]['Subnet'])
        addresses = {name:str(subnet.network_address+offset) for name,offset in
                     [('server',10),('robot_a',11),('robot_b',12),('agent',20)]}
        (root/'network.json').write_text(json.dumps(dict(network=network, addresses=addresses),indent=2))

    def compose(config, client=False):
        command = launcher.compose(config, client)
        if network:
            key = config['AGENTICROS_ROBOT_ID'] if client else 'server'
            services = {'client':addresses[key]} if client else {
                'server':addresses['server'], 'agenticros':addresses['agent']}
            override = root/('network-'+key+'.yaml')
            content = 'services:\n'
            for service,address in services.items():
                content += (f'  {service}:\n    network_mode: !reset null\n'
                            f'    networks:\n      test_lan:\n        ipv4_address: {address}\n')
            content += 'networks:\n  test_lan:\n    external: true\n    name: '+network+'\n'
            override.write_text(content)
            command += ['-f',str(override)]
        return command

    def run(config, command, client=False):
        subprocess.run(compose(config,client)+command,env=launcher.environment(config),cwd=ROOT,check=True)

    config = dict(AGENTICROS_INSTANCE=instance, AGENTICROS_INSTANCE_ID=secrets.token_hex(16),
                  AGENTICROS_DDS_HOST=addresses.get('server','127.0.0.1'), AGENTICROS_REGISTRY_PORT=7447 if network else free_port(),
                  AGENTICROS_DDS_PORT=11811 if network else free_port(True), ROS_DOMAIN_ID=74,
                  AGENTICROS_DDS_PREFIX='.'.join(f'{x:02x}' for x in secrets.token_bytes(12)),
                  AGENTICROS_JOIN_TOKEN=secrets.token_hex(32))
    launcher.write_new(launcher.server_file(instance), config)
    env=launcher.environment(config)
    subprocess.run(['python3','scripts/prepare_workspace.py'],cwd=ROOT,env=env,check=True)
    clients=[];agent='agenticros-probe-'+instance
    print('Isolated integration evidence:',root,flush=True)
    try:
        run(config,['up','-d','--no-recreate','--wait','server'])
        for robot in ('robot_a','robot_b'):
            c=dict(AGENTICROS_INSTANCE=instance,AGENTICROS_ROBOT_ID=robot,
                   AGENTICROS_SERVER=f"http://{config['AGENTICROS_DDS_HOST']}:{config['AGENTICROS_REGISTRY_PORT']}",
                   AGENTICROS_CLIENT_HOST=addresses.get(robot,'127.0.0.1'),AGENTICROS_OBSERVATION_PORT=8800 if network else free_port(),
                   AGENTICROS_JOIN_TOKEN=config['AGENTICROS_JOIN_TOKEN'])
            launcher.write_new(root/'lan'/('client-'+robot+'.json'),c)
            clients.append(c);run(c,['up','-d','--no-recreate','--wait','client'],True)
        run(config,['run','-d','--no-deps','--name',agent,'agenticros','sleep','infinity'])
        actual=json.loads(docker('inspect',agent,capture_output=True).stdout)[0]
        assert actual['Config']['User']=='agent' and not actual['HostConfig']['Privileged']
        assert actual['HostConfig']['NetworkMode']==(network or 'host') and actual['HostConfig']['CapDrop']==['ALL']
        mounts={m['Destination']:m for m in actual['Mounts']}
        assert set(mounts)=={'/workspace','/home/agent/.codex','/config/codex.toml'}
        assert mounts['/workspace']['Source']==str(root/'workspace')
        assert mounts['/workspace']['Type']=='bind' and not mounts['/config/codex.toml']['RW']
        assert mounts['/home/agent/.codex']['Type']=='volume'
        assert not any(x.startswith('AGENTICROS_JOIN_TOKEN=') for x in actual['Config']['Env'])
        (root/'mounts.json').write_text(json.dumps(actual['Mounts'],indent=2))
        if network:
            endpoints = {'agent':actual}
            for key,c,is_client,service in [('server',config,False,'server')]+[
                    (c['AGENTICROS_ROBOT_ID'],c,True,'client') for c in clients]:
                cid = subprocess.check_output(compose(c,is_client)+['ps','-q',service],
                                              cwd=ROOT,env=launcher.environment(c),text=True).strip()
                endpoints[key] = json.loads(docker('inspect',cid,capture_output=True).stdout)[0]
            assert len({item['NetworkSettings']['SandboxKey'] for item in endpoints.values()})==4
            for key,item in endpoints.items():
                assert item['HostConfig']['NetworkMode']==network
                assert item['NetworkSettings']['Networks'][network]['IPAddress']==addresses[key]
            (root/'network-inspection.json').write_text(json.dumps({key:{
                'ip':item['NetworkSettings']['Networks'][network]['IPAddress'],
                'namespace':item['NetworkSettings']['SandboxKey']} for key,item in endpoints.items()},indent=2))
        docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','python','-',input=BOOTSTRAP)
        command='source /workspace/ros_ws/install/setup.bash && python -'
        def probe(phase):
            return PROBE.replace('__PHASE__',repr(phase))
        docker('exec','-i',agent,'/usr/local/bin/agent-entrypoint','bash','-c',command,input=probe('normal'))
        proc=subprocess.Popen(['docker','exec','-i',agent,'/usr/local/bin/agent-entrypoint','bash','-c',command],stdin=subprocess.PIPE,text=True)
        proc.stdin.write(probe('disconnect'));proc.stdin.close()
        deadline=time.monotonic()+30
        while not (root/'workspace/evidence/disconnect-ready').exists():
            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('disconnect probe did not start')
            time.sleep(.2)
        run(config,['stop','server'])
        deadline=time.monotonic()+40
        while not (root/'workspace/evidence/lease-lost-confirmed').exists():
            if proc.poll() is not None or time.monotonic()>deadline:raise RuntimeError('lease loss was not reconciled')
            time.sleep(.2)
        # A recovered registry does not revive clients with an expired membership.
        run(config,['up','-d','--no-recreate','--wait','server'])
        (root/'workspace/evidence/registry-restarted').write_text('ready')
        assert proc.wait(timeout=50)==0
        for c in clients:
            cid=subprocess.check_output(compose(c,True)+['ps','-q','client'],cwd=ROOT,env=launcher.environment(c),text=True).strip()
            data=json.loads(docker('exec',cid,'cat','/tmp/lan-health.json',capture_output=True).stdout)
            assert not data['connected']
        (root/'result.json').write_text(json.dumps({'docker_integration':'PASS','physical_hosts':1,
            'network_namespaces':4 if network else 1,'addresses':addresses,
            'autonomous_adaptation':False,'task_evaluation':False,'restart_does_not_revive_clients':True},indent=2))
        print('PASS:',root/'result.json')
    finally:
        # Stop only these freshly created fixtures. Preserve all volumes and generated evidence.
        docker('stop',agent,capture_output=True) if subprocess.run(['docker','inspect',agent],capture_output=True).returncode==0 else None
        for c in clients:run(c,['stop','client'],True)
        run(config,['stop','server'])


if __name__=='__main__':
    main()
