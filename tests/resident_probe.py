"""Exercise the installed persistent client, including timeout and UUID fencing."""
import concurrent.futures
import json
from pathlib import Path
import sys
import subprocess
import time
import uuid
sys.path.insert(0,'/opt/instance')
from ros_client import request

count=int(sys.argv[1]);restart=len(sys.argv)>2
root=Path('/workspace/evidence')
rows=[]

def service(robot):
    start=time.monotonic()
    r=request(dict(op='service',endpoint=f'/robots/{robot}/driver/status',type='std_srvs/srv/Trigger'))
    rows.append(dict(layer='resident_rpc',phase='restarted' if restart else 'resident',robot=robot,
                     success='error' not in r, total_ms=(time.monotonic()-start)*1000,**r))
    assert 'error' not in r,r
    return json.loads(r['response']['message'])

try:
    for _ in range(count):service('robot_a')
    for _ in range(count//2):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(service,['robot_a','robot_b']))
    for _ in range(10 if not restart else 2):
        start=time.monotonic()
        reply=subprocess.run(['agenticros-ros','request',json.dumps(dict(op='service',endpoint='/robots/robot_a/driver/status',type='std_srvs/srv/Trigger'))],capture_output=True,text=True)
        result=json.loads(reply.stdout)
        rows.append(dict(layer='resident_cli_cold',phase='restarted' if restart else 'resident',success=reply.returncode==0,total_ms=(time.monotonic()-start)*1000,**result))
        assert reply.returncode==0,result
    state=service('robot_a')
    record=root/'resident-goal.json'
    if restart:
        goal=json.loads(record.read_text())
        goal['request']['session_id']=state['session_id']
        assert request(goal)['error']=='duplicate_uuid'
        # Reconciliation still works without the original in-memory goal handle.
        result=request(dict(op='result',goal_id=goal['goal_id'],timeout=.01))
        assert result['replay_allowed'] is False and 'response' in result['reconciliation']
    else:
        q=state['state']['joint_positions']
        goal=dict(op='goal',endpoint='/robots/robot_a/driver/actuate',type='agenticros_driver_interfaces/action/Actuate',
                  goal_id=uuid.uuid4().hex,status_endpoint='/robots/robot_a/driver/status',
                  request=dict(session_id=state['session_id'],positions=q,duration=20.),timeout=5)
        record.write_text(json.dumps(goal))
        sent=request(goal);assert sent.get('accepted'),sent
        assert request(goal)['error']=='duplicate_uuid'
        result=request(dict(op='result',goal_id=goal['goal_id'],timeout=.001))
        assert result['error']=='request_timeout' and result['replay_allowed'] is False
        assert 'response' in result['reconciliation']
        assert json.loads(result['reconciliation']['response']['message'])['active_goal_id']==goal['goal_id']
        cancel=request(dict(op='cancel',goal_id=goal['goal_id']));assert cancel['cancel']['response']['goals_canceling']
        result=request(dict(op='result',goal_id=goal['goal_id'],timeout=60))
        receipt=json.loads(result['result']['result']['receipt_json'])
        assert receipt['reason']=='cancelled' and receipt['stop_confirmed'] and receipt['goal_id']==goal['goal_id']
        (root/'resident-action.json').write_text(json.dumps(dict(sent=sent,receipt=receipt,timeout_reconciled=True,uuid_replay_rejected=True),indent=2))
    # Subscription is kept alive across lightweight CLI requests.
    request(dict(op='status'));time.sleep(2)
    status=request(dict(op='status'))
    assert len(status['robots'])==2,status
    assert all(r['ros_discoverable'] and r['observation_fresh'] for r in status['robots']),status
    (root/('resident-status-restarted.json' if restart else 'resident-status.json')).write_text(json.dumps(status,indent=2))
finally:
    (root/('bench-resident-restarted.json' if restart else 'bench-resident.json')).write_text(json.dumps(rows,indent=2))
print('resident PASS '+str(len(rows)),flush=True)
