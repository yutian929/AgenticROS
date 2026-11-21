"""Read-only public-interface benchmark, executed inside the isolated Agent container."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import threading
import time
import rclpy
from rclpy.executors import MultiThreadedExecutor
from std_srvs.srv import Trigger
from sensor_msgs.msg import CompressedImage
from rclpy.qos import qos_profile_sensor_data

mode, count, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = []

namespaces = {robot:'/robot/'+robot for robot in ('robot_a','robot_b')}
sessions = {}
rclpy.init()
node = rclpy.create_node('network_benchmark_'+str(os.getpid()))
executor = MultiThreadedExecutor(num_threads=4)
executor.add_node(node)
worker = threading.Thread(target=executor.spin, daemon=True)
worker.start()
clients = {(robot, kind): node.create_client(typ, ns+'/driver/'+kind)
           for robot, ns in namespaces.items() for kind, typ in [('status', Trigger)]}

def call(robot, kind='status'):
    row = dict(layer='ros_data', phase=mode, robot=robot, operation=kind, success=False)
    start = time.monotonic()
    c = clients[robot, kind]
    if not c.wait_for_service(timeout_sec=10):
        row.update(error='discovery_failure', discovery_ms=(time.monotonic()-start)*1000)
        rows.append(row)
        return
    row['discovery_ms'] = (time.monotonic()-start)*1000
    start = time.monotonic()
    request=c.srv_type.Request()
    f = c.call_async(request)
    done = threading.Event()
    f.add_done_callback(lambda _: done.set())
    if not done.wait(12):
        row['error'] = 'request_timeout'
        c.remove_pending_request(f)
    else:
        try:
            response = f.result()
            if kind == 'status' and not response.success:
                raise RuntimeError('server failure')
            payload = json.loads(response.message)
            if isinstance(payload,dict) and 'error' in payload:raise RuntimeError(payload['error'])
            row['success'] = True
            if kind == 'status': sessions[robot]=payload['session_id']
        except Exception:
            row['error'] = 'server_failure'
    row['response_ms'] = (time.monotonic()-start)*1000
    rows.append(row)

try:
    for _ in range(count):
        if mode == 'concurrent':
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(call, ['robot_a', 'robot_b']))
        else:
            call('robot_a')
    if mode != 'cold':
        samples=[]
        subscription=node.create_subscription(CompressedImage,namespaces['robot_a']+'/camera/color/image_raw/compressed',lambda msg:samples.append(msg),qos_profile_sensor_data)
        deadline=time.monotonic()+20
        while len(samples)<5 and time.monotonic()<deadline:time.sleep(.1)
        rows.append(dict(layer='ros_image',phase=mode,robot='robot_a',success=len(samples)>=5 and all(len(m.data)>0 for m in samples),samples=len(samples)))
        node.destroy_subscription(subscription)
finally:
    executor.shutdown()
    worker.join()
    node.destroy_node()
    rclpy.shutdown()
    p = Path('/workspace/evidence/bench-'+label+'.json')
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows, indent=2))
print(json.dumps({'label': label, 'samples': len(rows), 'failed': sum(not r['success'] for r in rows)}), flush=True)

assert all(r["success"] for r in rows), "communication benchmark failures; see evidence"
