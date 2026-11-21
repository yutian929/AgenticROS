"""Agent-side sensor recording over native ROS only; no robot file access or motion."""
from collections import OrderedDict
import json
from pathlib import Path
import sys
import time
import numpy as np
from PIL import Image as PILImage
import rclpy
from rclpy.qos import qos_profile_sensor_data, QoSProfile, DurabilityPolicy
from sensor_msgs.msg import Image, CameraInfo
from tf2_msgs.msg import TFMessage
from std_srvs.srv import Trigger

robots=json.loads(sys.argv[1])
root=Path('/workspace/evidence/ros-observations');root.mkdir(parents=True,exist_ok=True)
rclpy.init();node=rclpy.create_node('agent_sensor_recording_check')
static, dynamic = {}, OrderedDict()

def stamp(msg):
    return (msg.header.stamp.sec,msg.header.stamp.nanosec)

def transforms(msg, fixed=False):
    for t in msg.transforms:
        if fixed:static[t.child_frame_id]=t
        else:dynamic[(t.child_frame_id,stamp(t))]=t
    while len(dynamic)>200:dynamic.popitem(last=False)

node.create_subscription(TFMessage,'/tf',transforms,100)
node.create_subscription(TFMessage,'/tf_static',lambda m:transforms(m,True),QoSProfile(depth=100,durability=DurabilityPolicy.TRANSIENT_LOCAL))
report=[]
try:
    for robot in robots:
        rid=robot['AGENTICROS_ROBOT_ID'];ns='/robot/'+rid
        cameras=[''] if robot['AGENTICROS_ENVIRONMENT']=='cartesian_gripper' else ['left','right','overhead']
        status=node.create_client(Trigger,ns+('/left' if len(cameras)>1 else '')+'/driver/status')
        assert status.wait_for_service(timeout_sec=20)
        f=status.call_async(Trigger.Request());rclpy.spin_until_future_complete(node,f,timeout_sec=20)
        assert f.done() and f.result().success
        session=json.loads(f.result().message)['session_id']
        for camera in cameras:
            frames=OrderedDict();subscriptions=[]
            prefix=ns+('/'+camera if camera else '')+'/camera'
            def receive(msg,key):
                frames.setdefault(stamp(msg),{})[key]=msg
                while len(frames)>12:frames.popitem(last=False)
            for key,typ,endpoint in [('rgb',Image,'/color/image_raw'),('depth',Image,'/depth/image_raw'),('info',CameraInfo,'/camera_info')]:
                subscriptions.append(node.create_subscription(typ,prefix+endpoint,lambda m,k=key:receive(m,k),qos_profile_sensor_data))
            deadline=time.monotonic()+45
            while True:
                rclpy.spin_once(node,timeout_sec=.1)
                match=None
                for timestamp,items in frames.items():
                    if set(items)!={'rgb','depth','info'}:continue
                    frame=items['info'].header.frame_id
                    tf=dynamic.get((frame,timestamp)) or static.get(frame)
                    if tf is not None:match=(timestamp,items,tf);break
                if match:break
                assert time.monotonic()<deadline,(rid,camera,'no synchronized RGB-D/CameraInfo/TF')
            timestamp,items,tf=match
            rgb,depth,info=(items[k] for k in ('rgb','depth','info'))
            assert rgb.encoding=='rgb8' and depth.encoding=='32FC1'
            assert rgb.header.frame_id==depth.header.frame_id==info.header.frame_id==tf.child_frame_id
            assert tf.header.frame_id==ns.strip('/')+'/base', tf.header.frame_id
            assert (rgb.width,rgb.height)==(depth.width,depth.height)==(info.width,info.height)
            assert rgb.step==rgb.width*3 and depth.step==depth.width*4
            pixels=np.frombuffer(bytes(rgb.data),dtype=np.uint8).reshape(rgb.height,rgb.width,3)
            distances=np.frombuffer(bytes(depth.data),dtype='>f4' if depth.is_bigendian else '<f4').reshape(depth.height,depth.width)
            assert np.isfinite(distances).all() and distances.min()>0 and info.k[0]>0
            folder=root/rid/(camera or 'overhead');folder.mkdir(parents=True,exist_ok=True)
            PILImage.fromarray(pixels).save(folder/'rgb.png');np.save(folder/'depth.npy',distances)
            value=dict(robot_id=rid,session_id=session,camera=camera or 'overhead',stamp=timestamp,
                       frame=info.header.frame_id,K=list(info.k),bytes_rgb=len(rgb.data),bytes_depth=len(depth.data))
            (folder/'capture.json').write_text(json.dumps(value,indent=2));report.append(value)
            for sub in subscriptions:node.destroy_subscription(sub)
        node.destroy_client(status)
    services=node.get_service_names_and_types()
    assert not any(name.endswith('/driver/observe') or any('Snapshot' in t for t in types) for name,types in services)
    (root/'result.json').write_text(json.dumps(dict(result='PASS',cameras=report,snapshot_services_absent=True),indent=2))
    print('PASS: Agent saved '+str(len(report))+' synchronized ROS camera captures',flush=True)
finally:
    node.destroy_node();rclpy.shutdown()
