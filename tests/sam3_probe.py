"""Cross-container generic Agent transport probe; synthetic masks, no model claim."""
import json
from pathlib import Path
import time
import uuid
from sensor_msgs.msg import Image
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from agenticros_perception_interfaces.msg import Segmentation
from ros_client import Clients

client = Clients()
received = []
subscription = client.node.create_subscription(Segmentation, '/perception/sam3/results', received.append,
    QoSProfile(depth=4, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL),
    callback_group=client.group)
publisher = client.node.create_publisher(Image, '/test/rgb', 10)
stale = False
width, height = 12, 8

def publish():
    frame = Image(height=height,width=width,encoding='rgb8',step=width*3,data=bytes([30,100,40])*(width*height))
    frame.header.stamp = client.node.get_clock().now().to_msg()
    if stale:
        frame.header.stamp.sec -= 120
    frame.header.frame_id = 'test_optical_frame'
    publisher.publish(frame)

timer = client.node.create_timer(.05, publish, callback_group=client.group)

def status():
    return json.loads(client.call(dict(endpoint='/perception/sam3/status',type='std_srvs/srv/Trigger',request={}))['response']['message'])


def submit(**changes):
    key = uuid.uuid4().hex
    request = dict(session_id=status()['session_id'], image_topic='/test/rgb', frame_policy='LATEST',
        max_image_age_sec=10.,image_timeout_sec=3.,text_prompt='cup',score_threshold=.5,max_instances=4)
    request.update(changes)
    accepted = client.goal(dict(endpoint='/perception/sam3/segment',type='agenticros_perception_interfaces/action/Segment',
        goal_id=key,status_endpoint='/perception/sam3/status',request=request,timeout=10))
    return key, accepted


def result(key):
    response = client.result(dict(goal_id=key,timeout=20))
    assert 'error' not in response, response
    return response['result']['result']

try:
    state = status()
    assert state['model']['not_sam3_inference']
    assert any(name == '/perception/sam3/segment' for name, _ in client.graph()['actions'])
    key, accepted = submit()
    assert accepted['accepted'], accepted
    out = result(key)
    assert out['success'] and out['code'] == 'OK', out
    deadline = time.monotonic()+5
    while not any(item.goal_id == key for item in received) and time.monotonic()<deadline:
        time.sleep(.05)
    message = next(item for item in received if item.goal_id == key)
    assert message.header.frame_id == 'test_optical_frame'
    assert len(message.instances) == 1 and len(message.instances[0].mask.data) == 96
    assert bytes(message.source_image.data) == bytes([30,100,40])*96
    assert message.source_image.header == message.header
    assert message.header.stamp.sec == out['source_header']['stamp']['sec']
    successful_key = key
    # Camera-sized input and mask must survive UDP fragmentation/reassembly.
    width, height = 1280, 720
    key, accepted = submit()
    assert accepted['accepted'], accepted
    assert result(key)['success']
    deadline = time.monotonic()+15
    while not any(item.goal_id == key for item in received) and time.monotonic()<deadline:
        time.sleep(.05)
    large = next(item for item in received if item.goal_id == key)
    assert len(large.instances[0].mask.data) == width*height
    assert bytes(large.source_image.data) == bytes([30,100,40])*(width*height)
    width, height = 12, 8
    # A result timeout does not cancel or replay; query the same UUID later.
    key, accepted = submit()
    timed_out = client.result(dict(goal_id=key,timeout=.01))
    assert timed_out['outcome'] == 'unknown' and not timed_out['replay_allowed'], timed_out
    assert result(key)['success']
    key, accepted = submit()
    deadline = time.monotonic()+10
    while status()['active_goals'].get(key) != 'RUNNING':
        assert time.monotonic() < deadline
        time.sleep(.02)
    client.cancel(dict(goal_id=key,timeout=5))
    assert result(key)['code'] == 'CANCELLED'
    key, accepted = submit(text_prompt='empty')
    out = result(key)
    assert out['success'] and json.loads(out['instances_json']) == []
    key, accepted = submit(text_prompt='fail')
    assert result(key)['code'] == 'INFERENCE_FAILED'
    key, accepted = submit(session_id='old-session')
    assert not accepted['accepted']
    key, accepted = submit(frame_policy='EXACT',image_stamp={'sec':1,'nanosec':0},image_timeout_sec=.3)
    assert result(key)['code'] == 'FRAME_TIMEOUT'
    stale = True
    key, accepted = submit()
    assert result(key)['code'] == 'STALE_FRAME'
    Path('/workspace/evidence/summary.json').write_text(json.dumps(dict(
        transport='PASS', model_inference=False, session=state['session_id'], successful_goal=successful_key,
        cases=['discovery','action','pixels','camera_sized_udp_payload','timeout_reconciliation','cancel','empty','inference_failure',
               'wrong_session','exact_frame_missing','stale_frame']),indent=2))
    print('PASS: synthetic SAM3 transport; real model inference not exercised',flush=True)
finally:
    client.close()
