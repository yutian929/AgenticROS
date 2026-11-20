"""One ROS node and cached clients across a remote robot process restart."""
import json
from pathlib import Path
import threading
import time
import uuid
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from std_srvs.srv import Trigger
from agenticros_driver_interfaces.action import Actuate
from agenticros_driver_interfaces.srv import Snapshot
from unique_identifier_msgs.msg import UUID
rclpy.init();node=rclpy.create_node('restart_probe')
executor=MultiThreadedExecutor(num_threads=3);executor.add_node(node)
worker=threading.Thread(target=executor.spin,daemon=True);worker.start()
client=node.create_client(Trigger,'/robots/robot_a/driver/status')
action=ActionClient(node,Actuate,'/robots/robot_a/driver/actuate')
root=Path('/workspace/evidence');rows=[]
def wait(future,timeout=10):
    event=threading.Event();future.add_done_callback(lambda _:event.set())
    assert event.wait(timeout),'request timeout; no resend'
    return future.result()
def status(timeout=2,phase='robot_restart'):
    row=dict(layer='ros_data',phase=phase,success=False)
    start=time.monotonic()
    if not client.wait_for_service(timeout_sec=2):
        row.update(error='discovery_failure',discovery_ms=(time.monotonic()-start)*1000);rows.append(row);return
    row['discovery_ms']=(time.monotonic()-start)*1000;start=time.monotonic()
    f=client.call_async(Trigger.Request());event=threading.Event();f.add_done_callback(lambda _:event.set())
    if not event.wait(timeout):
        row['error']='request_timeout';client.remove_pending_request(f);s=None
    else:
        try:s=json.loads(f.result().message);row['success']=True
        except Exception:row['error']='server_failure';s=None
    row['response_ms']=(time.monotonic()-start)*1000;rows.append(row);return s
try:
    assert client.wait_for_service(timeout_sec=20)
    old=status(timeout=10,phase='restart_initial');assert old
    assert action.wait_for_server(timeout_sec=20)
    (root/'restart-ready').write_text(old['session_id'])
    deadline=time.monotonic()+70
    while True:
        new=status()
        if new and new['session_id']!=old['session_id']:break
        assert time.monotonic()<deadline,'new session not observed'
        time.sleep(.5)
    def send(session):
        g=Actuate.Goal(session_id=session,positions=new['state']['joint_positions'],duration=2.)
        return wait(action.send_goal_async(g,goal_uuid=UUID(uuid=list(uuid.uuid4().bytes))))
    assert not send(old['session_id']).accepted
    snapshot=node.create_client(Snapshot,'/robots/robot_a/driver/observe')
    assert snapshot.wait_for_service(timeout_sec=20)
    rejected=json.loads(wait(snapshot.call_async(Snapshot.Request(session_id=old['session_id']))).snapshot_json)
    assert rejected['error']=='session_mismatch' and rejected['session_id']==new['session_id']
    handle=send(new['session_id']);assert handle.accepted
    receipt=json.loads(wait(handle.get_result_async(),45).result.receipt_json)
    assert receipt['session_id']==new['session_id'] and receipt['stop_confirmed']
    for _ in range(10):assert status()
    (root/'restart.json').write_text(json.dumps(dict(namespace='/robots/robot_a',old_session=old['session_id'],new_session=new['session_id'],
          old_session_rejected=True,cached_clients_reconnected=True),indent=2))
    print('robot restart PASS',flush=True)
finally:
    (root/'bench-reconnect.json').write_text(json.dumps(rows,indent=2))
    executor.shutdown();worker.join();node.destroy_node();rclpy.shutdown()
