"""Actual ROS Cartesian motions in a disposable ALOHA world, never a live robot."""
import json
from pathlib import Path
import sys
import threading
import time
import uuid
sys.path.insert(0, '/opt/body')
import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from unique_identifier_msgs.msg import UUID
from std_srvs.srv import Trigger
from aloha_driver_interfaces.action import Actuate, MovePose, MoveDelta
from driver import Driver
from world import World
from cartesian import quat_matrix, matrix_quat, resolve_target, rotation_matrix, rotation_vector


def wait(future, timeout=50):
    end = time.monotonic()+timeout
    while not future.done():
        if time.monotonic()>end:
            raise TimeoutError('No action replay after timeout')
        time.sleep(.01)
    return future.result()


def main():
    # Analytic frame convention checks independent of solver and robot motion.
    r90 = np.array([[0.,-1,0],[1,0,0],[0,0,1]])
    start = (np.array([1.,2.,3.]), r90)
    dq = [np.sin(.1),0,0,np.cos(.1)]
    p, r = resolve_target(start, [1,0,0], dq, 'tcp', True)
    assert np.allclose(p,[1,3,3]) and np.allclose(r, r90@quat_matrix(dq))
    p, r = resolve_target(start, [1,0,0], dq, 'base', True)
    assert np.allclose(p,[2,2,3]) and np.allclose(r, quat_matrix(dq)@r90)
    for angle in (0., .001, 1., np.pi):
        v=np.array([angle,0,0]); assert np.allclose(rotation_matrix(rotation_vector(rotation_matrix(v))),rotation_matrix(v))
    rclpy.init()
    world=World()
    drivers=[Driver(world,arm) for arm in ('left','right')]
    node=rclpy.create_node('cartesian_test')
    executor=MultiThreadedExecutor(num_threads=8)
    for n in [*drivers,node]: executor.add_node(n)
    threading.Thread(target=executor.spin,daemon=True).start()
    clients={(arm,kind):ActionClient(node,cls,f'/{arm}/driver/{kind}') for arm in ('left','right')
             for kind,cls in [('move_pose',MovePose),('move_delta',MoveDelta),('actuate',Actuate)]}
    statuses={arm:node.create_client(Trigger,f'/{arm}/driver/status') for arm in ('left','right')}
    reports=[]
    def state(arm):
        return json.loads(wait(statuses[arm].call_async(Trigger.Request())).message)
    def goal(arm, kind, frame='base', translation=(0,0,.008), rotation=(0,0,0,1), duration=1.5):
        g=(MoveDelta.Goal() if kind=='move_delta' else MovePose.Goal())
        g.session_id=world.session;g.reference_frame=frame;g.duration=float(duration)
        p=g.translation if kind=='move_delta' else g.target.position
        q=g.rotation if kind=='move_delta' else g.target.orientation
        p.x,p.y,p.z=map(float,translation);q.x,q.y,q.z,q.w=map(float,rotation)
        return g
    def send(arm,kind,g):
        uid=uuid.uuid4(); feedback=[]
        h=wait(clients[arm,kind].send_goal_async(g,goal_uuid=UUID(uuid=list(uid.bytes)),
                   feedback_callback=lambda msg:feedback.append(msg.feedback)))
        return h,uid.hex,feedback
    def wait_executing(sent):
        end=time.monotonic()+20
        while not any(f.phase=='executing' for f in sent[2]):
            if time.monotonic()>end: raise TimeoutError('execution feedback missing')
            time.sleep(.01)
    def result(arm, sent, success=True):
        h,gid,feedback=sent; assert h.accepted
        r=json.loads(wait(h.get_result_async()).result.receipt_json)
        reports.append(r)
        Path('/observations/integration.json').write_text(json.dumps(dict(receipts=reports),indent=2))
        print(arm,r['reason'],r.get('position_error'),r.get('orientation_error'),flush=True)
        assert r['goal_id']==gid and r['session_id']==world.session and r['arm']==arm
        assert r['stop_confirmed'],r
        assert r['complete'] == success,r
        assert gid in state(arm)['receipts'] and not state(arm)['busy']
        if success:
            assert feedback and r['position_error']<=r['position_tolerance'] and r['orientation_error']<=r['orientation_tolerance']
        return r
    try:
        for c in clients.values(): assert c.wait_for_server(timeout_sec=20)
        for c in statuses.values(): assert c.wait_for_service(timeout_sec=20)
        time.sleep(2)
        # Both arms, both action kinds, both reference frames, rotations and translations.
        for arm in ('left','right'):
            for kind in ('move_delta','move_pose'):
                for frame in ('base','tcp'):
                    before=state(arm)['state']; r0=quat_matrix(before['tcp_quaternion_xyzw'])
                    dr=rotation_matrix(np.array([.015,0,0]))
                    t=np.array([0.,0.,.006])
                    if kind=='move_pose' and frame=='base':
                        t=np.array(before['tcp_xyz'])+t
                        dr=dr@r0
                    g=goal(arm,kind,frame,t,matrix_quat(dr))
                    receipt=result(arm,send(arm,kind,g))
                    p0=np.array(receipt['start_tcp_xyz']);r0=quat_matrix(receipt['start_tcp_quaternion_xyzw'])
                    expected_p=p0+r0@t if frame=='tcp' else (p0+t if kind=='move_delta' else t)
                    expected_r=r0@dr if frame=='tcp' else (dr@r0 if kind=='move_delta' else dr)
                    assert np.allclose(receipt['target_tcp_xyz'],expected_p)
                    assert np.allclose(quat_matrix(receipt['target_tcp_quaternion_xyzw']),expected_r)
                    actual=receipt['state']
                    assert np.linalg.norm(np.array(actual['tcp_xyz'])-expected_p)<=.003
                    assert abs(actual['joint_positions'][-1]-before['joint_positions'][-1])<.002
        # Reject malformed inputs and stale sessions before occupying the arm.
        invalid=[goal('left','move_delta',frame='world'),goal('left','move_delta',rotation=(0,0,0,0)),
                 goal('left','move_delta',translation=(float('nan'),0,0)),goal('left','move_delta',duration=-1)]
        stale=goal('left','move_delta');stale.session_id='stale';invalid.append(stale)
        for g in invalid: assert not send('left','move_delta',g)[0].accepted
        before=state('left')['state']['joint_positions']
        failure=result('left',send('left','move_pose',goal('left','move_pose',translation=(5,5,5),duration=.1)),False)
        assert failure['executed']=='none' and 'ik_failed' in failure['reason'],failure
        assert np.max(np.abs(np.array(state('left')['state']['joint_positions'])-before))<.01
        too_fast=goal('left','move_delta','tcp',translation=(0,0,0),
                      rotation=matrix_quat(rotation_matrix(np.array([.6,0,0]))),duration=.1)
        failure=result('left',send('left','move_delta',too_fast),False)
        assert failure['executed']=='none' and 'joint_speed_limit' in failure['reason'],failure
        planning=send('left','move_delta',goal('left','move_delta',duration=30.))
        assert wait(planning[0].cancel_goal_async()).goals_canceling
        failure=result('left',planning,False)
        assert failure['reason']=='cancelled' and failure['executed']=='none',failure
        # Other arm may run concurrently; raw and Cartesian actions cannot contend.
        left=send('left','move_delta',goal('left','move_delta',duration=5.))
        right=send('right','move_delta',goal('right','move_delta',duration=5.))
        assert left[0].accepted and right[0].accepted
        assert not send('left','move_pose',goal('left','move_pose',frame='tcp',translation=(0,0,0)))[0].accepted
        raw=Actuate.Goal(session_id=world.session,positions=state('left')['state']['joint_positions'],duration=1.)
        assert not send('left','actuate',raw)[0].accepted
        for sent in (left,right): wait_executing(sent)
        time.sleep(.2)
        for sent in (left,right): assert wait(sent[0].cancel_goal_async()).goals_canceling
        for arm,sent in [('left',left),('right',right)]:
            r=result(arm,sent,False)
            assert r['reason']=='cancelled' and r['executed']=='some'
        raw.duration=5.
        sent_raw=send('left','actuate',raw)
        assert sent_raw[0].accepted
        assert not send('left','move_delta',goal('left','move_delta'))[0].accepted
        assert wait(sent_raw[0].cancel_goal_async()).goals_canceling
        assert result('left',sent_raw,False)['reason']=='cancelled'
        sent=send('left','move_delta',goal('left','move_delta',duration=5.))
        wait_executing(sent);drivers[0].shutting_down.set()
        assert result('left',sent,False)['reason']=='shutdown'
        drivers[0].shutting_down.clear()
        # Lease loss is latched by the registration layer; exercise execution response.
        class Lease:
            ok=True
            def valid(self): return self.ok
        class Lan:
            lease=Lease()
            membership={'namespace':''}
            def identity(self): return {}
        drivers[0].lan=Lan()
        sent=send('left','move_delta',goal('left','move_delta',duration=5.))
        wait_executing(sent);drivers[0].lan.lease.ok=False
        assert result('left',sent,False)['reason']=='lease_lost'
        assert not send('left','move_delta',goal('left','move_delta'))[0].accepted
        Path('/observations/integration.json').write_text(json.dumps(dict(passed=True,receipts=reports,
            cases=['both_arms_both_actions_both_frames','rotation_and_translation','invalid_and_stale',
                   'unreachable_no_execution','joint_speed_limit','planning_cancel','shutdown','shared_exclusion','concurrent_arms','cancel','lease_loss']),indent=2))
        print('PASS: Cartesian integration',flush=True)
    finally:
        for d in drivers: d.shutting_down.set()
        executor.shutdown(timeout_sec=10)
        for d in drivers:d.destroy_node()
        node.destroy_node();world.close();rclpy.shutdown()


if __name__=='__main__':main()
