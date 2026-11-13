"""Raw-driver regression: status, observation, rejection, cancellation, HTTP boundary.
Run with ROS overlay for docker/interfaces; never during an active agent task.
"""
import json
import time
import urllib.error
import urllib.request
import uuid

import rclpy
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from rosbody_driver_interfaces.action import Actuate
from rosbody_driver_interfaces.srv import Snapshot


def main():
    rclpy.init()
    node = rclpy.create_node('raw_driver_check_' + uuid.uuid4().hex[:8])
    def wait(future):
        rclpy.spin_until_future_complete(node, future, timeout_sec=10)
        assert future.done(), 'ROS timeout'
        return future.result()
    state = node.create_client(Trigger, '/driver/status')
    assert state.wait_for_service(timeout_sec=15)
    def status():
        return json.loads(wait(state.call_async(Trigger.Request())).message)
    before = status()
    assert not before['busy'] and not before['recovery_required']
    action = ActionClient(node, Actuate, '/driver/actuate')
    assert action.wait_for_server(timeout_sec=10)
    bad = wait(action.send_goal_async(Actuate.Goal(session_id=before['session_id'], positions=[999.,0.,0.,0.,0.], duration=1.)))
    assert not bad.accepted
    assert status()['state']['sim_time'] == before['state']['sim_time']
    q = [max(lo, min(hi, v)) for v, lo, hi in zip(before['state']['joint_positions'],
         [-.38,-.26,-.175,0.,0.], [.38,.26,.13,.04,.04])]
    q[0] += .01 if q[0] < .36 else -.01
    goal = Actuate.Goal(session_id=before['session_id'], positions=q, duration=2.)
    handle = wait(action.send_goal_async(goal))
    assert handle.accepted
    concurrent = wait(action.send_goal_async(goal))
    assert not concurrent.accepted
    time.sleep(.1)
    active = status()
    assert active['busy'] and active['active_goal_id'] == bytes(handle.goal_id.uuid).hex()
    cancel_ack = wait(handle.cancel_goal_async())
    assert cancel_ack.goals_canceling, 'cancel acknowledgment missing; stop not confirmed'
    receipt = json.loads(wait(handle.get_result_async()).result.receipt_json)
    assert receipt['reason'] == 'cancelled' and receipt['stop_confirmed']
    assert receipt['goal_id'] in status()['receipts']
    assert not status()['busy']
    # A separate ordinary servo completion, not a grasp or task test.
    done = wait(action.send_goal_async(Actuate.Goal(session_id=before['session_id'], positions=q, duration=.5)))
    assert done.accepted
    completed = json.loads(wait(done.get_result_async()).result.receipt_json)
    assert completed['complete'] and completed['stop_confirmed']
    assert completed['session_id'] == before['session_id']
    obs = node.create_client(Snapshot, '/driver/observe')
    assert obs.wait_for_service(timeout_sec=10)
    shot = json.loads(wait(obs.call_async(Snapshot.Request())).snapshot_json)
    assert shot['snapshot_id'].startswith(before['session_id'])
    print(json.dumps({'raw_motion_check': 'PASS', 'session_id': before['session_id'],
                      'cancel_receipt': receipt, 'completion_receipt': completed,
                      'snapshot_id': shot['snapshot_id']}))
    node.destroy_node()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
