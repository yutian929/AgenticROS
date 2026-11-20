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
    images=[n for n,t in topics if 'sensor_msgs/msg/CompressedImage' in t and '/robots/robot_a/' in n]
    config=yaml.safe_load(Path('/tmp/current.rviz').read_text()) if Path('/tmp/current.rviz').exists() else {}
    displays=config.get('Visualization Manager',{}).get('Displays',[])
    cameras=[d for d in displays if d['Class']=='rviz_default_plugins/Image' and d['Enabled']]
    actions={n for n,t in state.get('interfaces',{}).get('actions',[])}
    expected={f'/robots/{robot}/{arm}/driver/{kind}' for robot in ('robot_a','robot_b') for arm in ('left','right') for kind in ('actuate','move_pose','move_delta')}
    if (len(images)==3 and state.get('rviz_pid') and len(state.get('robots',{}))==2
            and len(cameras)==3 and expected<=actions):break
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
assert len(cameras)==3 and config['Window Geometry']['QMainWindow State']
actions=state['interfaces']['actions']
for robot in ('robot_a','robot_b'):
    for arm in ('left','right'):
        for kind in ('move_pose','move_delta','actuate'):
            assert any(n==f'/robots/{robot}/{arm}/driver/{kind}' for n,t in actions)
# Receive actual camera samples through the product's persistent ROS worker.
# Independent cold participants are measured separately by network_probe.py.
import sys
sys.path.insert(0,'/opt/instance')
from ros_client import request
received={};started=time.monotonic();end=started+25
while len(received)<3 and time.monotonic()<end:
    for name in images:
        sample=request(dict(op='read',endpoint=name,type='sensor_msgs/msg/CompressedImage'))
        assert 'error' not in sample,sample
        if sample['received'] and sample['age_seconds']<2:
            msg=sample['message']
            received[name]=dict(bytes=len(msg['data']),frame=msg['header']['frame_id'],stamp=msg['header']['stamp']['sec'])
    time.sleep(.1)
diagnostics=dict(client='persistent-worker',topics=request(dict(op='graph'))['topics'])
assert len(received)==3 and all(s['bytes']>1000 for s in received.values()),dict(received=received,endpoints=diagnostics)
first_frames_ms=(time.monotonic()-started)*1000
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
app.primaryScreen().grabWindow(0).save('/workspace/evidence/viewer-desktop.png')
root=Path('/workspace/evidence');root.mkdir(exist_ok=True)
(root/'viewer-check.json').write_text(json.dumps(dict(native_camera_panels=3,actual_camera_samples=received,all_first_frames_ms=first_frames_ms,endpoints=diagnostics,rviz_pid=state['rviz_pid'],vnc_authentication=True,state=state),indent=2))
(root/'viewer.rviz').write_text(yaml.safe_dump(config))
print('native RViz + cameras PASS',flush=True)
