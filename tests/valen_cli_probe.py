"""Exercise the documented interface using only ros2 CLI for all ROS traffic."""
import ast
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time
import uuid

import yaml


ROOT = Path('/workspace/evidence')
ROOT.mkdir(exist_ok=True)
INDEX = 0
WIDTH, HEIGHT = 12, 8


def cli(arguments, label, timeout=90):
    global INDEX
    INDEX += 1
    response = subprocess.run(['ros2', *arguments], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
    (ROOT/f'cli-{INDEX:02d}-{label}.log').write_text(response.stdout)
    assert response.returncode == 0, response.stdout
    return response.stdout


def service(endpoint, interface, request, label):
    output = cli(['service', 'call', endpoint, interface, json.dumps(request)], label, 20)
    expression = ast.parse(output.split('response:\n', 1)[1].strip(), mode='eval').body
    return {field.arg: ast.literal_eval(field.value) for field in expression.keywords}


def status():
    return json.loads(service('/decision/valen/status', 'std_srvs/srv/Trigger', {}, 'status')['message'])


def query(session, goal):
    return service('/decision/valen/result', 'agenticros_decision_interfaces/srv/DecisionResult',
                   dict(session_id=session, goal_id=goal), 'get-result')


def result(output):
    assert 'Goal finished with status:' in output, output
    return yaml.safe_load(output.split('Result:\n', 1)[1].split('Goal finished with status:', 1)[0].lstrip())


def send(request, label):
    (ROOT/f'cli-{label}-request.json').write_text(json.dumps(request))
    return cli(['action', 'send_goal', '/decision/valen/decide',
                'agenticros_decision_interfaces/action/Decide', json.dumps(request),
                '--feedback', '--timeout', '60'], label)


def check(output, request):
    value = result(output)
    assert value['success'] and value['code'] == 'OK', value
    assert value['session_id'] == request['session_id']
    uuid.UUID(hex=value['goal_id'])
    assert re.search(r'Goal accepted with ID: ([a-f0-9]{32})', output)[1] == value['goal_id']
    answers = json.loads(value['answers_json'])
    questions = json.loads(request['questions_json'])
    assert set(answers) == set(questions)
    for key, question in questions.items():
        answer = answers[key]
        assert answer['type'] == question['type']
        if question['type'] == 'bbox':
            x1, y1, x2, y2 = answer['bbox']
            assert 0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1, answer
            assert all(0 <= coordinate <= 1 for coordinate in answer['point']), answer
            assert 0 <= answer['confidence'] <= 1
    if request['image_topic']:
        assert value['source_header']['frame_id'] == 'cli_optical_frame', value
        assert (value['width'], value['height']) == (WIDTH, HEIGHT), value
    else:
        assert (value['width'], value['height']) == (0, 0), value
    (ROOT/f"cli-{value['goal_id']}-result.json").write_text(json.dumps(value))
    return value


publisher = None
try:
    deadline = time.monotonic()+600
    while True:
        state = status()
        assert state['model_state'] != 'FAILED', state
        if state['model_state'] == 'READY':
            break
        assert time.monotonic() < deadline, state
        time.sleep(1)
    synthetic = bool(state['model'].get('not_valen_inference'))
    if not synthetic:
        WIDTH, HEIGHT = 96, 72
    data = []
    for y in range(HEIGHT):
        for x in range(WIDTH):
            cup = not synthetic and (30 <= x < 56 and 24 <= y < 57 or
                55 <= x < 70 and 28 <= y < 50 and (x < 59 or x >= 66 or y < 32 or y >= 46))
            data.extend([230, 30, 20] if cup else [30, 100, 40])
    image = dict(header=dict(stamp='now', frame_id='cli_optical_frame'), width=WIDTH, height=HEIGHT,
                 encoding='rgb8', step=WIDTH*3, data=data)
    (ROOT/'cli-input-image.json').write_text(json.dumps(image))
    with (ROOT/'cli-publisher.log').open('w') as log:
        publisher = subprocess.Popen(['ros2', 'topic', 'pub', '--rate', '10', '--print', '100', '/test/cli_rgb',
                                      'sensor_msgs/msg/Image', json.dumps(image, separators=(',', ':'))],
                                      stdout=log, stderr=subprocess.STDOUT)
    text = Path('/workspace/.agents/skills/perception/SKILL.md').read_text()
    requests = []
    for script in re.findall(r'```bash\n(.*?)\n```', text, re.S):
        tokens = shlex.split(script.replace('\\\n', ''))
        if tokens[:4] != ['ros2', 'action', 'send_goal', '/decision/valen/decide']:
            continue
        assert tokens[4] == 'agenticros_decision_interfaces/action/Decide'
        payload = tokens[5].replace('<VALEN_SESSION>', state['session_id']).replace('<RGB_TOPIC>', '/test/cli_rgb')
        request = yaml.safe_load(payload)
        (ROOT/f'cli-example-{len(requests)}-request.json').write_text(json.dumps(request))
        output = cli(tokens[1:5]+[payload]+tokens[6:], f'example-{len(requests)}')
        value = check(output, request)
        requests.append(request)
        if len(requests) == 1:
            successful = value['goal_id']
    assert len(requests) == 4
    assert {q['type'] for request in requests for q in json.loads(request['questions_json']).values()} == {
        'choice', 'score', 'noul', 'bbox'}
    combined = dict(requests[0])
    questions = {key: question for request in requests for key, question in json.loads(request['questions_json']).items()}
    combined['questions_json'] = json.dumps(questions)
    value = check(send(combined, 'mixed'), combined)
    # The public query service reconciles a recorded UUID through native CLI.
    recovered = query(state['session_id'], value['goal_id'])
    assert recovered['success'] and recovered['state'] == 'SUCCEEDED', recovered
    assert json.loads(recovered['result_json']) == value
    for session, goal, expected in [('old', value['goal_id'], 'SESSION_MISMATCH'),
                                    (state['session_id'], '../result.json', 'INVALID_REQUEST'),
                                    (state['session_id'], uuid.uuid4().hex, 'UNKNOWN')]:
        unknown = query(session, goal)
        assert not unknown['success'] and unknown['state'] == expected and unknown['result_json'] == '', unknown
    plain = dict(combined, image_topic='', state_json='{"text":"The cup is red and fully visible."}',
                 questions_json=json.dumps({key:q for key,q in questions.items() if q['type'] != 'bbox'}))
    check(send(plain, 'text'), plain)
    bbox = next(request for request in requests if any(q['type'] == 'bbox' for q in json.loads(request['questions_json']).values()))
    assert 'Goal was rejected.' in send(dict(bbox, image_topic=''), 'bbox-no-image')
    if synthetic:
        failed = result(send(dict(bbox, state_json='{"mode":"invalid_bbox"}'), 'invalid-bbox-output'))
        assert failed['code'] == 'INFERENCE_FAILED' and failed['answers_json'] == '{}', failed
        slow = dict(bbox, state_json='{"mode":"slow"}')
        log_path = ROOT/'cli-cancel.log'
        with log_path.open('w') as log:
            process = subprocess.Popen(['ros2', 'action', 'send_goal', '/decision/valen/decide',
                'agenticros_decision_interfaces/action/Decide', json.dumps(slow), '--feedback', '--timeout', '30'],
                stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, PYTHONUNBUFFERED='1'))
            try:
                deadline = time.monotonic()+20
                while 'Goal accepted with ID:' not in log_path.read_text():
                    assert process.poll() is None and time.monotonic() < deadline, log_path.read_text()
                    time.sleep(.05)
                active_goal = re.search(r'Goal accepted with ID: ([a-f0-9]{32})', log_path.read_text())[1]
                active = query(state['session_id'], active_goal)
                assert active['success'] and active['state'] in ('ACCEPTED', 'WAITING_FOR_IMAGE', 'QUEUED', 'RUNNING'), active
                process.send_signal(signal.SIGINT)
                assert process.wait(timeout=20) == 0, log_path.read_text()
                cancelled = result(log_path.read_text())
                assert cancelled['code'] == 'CANCELLED', cancelled
                terminal = query(state['session_id'], active_goal)
                assert terminal['state'] == 'CANCELLED' and json.loads(terminal['result_json']) == cancelled, terminal
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
    cases = ['choice', 'score', 'noul', 'bbox', 'mixed', 'text', 'get_result', 'query_identity', 'bbox_requires_image']
    if synthetic:
        cases += ['invalid_bbox_output', 'cli_cancel']
    (ROOT/'cli-summary.json').write_text(json.dumps(dict(transport='PASS', ros_cli_only=True,
        model_inference=not synthetic, session=state['session_id'], successful_goal=successful,
        model=state['model'], cases=cases), indent=2))
    print('PASS: Valen four-function native ROS CLI, real_model='+str(not synthetic), flush=True)
finally:
    if publisher is not None:
        publisher.terminate()
        publisher.wait(timeout=10)
