"""ROS transport and failure semantics integration checks. Requires a running server."""
import json
import numpy as np
import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import Image
from rosbody.client import Robot, ExecutionUnknown
from rosbody_interfaces.action import Move, Gripper


def main():
    with Robot() as robot:
        robot.reset()
        status = robot.status()
        assert not status["busy"]
        snapshot = robot.snapshot()
        assert np.load(snapshot["depth_path"]).shape == (480, 640)
        got = []
        sub = robot.node.create_subscription(Image, "/camera/front/rgb", got.append, 2)
        import time
        deadline = time.monotonic() + 15
        while not got and time.monotonic() < deadline:
            robot.executor.spin_once(timeout_sec=.2)
        assert got and got[0].encoding == "rgb8"
        robot.node.destroy_subscription(sub)
        before = robot.status()["state"]
        invalid = robot.wait(robot.move_client.send_goal_async(Move.Goal(xyz=[4.,0.,.1], speed=.1)))
        assert not invalid.accepted
        assert robot.status()["state"]["sim_time"] == before["sim_time"]
        handle = robot.wait(robot.move_client.send_goal_async(Move.Goal(xyz=[.35,.2,.1], speed=.01)))
        assert handle.accepted
        duplicate = robot.wait(robot.grip_client.send_goal_async(Gripper.Goal(width=.04)))
        assert not duplicate.accepted, "second body action must be rejected while busy"
        cancellation = robot.wait(handle.cancel_goal_async())
        assert cancellation.goals_canceling
        result = robot.wait(handle.get_result_async())
        receipt = json.loads(result.result.receipt_json)
        assert receipt["reason"] == "cancelled"
        assert receipt["stop_confirmed"]
        assert not receipt["complete"]
        assert receipt["goal_id"] in robot.status()["receipts"]
        assert robot.move([0.,-.25,.28])["complete"]
        assert robot.grip(.08)["task_success"] is None
        receipt_ids = set(robot.status()["receipts"])
        robot.timeout = .02
        try:
            robot.move([.3, .15, .12], speed=.03)
        except ExecutionUnknown:
            pass
        else:
            raise AssertionError("short client deadline should report execution unknown")
        robot.timeout = 30
        deadline = time.monotonic() + 20
        while robot.status()["busy"] and time.monotonic() < deadline:
            time.sleep(.05)
        reconciled = robot.status()
        assert not reconciled["busy"]
        assert len(set(reconciled["receipts"]) - receipt_ids) == 1, "timeout must not replay action"
        assert np.linalg.norm(np.array(reconciled["state"]["tcp_xyz"])-[.3,.15,.12]) < .004
        print(json.dumps({"passed": ["snapshot RGB-D", "RGB topic", "invalid goal rejection without stepping",
                                     "exclusive actions", "cancel and stop confirmation", "receipt lookup",
                                     "new action after cancellation", "task verdict isolation", "timeout reconciliation without replay"]}, indent=2))


if __name__ == "__main__":
    main()
