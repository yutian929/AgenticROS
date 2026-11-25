"""Private disposable MuJoCo/ROS checks, including injected stop failure."""
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, '/opt/body')
import numpy as np
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from geometry_msgs.msg import PoseStamped
from std_msgs.msg import String
from std_srvs.srv import Trigger
from aloha_driver_interfaces.action import Actuate, MovePose, Recover
from driver import Driver
from world import World
from cartesian import quat_matrix


def wait(future, timeout=30):
    deadline = time.monotonic()+timeout
    while not future.done():
        assert time.monotonic() < deadline, 'timeout; never replay a goal'
        time.sleep(.01)
    return future.result()


def main():
    out = Path('/tmp/test-evidence');out.mkdir(exist_ok=True)
    rclpy.init();world = World()
    drivers = [Driver(world, arm) for arm in ('left', 'right')]
    node = rclpy.create_node('alignment_check')
    executor = MultiThreadedExecutor(num_threads=8)
    for n in [*drivers, node]:executor.add_node(n)
    threading.Thread(target=executor.spin, daemon=True).start()
    clients = {(arm, name): ActionClient(node, cls, '/'+arm+'/driver/'+name)
               for arm in ('left', 'right') for name, cls in [('actuate', Actuate), ('move_pose', MovePose), ('recover', Recover)]}
    statuses = {arm: node.create_client(Trigger, '/'+arm+'/driver/status') for arm in ('left', 'right')}
    poses, streams = {}, {}
    subs = []
    for arm in ('left', 'right'):
        subs.append(node.create_subscription(PoseStamped, '/'+arm+'/driver/ee_pose', lambda msg, a=arm: poses.update({a: msg}), 10))
        subs.append(node.create_subscription(String, '/'+arm+'/driver/state', lambda msg, a=arm: streams.update({a: json.loads(msg.data)}), 10))
    def state(arm='left'):
        return json.loads(wait(statuses[arm].call_async(Trigger.Request())).message)
    def send(name, goal, arm='left'):
        return wait(clients[arm, name].send_goal_async(goal))
    def result(handle):
        assert handle.accepted
        return json.loads(wait(handle.get_result_async()).result.receipt_json)
    reports = []
    try:
        for client in clients.values():assert client.wait_for_server(timeout_sec=10)
        for client in statuses.values():assert client.wait_for_service(timeout_sec=10)
        time.sleep(2)
        for arm in ('left', 'right'):
            s = state(arm)
            assert s['feedback_fresh'] and s['simulation_ready'] and s['stop_confirmed'], s
            assert s['state']['pose_frame'] == arm+'/arm_base' == poses[arm].header.frame_id
            assert set(s) == set(streams[arm])
            assert s['state']['joint_units'] == ['rad']*6+['m']
            assert len(s['state']['finger_joint_positions']) == 2
            # Full arm-base names resolve to the actual layout, not an identity alias.
            g = MovePose.Goal(session_id=world.session, reference_frame=arm+'/arm_base', duration=.5)
            p = s['state']['tcp_pose']
            mount_profile = world.layout['arms'][arm]
            mount = np.array(mount_profile['position_m'])
            w, x, y, z = mount_profile['quaternion_wxyz']
            axes = quat_matrix([x, y, z, w]).T
            assert np.allclose(axes@(np.array(s['state']['tcp_xyz'])-mount),
                               [p['position'][k] for k in ('x', 'y', 'z')], atol=1e-9)
            assert np.allclose(axes@quat_matrix(s['state']['tcp_quaternion_xyzw']),
                               quat_matrix([p['orientation'][k] for k in ('x', 'y', 'z', 'w')]))
            for key in ('x', 'y', 'z'):setattr(g.target.position, key, p['position'][key])
            for key in ('x', 'y', 'z', 'w'):setattr(g.target.orientation, key, p['orientation'][key])
            r = result(send('move_pose', g, arm));reports.append(r)
            assert r['complete'] and r['pose_frame'] == arm+'/arm_base' and r['task_success'] is None, r
            assert np.linalg.norm(np.array(list(r['target_pose']['position'].values()))-list(p['position'].values())) < 1e-9
            g.reference_frame = 'another_robot/'+arm+'/arm_base'
            assert not send('move_pose', g, arm).accepted
        driver = drivers[0]
        # Disable the yaw servo on this disposable arm to create a stationary,
        # unreachable raw target. Rest alone must not count as target completion.
        aid = driver.sim.aids[0]
        with world.lock:
            gain, bias = world.model.actuator_gainprm[aid].copy(), world.model.actuator_biasprm[aid].copy()
            world.model.actuator_gainprm[aid] = 0
            world.model.actuator_biasprm[aid] = 0
        try:
            q = state()['state']['joint_positions'];q[0] += .15
            blocked = result(send('actuate', Actuate.Goal(session_id=world.session, positions=q, duration=.2)))
            assert not blocked['complete'] and blocked['stop_confirmed'] and blocked['task_success'] is None, blocked
            assert blocked['reason'] == 'target_tolerance_not_reached' and state()['control_available']
            reports.append(blocked)
        finally:
            with world.lock:
                world.model.actuator_gainprm[aid], world.model.actuator_biasprm[aid] = gain, bias
        s = state()
        q = s['state']['joint_positions'];q[0] += .01
        h = send('actuate', Actuate.Goal(session_id=world.session, positions=q, duration=5.))
        assert h.accepted
        # Inject only a failed reconciliation result, never a false successful stop.
        with patch.object(driver, 'hold', return_value=False):
            assert wait(h.cancel_goal_async()).goals_canceling
            failed = result(h)
        assert not failed['stop_confirmed'] and failed['task_success'] is None
        gid = failed['goal_id']
        assert state()['recovery_goal_id'] == gid
        time.sleep(.5)
        assert state()['recovery_required'], 'never silently unlock a stopped arm'
        assert not send('actuate', Actuate.Goal(session_id=world.session, positions=q, duration=.5)).accepted
        assert not send('recover', Recover.Goal(session_id='stale', failed_goal_id=gid)).accepted
        assert not send('recover', Recover.Goal(session_id=world.session, failed_goal_id='wrong')).accepted
        recover = Recover.Goal(session_id=world.session, failed_goal_id=gid)
        h = send('recover', recover)
        assert h.accepted and wait(h.cancel_goal_async()).goals_canceling
        cancelled = result(h)
        assert cancelled['reason'] == 'cancelled' and state()['recovery_required']
        # The other arm's bias-compensated servo input can change as physics
        # advances. Recovery owns only the selected arm's existing hold.
        before = world.data.ctrl[:7].copy()
        h = send('recover', recover)
        assert h.accepted
        assert not send('recover', recover).accepted
        assert not send('actuate', Actuate.Goal(session_id=world.session, positions=q, duration=.5)).accepted
        recovered = result(h);reports.extend([failed, cancelled, recovered])
        assert recovered['complete'] and recovered['reason'] == 'recovered' and recovered['stop_confirmed'], recovered
        assert recovered['failed_goal_id'] == gid and recovered['executed'] == 'none'
        assert recovered['stop_evidence']['duration_s'] >= .25-1e-9
        assert state()['receipts'][gid] == failed
        assert not state()['recovery_required'] and state()['recovery_goal_id'] is None
        assert np.array_equal(before, world.data.ctrl[:7]), 'recovery must not change servo targets'
        # Freeze the disposable physics worker to test a stale but still queryable ROS node.
        world.stop.set();world.worker.join(timeout=5)
        world.updated = time.time()-6
        driver.recovery_required, driver.recovery_goal_id = True, gid
        stale = state()
        assert not stale['feedback_fresh'] and not stale['control_available'] and not stale['stop_confirmed']
        assert not send('recover', recover).accepted
        assert not send('actuate', Actuate.Goal(session_id=world.session, positions=q, duration=.5)).accepted
        (out/'integration.json').write_text(json.dumps(dict(result='PASS', receipts=reports,
            scope='isolated MuJoCo/ROS with explicit stop-failure injection; not task evaluation',
            cases=['canonical_pose_frames', 'actual_layout_arm_bases', 'state_topic_schema',
                   'stopped_but_unreached_raw_target', 'failed_uuid_identity', 'recovery_cancellation', 'exclusive_recovery',
                   'new_stop_window', 'unchanged_hold_targets_and_failed_receipt', 'stale_physics_rejection']), indent=2))
        print('PASS: ALOHA/Quanta interface alignment and explicit recovery')
    finally:
        for driver in drivers:driver.shutting_down.set()
        executor.shutdown(timeout_sec=10)
        for driver in drivers:driver.destroy_node()
        node.destroy_node();world.close();rclpy.shutdown()


if __name__ == '__main__':main()
