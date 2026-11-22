"""Humble ROS client: exercise public IDL across containers, never task scoring."""
import json
import os
import time
from pathlib import Path
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from std_srvs.srv import Trigger
from sensor_msgs.msg import Image, CameraInfo
from std_msgs.msg import String
from quanta_driver_interfaces.action import Actuate, Recover

rclpy.init()
node = Node('quanta_protocol_probe')
namespace = '/robot/'+os.environ['TEST_ROBOT_ID']

def wait(future, timeout=15.):
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
    assert future.done(), 'RPC timeout; original UUID must be reconciled, never replayed'
    return future.result()

def status(arm):
    client = node.create_client(Trigger, namespace+'/'+arm+'/driver/status')
    assert client.wait_for_service(timeout_sec=30), 'discovery failed'
    result = wait(client.call_async(Trigger.Request()))
    node.destroy_client(client)
    assert result.success
    return json.loads(result.message)

left = status('left')
if left['recovery_required']:
    assert os.environ.get('TEST_REQUIRE_IMAGES') != '1'
    recovery = ActionClient(node, Recover, namespace+'/left/driver/recover')
    assert recovery.wait_for_server(timeout_sec=30)
    failed = left['recovery_goal_id']
    original = dict(left['receipts'][failed])
    assert not wait(recovery.send_goal_async(Recover.Goal(session_id='old-session', failed_goal_id=failed))).accepted
    assert not wait(recovery.send_goal_async(Recover.Goal(session_id=left['session_id'], failed_goal_id='wrong'))).accepted
    recovered = wait(recovery.send_goal_async(Recover.Goal(session_id=left['session_id'], failed_goal_id=failed)))
    assert recovered.accepted
    receipt = json.loads(wait(recovered.get_result_async()).result.receipt_json)
    assert receipt['reason']=='recovered' and receipt['stop_confirmed'] and receipt['task_success'] is None
    left = status('left')
    assert left['receipts'][failed] == original
    Path('/workspace/recovery.json').write_text(json.dumps(receipt,indent=2))
assert left['control_available']
if os.environ.get('TEST_REQUIRE_IMAGES') == '1':
    frames, infos = {}, {}
    scanner_state = {}
    scanner_subscription = node.create_subscription(String, namespace+'/scanner/state',
        lambda message: scanner_state.update(json.loads(message.data)), 2)
    info_subscriptions = [node.create_subscription(CameraInfo, namespace+'/'+name+'/camera/camera_info',
        lambda message, n=name: infos.update({n: message}), 2) for name in ('left','right','head')]
    subscriptions = [node.create_subscription(Image, namespace+'/'+name+'/camera/color/image_raw',
                     lambda message, n=name: frames.update({n: message}), 2) for name in ('left','right','head')]
    deadline = time.monotonic()+30
    while (len(frames) < 3 or len(infos) < 3 or not scanner_state) and time.monotonic() < deadline:
        rclpy.spin_once(node, timeout_sec=.2)
    assert len(frames) == 3 and len(infos) == 3, 'missing camera frames/calibration'
    assert scanner_state.get('session_id') == left['session_id'], scanner_state
    assert isinstance(scanner_state.get('trigger_pressed'), bool), scanner_state
    assert not scanner_state['trigger_pressed'] and scanner_state['press_count'] == 0, scanner_state
    assert not any(key in scanner_state for key in ('pose', 'image', 'target', 'barcode_text')), scanner_state
    Path('/workspace/scanner_state.json').write_text(json.dumps(scanner_state, indent=2))
    body = status('left')['body']
    assert body['mode'] == 'stationary_tabletop'
    assert abs(body['joint_positions'][0]-.379) < .03, body
    assert abs(body['joint_positions'][2]+3.141592653589793/12) < .03, body
    assert max(map(abs, body['base_position'])) < .01, body
    assert abs(body['base_orientation_wxyz'][3]) > .999, body
    for name, frame in frames.items():
        size = (1280,720) if name == 'head' else (640,480)
        assert frame.encoding == 'rgb8' and (frame.width,frame.height) == size
        info = infos[name]
        assert (info.width,info.height) == size
        assert info.distortion_model == ('plumb_bob' if name == 'head' else 'equidistant')
        assert abs(info.k[0]-(720.3193 if name == 'head' else 699.3900146484375*.4)) < .001
        assert len(info.d) == (5 if name == 'head' else 4)
        assert len(set(frame.data)) > 8, 'empty camera image'
        # Portable PPM evidence; no optional image package in the Agent.
        Path('/workspace/'+name+'.ppm').write_bytes(f'P6\n{frame.width} {frame.height}\n255\n'.encode()+bytes(frame.data))
action = ActionClient(node, Actuate, namespace+'/left/driver/actuate')
assert action.wait_for_server(timeout_sec=30)
q = [min(hi, max(lo, value)) for value, (lo, hi) in
     zip(left['state']['joint_positions'], left['state']['joint_limits'])]
stale = Actuate.Goal(session_id='old-session', positions=q, duration=.5)
assert not wait(action.send_goal_async(stale)).accepted
q = list(q);q[0] += .03
handle = wait(action.send_goal_async(Actuate.Goal(session_id=left['session_id'], positions=q, duration=1.)))
assert handle.accepted
assert not wait(action.send_goal_async(Actuate.Goal(session_id=left['session_id'], positions=q, duration=1.))).accepted
right_state = status('right')
assert right_state['control_available']
right_action = ActionClient(node, Actuate, namespace+'/right/driver/actuate')
assert right_action.wait_for_server(timeout_sec=30)
right_q = [min(hi, max(lo, value)) for value, (lo, hi) in
           zip(right_state['state']['joint_positions'], right_state['state']['joint_limits'])]
right_q[0] += .03
right_q[-1] = .6
right_handle = wait(right_action.send_goal_async(Actuate.Goal(
    session_id=right_state['session_id'], positions=right_q, duration=1.)))
assert right_handle.accepted
result = wait(handle.get_result_async(), 30)
receipt = json.loads(result.result.receipt_json)
assert receipt['complete'] and receipt['stop_confirmed'], receipt
assert receipt['task_success'] is None
assert receipt['goal_id'] == bytes(handle.goal_id.uuid).hex()
assert receipt['goal_id'] in status('left')['receipts']
right_receipt = json.loads(wait(right_handle.get_result_async(), 30).result.receipt_json)
Path('/workspace/right_status.json').write_text(json.dumps(status('right'),indent=2))
assert right_receipt['complete'] and right_receipt['stop_confirmed'], right_receipt
gripper_evidence = None
if os.environ.get('TEST_REQUIRE_IMAGES') == '1':
    q[-1] = .6
    grip = wait(action.send_goal_async(Actuate.Goal(session_id=left['session_id'], positions=q, duration=1.)))
    assert grip.accepted
    grip_receipt = json.loads(wait(grip.get_result_async(), 30).result.receipt_json)
    assert grip_receipt['complete'], grip_receipt
    fingers = status('left')['state']['finger_joint_positions']
    expected_right = 4.1827 * 3.141592653589793 / 180 - .5 * q[-1]
    assert abs(fingers[1]-expected_right) < .03 and abs(fingers[0]+fingers[1]) < .03, fingers
    right_fingers = status('right')['state']['finger_joint_positions']
    assert abs(right_fingers[1]-expected_right) < .03 and abs(sum(right_fingers)) < .03, right_fingers
    gripper_evidence = dict(receipt=grip_receipt, fingers=fingers,
                            right_arm_fingers=right_fingers, expected_right=expected_right)
q[0] += .05
cancel = wait(action.send_goal_async(Actuate.Goal(session_id=left['session_id'], positions=q, duration=3.)))
assert cancel.accepted
for _ in range(10):rclpy.spin_once(node, timeout_sec=.02)
assert wait(cancel.cancel_goal_async()).goals_canceling
cancel_result = wait(cancel.get_result_async(), 30)
cancel_receipt = json.loads(cancel_result.result.receipt_json)
assert cancel_receipt['reason'] == 'cancelled' and cancel_receipt['stop_confirmed'], cancel_receipt
final = status('left')
assert final['stop_confirmed'] and final['control_available']
Path('/workspace/result.json').write_text(json.dumps(dict(result='PASS',scope=os.environ.get('TEST_SCOPE','synthetic ROS transport'),
    client_ros='humble',server_ros='jazzy',physical_hosts=1,receipt=receipt,right_arm_receipt=right_receipt,cancellation=cancel_receipt,
    gripper_mimic=gripper_evidence,
    body=body if os.environ.get('TEST_REQUIRE_IMAGES') == '1' else None,
    camera_calibration={n: dict(width=i.width,height=i.height,k=list(i.k),d=list(i.d),
        distortion_model=i.distortion_model) for n,i in infos.items()} if os.environ.get('TEST_REQUIRE_IMAGES') == '1' else None),indent=2))
node.destroy_node()
rclpy.shutdown()
