"""Real-model ROS smoke test. Run in the SAM3 container with a photo path.

Uses only a private validation image topic; no robot commands. The supplied photo
must contain a person. Validates forward inference and transport, not task success.
"""
import argparse
import json
import sys
import threading
import uuid
from PIL import Image
import rclpy
from rclpy.action import ActionClient
from rclpy.executors import MultiThreadedExecutor
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from sensor_msgs.msg import Image as RosImage
from std_srvs.srv import Trigger
from unique_identifier_msgs.msg import UUID
from agenticros_perception_interfaces.action import Segment
from agenticros_perception_interfaces.msg import Segmentation


def wait(future, timeout=180):
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    assert event.wait(timeout), 'Timeout: do not resend this goal'
    return future.result()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image')
parser.add_argument('--prompt', default='person')
parser.add_argument('--threshold', type=float, default=.5)
args = parser.parse_args()
rclpy.init()
node = rclpy.create_node('sam3_real_validation')
executor = MultiThreadedExecutor(num_threads=2)
executor.add_node(node)
thread = threading.Thread(target=executor.spin, daemon=True)
thread.start()
try:
    photo = Image.open(args.image).convert('RGB')
    photo.thumbnail((1280,720))
    key = uuid.uuid4()
    topic = '/validation/sam3/image_'+key.hex
    publisher = node.create_publisher(RosImage, topic, 10)
    pixels = photo.tobytes()
    def publish():
        message = RosImage(width=photo.width,height=photo.height,encoding='rgb8',step=photo.width*3,data=pixels)
        message.header.stamp = node.get_clock().now().to_msg()
        message.header.frame_id = 'validation_optical_frame'
        publisher.publish(message)
    timer = node.create_timer(.1,publish)
    received = []
    matched = threading.Event()
    def receive(message):
        if message.goal_id == key.hex:
            received.append(message)
            matched.set()
    subscription = node.create_subscription(Segmentation,'/perception/sam3/results',receive,
        QoSProfile(depth=4,reliability=ReliabilityPolicy.RELIABLE,durability=DurabilityPolicy.TRANSIENT_LOCAL))
    status = node.create_client(Trigger,'/perception/sam3/status')
    assert status.wait_for_service(timeout_sec=10)
    state = json.loads(wait(status.call_async(Trigger.Request()),10).message)
    assert state['model_state'] == 'READY', state
    client = ActionClient(node,Segment,'/perception/sam3/segment')
    assert client.wait_for_server(timeout_sec=10)
    goal = Segment.Goal(session_id=state['session_id'],image_topic=topic,frame_policy='LATEST',
        max_image_age_sec=10.,image_timeout_sec=10.,text_prompt=args.prompt,score_threshold=args.threshold,max_instances=32)
    print('Submitted validation Goal UUID: '+key.hex,flush=True)
    handle = wait(client.send_goal_async(goal,goal_uuid=UUID(uuid=list(key.bytes))),10)
    assert handle.accepted
    response = wait(handle.get_result_async())
    result = response.result
    instances = json.loads(result.instances_json)
    summary = dict(goal_id=key.hex,success=result.success,code=result.code,message=result.message,
                   instances=len(instances),record_id=result.record_id,model=state['model'],
                   prompt=args.prompt,threshold=args.threshold)
    print(json.dumps(summary,indent=2),flush=True)
    assert response.status == 4 and result.success, summary
    assert instances, 'Photo contains people but no instances returned'
    assert matched.wait(10), 'Pixel result missing'
    assert len(received[0].instances) == len(instances)
    assert received[0].source_image.header == result.source_header
    assert all(len(item.mask.data)==photo.width*photo.height for item in received[0].instances)
    print('PASS: real SAM3 image/text inference and ROS masks',flush=True)
finally:
    executor.shutdown()
    thread.join()
    node.destroy_node()
    rclpy.shutdown()
