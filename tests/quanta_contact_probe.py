"""Private fresh-scene contact regression, never mounted in the Agent.

Uses a centered reference grasp and recorded left contact commands with new IDs,
then checks continued control while holding. This is not autonomous adaptation.
"""
import json, os, time
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
import rclpy
from rclpy.node import Node
from rclpy.action import ActionClient
from std_srvs.srv import Trigger
from sensor_msgs.msg import Image
from quanta_driver_interfaces.action import Actuate

rclpy.init();node=Node('contact_regression')
ns='/robot/'+os.environ['TEST_ROBOT_ID']
out=Path('/evidence');records=[]
frames={}
subscriptions=[node.create_subscription(Image,ns+'/'+camera+'/camera/color/image_raw',
    lambda msg, name=camera: frames.update({name:msg}),2) for camera in ('head','right')]

def wait(future,timeout=60):
    rclpy.spin_until_future_complete(node,future,timeout_sec=timeout)
    assert future.done(), 'timeout: do not replay the original goal'
    return future.result()

def status(arm='right'):
    c=node.create_client(Trigger,ns+'/'+arm+'/driver/status')
    assert c.wait_for_service(timeout_sec=10)
    value=json.loads(wait(c.call_async(Trigger.Request())).message)
    node.destroy_client(c)
    return value

clients={a:ActionClient(node,Actuate,ns+'/'+a+'/driver/actuate') for a in ('left','right')}
for client in clients.values():assert client.wait_for_server(timeout_sec=10)

def move(label,q,duration,arm='right'):
    before=status(arm)
    client=clients[arm]
    assert before['control_available'],before
    handle=wait(client.send_goal_async(Actuate.Goal(session_id=before['session_id'],positions=list(map(float,q)),duration=float(duration))))
    assert handle.accepted,label
    receipt=json.loads(wait(handle.get_result_async()).result.receipt_json)
    after=status(arm)
    records.append(dict(stage=label,receipt=receipt,status=after))
    (out/'contact.json').write_text(json.dumps(records,indent=2))
    assert receipt['stop_confirmed'] and after['control_available'],records[-1]
    assert receipt['task_success'] is None
    return after

# Return from the basic protocol probe to an open home before reproduction.
move('open_home',[0.,0.,0.,0.,0.,0.,1.],2.)
# Reference grasp centered on the handle; the original end-biased grasp slipped.
move('centered_approach',[-.101054067,1.657202299,-1.765464528,1.157671413,.050462509,-.087589848,1.],2.)
move('centered_straddle',[-.101054068,1.576934666,-1.325350549,.797825407,.050462507,-.087589864,1.],1.8)
q=status()['state']['joint_positions'];q[-1]=0.
held=move('close_contact',q,1.3)
for name, image in frames.items():
    (out/('after_close_'+name+'.ppm')).write_bytes(f'P6\n{image.width} {image.height}\n255\n'.encode()+bytes(image.data))
left_approach=[-.468041593,2.543669477,-1.545919051,-.997750444,-1.102754683,0.,0.]
move('left_approach',left_approach,3.,'left')
move('left_contact',[-.525635632,2.596313634,-1.658019018,-.938294611,-1.045160680,0.,0.],1.25,'left')
move('left_withdraw_after_contact',left_approach,1.5,'left')
held=status()

# Solve a short vertical translation using public kinematics, not scene poses.
def tf(p,q):
    m=np.eye(4);m[:3,:3]=Rotation.from_quat(np.roll(q,-1)).as_matrix();m[:3,3]=p
    return m
joints=json.loads(Path('/opt/test/kinematics.json').read_text())['joints']
by_child={j['child'][0]:j for j in joints}
chain=[];link='right_arm_gripper_base_link'
while link!='base_link':
    j=by_child[link];chain.append(j);link=j['parent'][0]
chain=list(reversed(chain))
body=held['body']
lift=body['joint_positions'][body['joint_names'].index('lift_joint')]
base=tf(body['base_position'],body['base_orientation_wxyz'])
def fk(q):
    m=base.copy();k=0
    for j in chain:
        m=m@tf(j['parent_anchor'],j['parent_rotation'])
        if j['name']=='lift_joint':m[:3,3]+=m[:3,'XYZ'.index(j['axis'])]*lift
        elif j['type']=='PhysicsRevoluteJoint':
            v=np.eye(3)['XYZ'.index(j['axis'])]*q[k];k+=1
            m[:3,:3]=m[:3,:3]@Rotation.from_rotvec(v).as_matrix()
        m=m@np.linalg.inv(tf(j['child_anchor'],j['child_rotation']))
    return m@tf([.217,0.,-.01],[1.,0.,0.,0.])
seed=np.array(held['state']['joint_positions'][:6]);target=fk(seed);target[2,3]+=.10
bounds=np.array(held['state']['joint_limits'][:6]).T
def residual(q):
    m=fk(q)
    return np.r_[m[:3,3]-target[:3,3],.14*Rotation.from_matrix(target[:3,:3].T@m[:3,:3]).as_rotvec()]
solution=least_squares(residual,np.clip(seed,*bounds),bounds=bounds,max_nfev=200)
assert np.linalg.norm(residual(solution.x))<.001
move('lift_after_contact',[*solution.x,0.],2.)
# Keep the hold for independent host-side object-clearance verification.
deadline=time.monotonic()+2
while time.monotonic()<deadline:rclpy.spin_once(node,timeout_sec=.1)
for name, image in frames.items():
    (out/('after_lift_'+name+'.ppm')).write_bytes(f'P6\n{image.width} {image.height}\n255\n'.encode()+bytes(image.data))
(out/'contact_result.json').write_text(json.dumps(dict(result='PASS',scope='scripted isolated contact/control regression; not autonomous bimanual task',final=status()),indent=2))
node.destroy_node();rclpy.shutdown()
