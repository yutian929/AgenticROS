"""Public ROS-only peer in the Agent image; no simulator imports or truth."""
import json
import os
from pathlib import Path
import time

import rclpy
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import String
from std_srvs.srv import Trigger
from rclpy.qos import qos_profile_sensor_data


def check_alignment(node, namespace, statuses):
    from rclpy.action import ActionClient
    from geometry_msgs.msg import PoseStamped
    from aloha_driver_interfaces.action import MovePose, Recover
    poses, streams = {}, {}
    subs = []
    for arm in ('left', 'right'):
        subs.append(node.create_subscription(PoseStamped, namespace+'/'+arm+'/driver/ee_pose',
                    lambda msg, a=arm: poses.update({a: msg}), 10))
        subs.append(node.create_subscription(String, namespace+'/'+arm+'/driver/state',
                    lambda msg, a=arm: streams.update({a: json.loads(msg.data)}), 10))
    deadline = time.monotonic()+10
    while len(poses) < 2 or len(streams) < 2:
        assert time.monotonic() < deadline, 'aligned pose/state topics missing'
        rclpy.spin_once(node, timeout_sec=.1)
    def wait(future):
        rclpy.spin_until_future_complete(node, future, timeout_sec=30)
        assert future.done(), 'timeout; never replay the goal'
        return future.result()
    receipts = []
    for arm, status in statuses.items():
        required = {'reported_at_unix', 'simulation_ready', 'feedback_fresh', 'fault',
                    'recovery_goal_id', 'active_phase', 'active_operation', 'stop_evidence', 'receipts'}
        assert required <= set(status) and set(status) == set(streams[arm])
        frame = namespace.strip('/')+'/'+arm+'/arm_base'
        assert status['state']['pose_frame'] == frame == poses[arm].header.frame_id
        assert status['feedback_fresh'] and status['stop_confirmed']
        assert status['state']['joint_units'] == ['rad']*6+['m']
        client = ActionClient(node, MovePose, namespace+'/'+arm+'/driver/move_pose')
        recovery = ActionClient(node, Recover, namespace+'/'+arm+'/driver/recover')
        assert client.wait_for_server(timeout_sec=10) and recovery.wait_for_server(timeout_sec=10)
        goal = MovePose.Goal(session_id=status['session_id'], reference_frame=frame, duration=.5)
        p = status['state']['tcp_pose']
        for key in ('x', 'y', 'z'):setattr(goal.target.position, key, p['position'][key])
        for key in ('x', 'y', 'z', 'w'):setattr(goal.target.orientation, key, p['orientation'][key])
        handle = wait(client.send_goal_async(goal));assert handle.accepted
        receipt = json.loads(wait(handle.get_result_async()).result.receipt_json)
        assert receipt['complete'] and receipt['stop_confirmed'] and receipt['task_success'] is None, receipt
        assert receipt['pose_frame'] == frame and receipt['reference_frame'] == frame
        assert receipt['stop_evidence']['duration_s'] >= .25-1e-9
        for key in ('x', 'y', 'z'):
            assert abs(receipt['target_pose']['position'][key]-p['position'][key]) < 1e-9
        receipts.append(receipt)
        goal.session_id = 'stale-session'
        assert not wait(client.send_goal_async(goal)).accepted
        assert not wait(recovery.send_goal_async(Recover.Goal(session_id=status['session_id'], failed_goal_id='wrong'))).accepted
        client.destroy();recovery.destroy()
    return dict(canonical_pose_topics=True, canonical_arm_base_actions=True, canonical_status_schema=True,
                stale_session_rejected=True, invalid_recovery_rejected=True, receipts=receipts)


def main():
    rclpy.init()
    node = rclpy.create_node('scanner_public_observer')
    namespace = '/robot/'+os.environ['TEST_ROBOT_ID']
    states, frames, infos = [], {}, {}
    subscriptions = [node.create_subscription(String, namespace+'/scanner/state',
                      lambda msg: states.append(json.loads(msg.data)), 10)]
    for camera in ('left', 'right', 'head', 'overhead'):
        subscriptions.append(node.create_subscription(Image, namespace+'/'+camera+'/camera/color/image_raw',
            lambda msg, c=camera: frames.update({c: msg}), qos_profile_sensor_data))
    subscriptions.append(node.create_subscription(CameraInfo, namespace+'/head/camera/camera_info',
                         lambda msg: infos.update(head=msg), qos_profile_sensor_data))
    clients = {arm: node.create_client(Trigger, namespace+'/'+arm+'/driver/status') for arm in ('left', 'right')}
    try:
        deadline = time.monotonic()+60
        while len(states) < 5 or len(frames) < 4 or 'head' not in infos:
            assert time.monotonic() < deadline, (len(states), list(frames))
            rclpy.spin_once(node, timeout_sec=.1)
        statuses = {}
        for arm, client in clients.items():
            assert client.wait_for_service(timeout_sec=10)
            future = client.call_async(Trigger.Request())
            rclpy.spin_until_future_complete(node, future, timeout_sec=10)
            assert future.done(), arm+' status RPC timed out'
            response = future.result()
            assert response.success
            statuses[arm] = json.loads(response.message)
        session = statuses['left']['session_id']
        assert session == statuses['right']['session_id']
        permitted = {'robot_id', 'namespace', 'session_id', 'trigger_pressed', 'press_count',
                     'observed_at_unix', 'observed_at_sim'}
        assert set(states[-1]) == permitted, states[-1]
        assert all(s['session_id'] == session and s['robot_id'] == os.environ['TEST_ROBOT_ID'] for s in states)
        assert states[-1]['observed_at_sim'] > states[0]['observed_at_sim']
        assert time.time()-states[-1]['observed_at_unix'] < 5
        assert all(not s['trigger_pressed'] and s['press_count'] == 0 for s in states)
        assert all(s['control_available'] and not s['busy'] for s in statuses.values()), statuses
        for camera, frame in frames.items():
            assert frame.header.frame_id == namespace.strip('/')+'/'+camera+'/camera_optical_frame'
            assert frame.encoding == 'rgb8' and (frame.width, frame.height) == ((1280, 720) if camera == 'head' else (320, 240))
        assert abs(infos['head'].k[0]-720.3193) < 1e-5
        assert abs(infos['head'].k[4]-719.8599) < 1e-5
        assert abs(infos['head'].k[2]-640.3909) < 1e-5
        assert abs(infos['head'].k[5]-351.073) < 1e-5
        assert list(infos['head'].d) == [0.]*5
        report = dict(result='PASS', scope='public ROS across separate containers on one host',
                      session_id=session, scanner_samples=len(states), scanner_state=states[-1],
                      cameras=list(frames), status_rpc=True, both_arms_available=True,
                      physical_hosts=1, network_namespaces=2)
        if os.getenv('TEST_ALIGNMENT') == '1':
            report['alignment'] = check_alignment(node, namespace, statuses)
        Path('/workspace/ros.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
