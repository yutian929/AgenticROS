"""Blocking ROS client. Never retries a physical action on timeout."""
import json
import uuid
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import SingleThreadedExecutor
from std_srvs.srv import Trigger
from rosbody_interfaces.action import Move, Gripper
from rosbody_interfaces.srv import Snapshot


class ExecutionUnknown(RuntimeError):
    pass


class Robot:
    def __init__(self, timeout=30):
        self.context = rclpy.context.Context()
        rclpy.init(context=self.context)
        self.node = rclpy.create_node("rosbody_client_" + uuid.uuid4().hex[:8], context=self.context)
        self.executor = SingleThreadedExecutor(context=self.context)
        self.executor.add_node(self.node)
        self.timeout = timeout
        self.move_client = ActionClient(self.node, Move, "/body/move_free")
        self.grip_client = ActionClient(self.node, Gripper, "/body/gripper")
        self.snapshot_client = self.node.create_client(Snapshot, "/body/capture_snapshot")
        self.status_client = self.node.create_client(Trigger, "/body/status")
        self.reset_client = self.node.create_client(Trigger, "/sim/reset")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.executor.shutdown()
        self.node.destroy_node()
        self.context.shutdown()

    def wait(self, future, timeout=None):
        self.executor.spin_until_future_complete(future, timeout_sec=self.timeout if timeout is None else timeout)
        if not future.done():
            raise TimeoutError("ROS response timed out")
        result = future.result()
        if result is None:
            raise RuntimeError("ROS returned no result")
        return result

    def service(self, client, request):
        if not client.wait_for_service(timeout_sec=5):
            raise ConnectionError("ROSBody service unavailable; check server and ROS_DOMAIN_ID")
        return self.wait(client.call_async(request))

    def snapshot(self):
        return json.loads(self.service(self.snapshot_client, Snapshot.Request()).snapshot_json)

    def status(self):
        return json.loads(self.service(self.status_client, Trigger.Request()).message)

    def reset(self):
        result = self.service(self.reset_client, Trigger.Request())
        if not result.success:
            raise RuntimeError(result.message)
        return result.message

    def action(self, client, goal):
        if not client.wait_for_server(timeout_sec=5):
            raise ConnectionError("ROSBody action server unavailable")
        # Allocate before sending so even a missing acceptance reply has a traceable ID.
        from unique_identifier_msgs.msg import UUID
        goal_uuid = UUID(uuid=list(uuid.uuid4().bytes))
        gid = bytes(goal_uuid.uuid).hex()
        try:
            handle = self.wait(client.send_goal_async(goal, goal_uuid=goal_uuid))
            if not handle.accepted:
                raise ValueError(f"Goal {gid} rejected: invalid arguments, body busy, or recovery required")
            response = self.wait(handle.get_result_async())
        except TimeoutError as exc:
            raise ExecutionUnknown(f"Goal {gid}: execution unknown; query status/receipt, do NOT repeat action") from exc
        receipt = json.loads(response.result.receipt_json)
        if not receipt["complete"]:
            raise RuntimeError(json.dumps(receipt))
        return receipt

    def move(self, xyz, speed=0.12):
        return self.action(self.move_client, Move.Goal(xyz=[float(x) for x in xyz], speed=float(speed)))

    def grip(self, width):
        return self.action(self.grip_client, Gripper.Goal(width=float(width)))
