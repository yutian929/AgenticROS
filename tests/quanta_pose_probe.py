"""Native CLI conformance/motion checks against a fresh disposable Quanta body.

Only public ROS observations are used. This is not grasp/task evaluation.
"""
import ast
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

import yaml

ROOT = Path('/workspace/pose-evidence')
ROOT.mkdir(exist_ok=True)
NS = '/robot/'+os.environ['TEST_ROBOT_ID']
INDEX = 0
RECORDS = []


def cli(arguments, label, timeout=70):
    global INDEX
    INDEX += 1
    response = subprocess.run(['ros2', *arguments], text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
    (ROOT/f'{INDEX:02d}-{label}.log').write_text(response.stdout)
    assert response.returncode == 0, response.stdout
    return response.stdout


def status(arm):
    output = cli(['service','call',NS+'/'+arm+'/driver/status','std_srvs/srv/Trigger','{}'], 'status-'+arm, 20)
    expression = ast.parse(output.split('response:\n',1)[1].strip(), mode='eval').body
    fields = {field.arg:ast.literal_eval(field.value) for field in expression.keywords}
    assert fields['success']
    return json.loads(fields['message'])


def echo(topic, interface, label, expression=None):
    command = ['topic','echo',topic,interface,'--once','--qos-reliability','reliable']
    if expression is not None:
        command += ['--filter',expression]
    output = cli(command,label,20)
    # Fast DDS can print its known cross-distro diagnostic before the YAML.
    # Keep it in the raw log, but parse the actual message independently.
    start = re.search(r'^(header|transforms):',output,re.MULTILINE)
    assert start is not None, output
    return yaml.safe_load(output[start.start():].split('---')[0])


def send(arm, kind, request, label, seconds=60):
    (ROOT/(label+'-request.json')).write_text(json.dumps(request))
    return cli(['action','send_goal',NS+'/'+arm+'/driver/'+kind,
        'quanta_driver_interfaces/action/'+('MovePose' if kind == 'move_pose' else 'Actuate'),
        json.dumps(request),'--feedback','--timeout',str(seconds)],label,seconds+15)


def receipt(output):
    result = yaml.safe_load(output.split('Result:\n',1)[1].split('Goal finished with status:',1)[0].lstrip())
    value = json.loads(result['receipt_json'])
    assert value['goal_id'] == re.search(r'Goal accepted with ID: ([a-f0-9]{32})',output)[1]
    assert value['task_success'] is None
    return value


def complete(output, arm):
    value = receipt(output)
    assert value['complete'] and value['stop_confirmed'], value
    current = status(arm)
    assert current['control_available'] and current['stop_confirmed'], current
    assert value['session_id'] == current['session_id']
    assert value['goal_id'] in current['receipts']
    RECORDS.append(value)
    (ROOT/'receipts.json').write_text(json.dumps(RECORDS,indent=2))
    if value['operation'] == 'move_pose':
        assert value['position_error'] < .003 and value['orientation_error'] < .03, value
        assert value['pose_frame'] == NS.strip('/')+'/'+arm+'/arm_base'
        assert abs(current['state']['joint_positions'][6]-value['gripper_target']) < .03
    return value


def quaternion(q):
    values = [q[key] for key in ('x','y','z','w')]
    norm = math.sqrt(sum(x*x for x in values))
    return [x/norm for x in values]


def product(a,b):
    x,y,z,w = a;X,Y,Z,W = b
    return [w*X+x*W+y*Z-z*Y, w*Y-x*Z+y*W+z*X,
            w*Z+x*Y-y*X+z*W, w*W-x*X-y*Y-z*Z]


def rotate(q,vector):
    return product(product(q,[*vector,0.]),[-q[0],-q[1],-q[2],q[3]])[:3]


def distance(a,b):
    return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))


def angle(a,b):
    return 2*math.acos(min(1.,abs(sum(x*y for x,y in zip(a,b)))))


def tool_expected(value, translation, rotation):
    start, target = value['start_pose'], value['target_pose']
    position = [start['position'][key] for key in 'xyz']
    expected = [a+b for a,b in zip(position,rotate(quaternion(start['orientation']),translation))]
    assert distance(expected,[target['position'][key] for key in 'xyz']) < 1e-8, value
    assert angle(product(quaternion(start['orientation']),rotation),quaternion(target['orientation'])) < 1e-6, value


session = status('left')['session_id']
unit = dict(x=0.,y=0.,z=0.,w=1.)
for arm in ('left','right'):
    before = status(arm)
    assert before['control_available'] and before['stop_confirmed'], before
    joints = [.05,.3,-.45,.15,.15,.05,before['state']['joint_positions'][6]]
    complete(send(arm,'actuate',dict(session_id=session,positions=joints,duration=2.),'seed-'+arm),arm)

for arm in ('left','right'):
    observation = echo(NS+'/'+arm+'/driver/ee_pose','geometry_msgs/msg/PoseStamped','ee-pose-'+arm)
    assert observation['header']['frame_id'] == NS.strip('/')+'/'+arm+'/arm_base'
    if os.environ.get('TEST_REQUIRE_IMAGES') == '1':
        # Compare FK-based public telemetry with independent USD-backed TF.
        transforms = {}
        for child in ('arm_base','tcp'):
            frame = NS.strip('/')+'/'+arm+'/'+child
            message = echo('/tf','tf2_msgs/msg/TFMessage','tf-'+arm+'-'+child,
                           'any(t.child_frame_id == '+repr(frame)+' for t in m.transforms)')
            transforms[child] = next(t['transform'] for t in message['transforms'] if t['child_frame_id'] == frame)
        base, tcp = transforms['arm_base'], transforms['tcp']
        inverse = quaternion(base['rotation']);inverse[:3] = [-x for x in inverse[:3]]
        position = rotate(inverse,[tcp['translation'][k]-base['translation'][k] for k in 'xyz'])
        orientation = product(inverse,quaternion(tcp['rotation']))
        assert distance(position,[observation['pose']['position'][k] for k in 'xyz']) < .003, (position,observation)
        assert angle(orientation,quaternion(observation['pose']['orientation'])) < .03, (orientation,observation)
        (ROOT/('tf-'+arm+'.json')).write_text(json.dumps(transforms,indent=2))
    target = observation['pose']
    target['position']['z'] += .004
    request = dict(session_id=session,reference_frame='arm_base',target=target,duration=1.5)
    value = complete(send(arm,'move_pose',request,'arm-base-'+arm),arm)
    assert distance([value['target_pose']['position'][k] for k in 'xyz'],[target['position'][k] for k in 'xyz']) < 1e-8
    translation = [.004,0.,0.]
    request.update(reference_frame=NS.strip('/')+'/'+arm+'/tcp',
                   target=dict(position=dict(zip('xyz',translation)),orientation=unit))
    value = complete(send(arm,'move_pose',request,'tool-translation-'+arm),arm)
    tool_expected(value,translation,[0.,0.,0.,1.])
    rotation = [0.,0.,math.sin(.01),math.cos(.01)]
    request.update(reference_frame='tcp',target=dict(position=dict.fromkeys('xyz',0.),
                   orientation=dict(zip(('x','y','z','w'),rotation))))
    value = complete(send(arm,'move_pose',request,'tool-rotation-'+arm),arm)
    tool_expected(value,[0.,0.,0.],rotation)

request = dict(session_id=session,reference_frame='tcp',duration=30.,
               target=dict(position=dict(x=.005,y=0.,z=0.),orientation=unit))
for change in [dict(session_id='old'),dict(reference_frame=NS.strip('/')+'/right/tcp'),
               dict(target=dict(position=dict(x=10.,y=0.,z=0.),orientation=unit)),
               dict(target=dict(position=dict.fromkeys('xyz',0.),orientation=dict.fromkeys(('x','y','z','w'),0.)))]:
    assert 'Goal was rejected.' in send('left','move_pose',dict(request,**change),'invalid')

# Native CLI times out without cancellation. Reconcile its UUID, never replay it.
output = send('left','move_pose',request,'timed-out-original',seconds=5)
assert 'Timed out waiting for result' in output, output
gid = re.search(r'Goal accepted with ID: ([a-f0-9]{32})',output)[1]
current = status('left')
assert current['active_goal_id'] == gid and current['busy'], current
assert 'Goal was rejected.' in send('left','actuate',dict(session_id=session,
    positions=current['state']['joint_positions'],duration=.5),'raw-while-pose')
cancel = dict(goal_info=dict(goal_id=dict(uuid=list(uuid.UUID(hex=gid).bytes))))
response = cli(['service','call',NS+'/left/driver/move_pose/_action/cancel_goal',
                'action_msgs/srv/CancelGoal',json.dumps(cancel)],'cancel-original',20)
assert 'return_code=0' in response, response
deadline = time.monotonic()+30
while True:
    final = status('left')
    if gid in final['receipts']:
        cancelled = final['receipts'][gid]
        assert cancelled['reason'] == 'cancelled' and cancelled['stop_confirmed'], cancelled
        assert final['control_available'] and final['stop_confirmed'], final
        break
    assert time.monotonic() < deadline, final
(ROOT/'result.json').write_text(json.dumps(dict(result='PASS',scope=os.environ.get('TEST_SCOPE'),
    physical_hosts=1,ros_cli_only=True,task_evaluation=False,receipts=RECORDS,cancellation=cancelled,
    cases=['arm_base_absolute_both_arms','tool_translation_both_arms','tool_rotation_both_arms',
           'session_and_frame_rejection','raw_pose_exclusivity','timeout_reconciliation','native_cancel',
           'measured_stop','gripper_preserved'],
    dds_sequence_diagnostics=sum(path.read_text().count('sequence size exceeds remaining buffer')
                                 for path in ROOT.glob('*.log'))),indent=2))
print('PASS: Quanta arm-base and tool-frame pose control via native ROS CLI',flush=True)
