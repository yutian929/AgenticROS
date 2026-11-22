"""Generic Agent clients across containers; no robot and no real model."""
import json
from pathlib import Path
import time
import uuid
from sensor_msgs.msg import Image
from ros_client import Clients

client = Clients()
publisher = client.node.create_publisher(Image, '/test/rgb', 10)
stale = False
width, height = 12, 8


def publish():
    frame = Image(height=height, width=width, encoding='rgb8', step=width*3,
                  data=bytes([30, 100, 40])*(width*height))
    frame.header.stamp = client.node.get_clock().now().to_msg()
    if stale:
        frame.header.stamp.sec -= 120
    frame.header.frame_id = 'test_optical_frame'
    publisher.publish(frame)


timer = client.node.create_timer(.05, publish, callback_group=client.group)


def status():
    return json.loads(client.call(dict(endpoint='/decision/valen/status', type='std_srvs/srv/Trigger', request={}))['response']['message'])


def submit(mode='', **changes):
    key = uuid.uuid4().hex
    request = dict(session_id=status()['session_id'], state_json=json.dumps({'mode': mode}),
                   questions_json=json.dumps({'motion': dict(type='choice', instructions='Select a candidate.',
                       criteria={'hold': 'Hold position', 'handoff': 'Return control'})}),
                   image_topic='/test/rgb', frame_policy='LATEST', max_image_age_sec=10., image_timeout_sec=3.)
    request.update(changes)
    accepted = client.goal(dict(endpoint='/decision/valen/decide', type='agenticros_decision_interfaces/action/Decide',
        goal_id=key, status_endpoint='/decision/valen/status', request=request, timeout=10))
    return key, accepted


def result(key):
    response = client.result(dict(goal_id=key, timeout=20))
    assert 'error' not in response, response
    return response['result']['result']


try:
    state = status()
    assert state['model']['not_valen_inference']
    assert any(name == '/decision/valen/decide' for name, _ in client.graph()['actions'])
    key, accepted = submit()
    assert accepted['accepted'], accepted
    output = result(key)
    assert output['success'] and output['source_header']['frame_id'] == 'test_optical_frame', output
    assert json.loads(output['answers_json'])['motion']['choice'] == 'hold'
    successful = key
    width, height = 1280, 720
    key, accepted = submit()
    output = result(key)
    assert output['success'] and output['width'] == width and output['height'] == height, output
    width, height = 12, 8
    key, accepted = submit(image_topic='')
    assert result(key)['success']
    key, accepted = submit()
    timeout = client.result(dict(goal_id=key, timeout=.01))
    assert timeout['outcome'] == 'unknown' and not timeout['replay_allowed'], timeout
    assert result(key)['success']
    key, accepted = submit()
    deadline = time.monotonic()+10
    while status()['active_goals'].get(key) != 'RUNNING':
        assert time.monotonic() < deadline
        time.sleep(.02)
    client.cancel(dict(goal_id=key, timeout=5))
    assert result(key)['code'] == 'CANCELLED'
    for mode in ('fail', 'invalid'):
        key, accepted = submit(mode)
        output = result(key)
        assert output['code'] == 'INFERENCE_FAILED' and output['answers_json'] == '{}', output
    key, accepted = submit(session_id='old')
    assert not accepted['accepted']
    key, accepted = submit(state_json='{"invalid":NaN}')
    assert not accepted['accepted']
    key, accepted = submit(frame_policy='EXACT', image_stamp={'sec':1, 'nanosec':0}, image_timeout_sec=.2)
    assert result(key)['code'] == 'FRAME_TIMEOUT'
    key, accepted = submit(max_image_age_sec=.15)
    output = result(key)
    assert output['code'] == 'STALE_FRAME' and output['answers_json'] == '{}', output
    stale = True
    key, accepted = submit()
    assert result(key)['code'] == 'STALE_FRAME'
    Path('/workspace/evidence/summary.json').write_text(json.dumps(dict(transport='PASS', model_inference=False,
        session=state['session_id'], successful_goal=successful, cases=['discovery', 'action', 'pixels', '720p',
        'text_only', 'timeout_reconciliation', 'cancel', 'inference_failure', 'invalid_output', 'wrong_session',
        'invalid_json', 'exact_frame_missing', 'stale_after_inference', 'stale_at_selection']), indent=2))
    print('PASS: synthetic Valen transport; no model inference or robot motion', flush=True)
finally:
    client.close()
