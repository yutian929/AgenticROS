"""Run inside the ALOHA image: shared-world ROS actions and cancellation."""
import json
from pathlib import Path
import sys
import threading
import time
import uuid
sys.path.insert(0, '/opt/body')
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from std_srvs.srv import Trigger
from unique_identifier_msgs.msg import UUID
from aloha_driver_interfaces.action import Actuate
from aloha_driver_interfaces.srv import Snapshot
from driver import Driver, CameraPublisher
from world import World
import numpy as np
from sensor_msgs.msg import CameraInfo
from tf2_msgs.msg import TFMessage
from rclpy.qos import qos_profile_sensor_data


def wait(future, timeout=30):
    deadline = time.monotonic()+timeout
    while not future.done():
        if time.monotonic() > deadline:
            raise TimeoutError('no replay after timeout')
        time.sleep(.01)
    return future.result()


def main():
    rclpy.init()
    world = World()
    drivers = [Driver(world, arm) for arm in ('left', 'right')]
    overhead = CameraPublisher(world, 'overhead')
    client = rclpy.create_node('aloha_integration')
    executor = MultiThreadedExecutor(num_threads=8)
    for node in [*drivers, overhead, client]:
        executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True)
    thread.start()
    try:
        actions = [ActionClient(client, Actuate, '/'+arm+'/driver/actuate') for arm in ('left','right')]
        statuses = [client.create_client(Trigger, '/'+arm+'/driver/status') for arm in ('left','right')]
        for action in actions:
            assert action.wait_for_server(timeout_sec=20)
        for status in statuses:
            assert status.wait_for_service(timeout_sec=20)
        infos, transforms = {}, {}
        for camera in ('left', 'overhead', 'right'):
            client.create_subscription(CameraInfo, '/'+camera+'/camera/camera_info',
                lambda msg, camera=camera: infos.update({camera: msg}), qos_profile_sensor_data)
        client.create_subscription(TFMessage, '/tf',
            lambda msg: transforms.update({t.child_frame_id:t for t in msg.transforms}), 100)
        time.sleep(2)
        before = world.render.submit(drivers[1].sim.observe).result()[2]
        states = [json.loads(wait(s.call_async(Trigger.Request())).message) for s in statuses]
        assert states[0]['session_id'] == states[1]['session_id'] == world.session
        def send(index, session=None, duration=1.):
            q = list(states[index]['state']['joint_positions'])
            q[0] += .01 if index == 0 else -.01
            goal = Actuate.Goal(session_id=session or world.session, positions=q, duration=duration)
            gid = UUID(uuid=list(uuid.uuid4().bytes))
            return actions[index].send_goal_async(goal, goal_uuid=gid), bytes(gid.uuid).hex()
        wrong, _ = send(0, 'wrong-session')
        assert not wait(wrong).accepted
        futures = [send(i, duration=3.) for i in (0,1)]
        handles = [wait(f) for f, _ in futures]
        assert all(h.accepted for h in handles)
        assert all(d.busy for d in drivers)
        duplicate, _ = send(0)
        assert not wait(duplicate).accepted
        result_futures = [h.get_result_async() for h in handles]
        cancelled = wait(handles[0].cancel_goal_async())
        assert cancelled.goals_canceling
        receipts = [json.loads(wait(f).result.receipt_json) for f in result_futures]
        assert receipts[0]['reason'] == 'cancelled'
        for i, receipt in enumerate(receipts):
            assert receipt['goal_id'] == futures[i][1]
            assert receipt['session_id'] == world.session
            assert receipt['stop_confirmed'], receipt
            state = json.loads(wait(statuses[i].call_async(Trigger.Request())).message)
            assert receipt['goal_id'] in state['receipts'] and not state['busy']
        snapshots = {}
        for camera in ('left', 'overhead', 'right'):
            snapshot = client.create_client(Snapshot, '/'+camera+'/driver/observe')
            assert snapshot.wait_for_service(timeout_sec=10)
            value = json.loads(wait(snapshot.call_async(Snapshot.Request()),60).snapshot_json)
            assert Path(value['rgb_path']).is_file() and Path(value['depth_path']).is_file()
            assert value['calibration']['camera'] == camera
            assert infos[camera].header.frame_id == camera+'/camera_optical_frame'
            assert np.allclose(np.array(infos[camera].k).reshape(3,3), value['calibration']['K'])
            snapshots[camera] = value
        assert len({Path(v['rgb_path']).read_bytes() for v in snapshots.values()}) == 3
        assert set(transforms) >= {'left/camera_optical_frame', 'right/camera_optical_frame'}
        assert not np.allclose(before['optical_to_base_rotation'],
                               snapshots['right']['calibration']['optical_to_base_rotation'], atol=1e-4)
        assert np.allclose(snapshots['right']['calibration']['K'][0][0], 120/np.tan(np.deg2rad(29)))
        value = snapshots['left']
        report = dict(shared_session=world.session, concurrent_arms=True, same_arm_rejected=True,
                      stale_session_rejected=True, cancelled_and_stopped=True, receipts=receipts, snapshot=value, cameras=snapshots)
        Path('/observations/integration.json').write_text(json.dumps(report,indent=2))
        print('PASS: shared world, concurrent arms, exclusion, stale session, UUID receipts, cancellation, RGB-D')
    finally:
        for d in drivers:
            d.shutting_down.set()
        executor.shutdown(timeout_sec=10)
        for d in drivers:
            d.destroy_node()
        overhead.destroy_node()
        client.destroy_node()
        world.close()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
