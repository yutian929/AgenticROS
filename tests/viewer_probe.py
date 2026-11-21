"""Live native RViz/ROS/VNC integration in disposable separate containers."""
import json
from pathlib import Path
import socket
import time
import urllib.request
import yaml

end=time.monotonic()+60
while True:
    p=Path('/tmp/viewer-status.json')
    state=json.loads(p.read_text()) if p.exists() else {}
    topics=state.get('interfaces',{}).get('topics',[])
    images=[n for n,t in topics if 'sensor_msgs/msg/CompressedImage' in t and '/robot/robot_a/' in n]
    config=yaml.safe_load(Path('/tmp/current.rviz').read_text()) if Path('/tmp/current.rviz').exists() else {}
    displays=config.get('Visualization Manager',{}).get('Displays',[])
    cameras=[d for d in displays if d['Class']=='rviz_default_plugins/Image' and d['Enabled']]
    actions={n for n,t in state.get('interfaces',{}).get('actions',[])}
    expected={f'/robot/{robot}/{arm}/driver/{kind}' for robot in ('robot_a','robot_b') for arm in ('left','right') for kind in ('actuate','move_pose','move_delta')}
    if (len(images)==3 and state.get('rviz_pid') and len(state.get('robots',{}))==2
            and not cameras and expected<=actions):break
    assert time.monotonic()<end,state
    time.sleep(.5)
with urllib.request.urlopen('http://127.0.0.1:8080/vnc.html',timeout=5) as response:assert b'noVNC' in response.read()
with socket.create_connection(('127.0.0.1',5900),timeout=5) as stream:
    f=stream.makefile('rb');version=f.read(12);assert version.startswith(b'RFB ')
    stream.sendall(version);count=f.read(1)[0];security=f.read(count)
    assert 2 in security and 1 not in security
config=yaml.safe_load(Path('/tmp/current.rviz').read_text())
displays=config['Visualization Manager']['Displays']
cameras=[d for d in displays if d['Class']=='rviz_default_plugins/Image' and d['Enabled']]
assert not cameras and config['Visualization Manager']['Global Options']['Fixed Frame']=='map'
actions=state['interfaces']['actions']
for robot in ('robot_a','robot_b'):
    for arm in ('left','right'):
        for kind in ('move_pose','move_delta','actuate'):
            assert any(n==f'/robot/{robot}/{arm}/driver/{kind}' for n,t in actions)
# Viewer receives ROS samples directly, with no Agent worker/socket dependency.
import rclpy
from sensor_msgs.msg import CompressedImage
from rclpy.qos import qos_profile_sensor_data
rclpy.init()
node=rclpy.create_node('viewer_camera_check')
received={};started=time.monotonic()
def receive(name,msg):
    received[name]=dict(bytes=len(msg.data),frame=msg.header.frame_id,stamp=msg.header.stamp.sec)
subscriptions=[node.create_subscription(CompressedImage,name,lambda msg,name=name: receive(name,msg),qos_profile_sensor_data) for name in images]
while len(received)<3 and time.monotonic()-started<25: rclpy.spin_once(node,timeout_sec=.2)
assert len(received)==3 and all(s['bytes']>1000 for s in received.values()),received
first_frames_ms=(time.monotonic()-started)*1000
diagnostics=dict(client='native-viewer',topics=node.get_topic_names_and_types())
node.destroy_node();rclpy.shutdown()
stable_pid=json.loads(Path('/tmp/viewer-status.json').read_text())['rviz_pid']
end=time.monotonic()+6
while time.monotonic()<end:
    time.sleep(.2)
    assert json.loads(Path('/tmp/viewer-status.json').read_text())['rviz_pid']==stable_pid,'RViz restarted under stable published interfaces'
# Preserve a real desktop frame for visual inspection, not just generated YAML.
import os
os.environ['DISPLAY']=':'+os.environ['AGENTICROS_VNC_PORT']
from PyQt5.QtWidgets import QApplication
app=QApplication.instance() or QApplication([])
root=Path('/tmp/viewer-evidence');root.mkdir(exist_ok=True)
app.primaryScreen().grabWindow(0).save(str(root/'viewer-desktop.png'))
(root/'viewer-check.json').write_text(json.dumps(dict(native_camera_panels=0,actual_camera_samples=received,all_first_frames_ms=first_frames_ms,endpoints=diagnostics,rviz_pid=state['rviz_pid'],vnc_authentication=True,state=state),indent=2))
(root/'viewer.rviz').write_text(yaml.safe_dump(config))
print('plain RViz + ROS camera access PASS',flush=True)
