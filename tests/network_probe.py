"""Read-only public-interface benchmark, executed inside the isolated Agent container."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time
import urllib.request
import rclpy
from rclpy.executors import MultiThreadedExecutor
from std_srvs.srv import Trigger
from agenticros_driver_interfaces.srv import Snapshot

mode, count, label = sys.argv[1], int(sys.argv[2]), sys.argv[3]
rows = []

def http(url):
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read()

start = time.monotonic()
try:
    roster = json.loads(http(os.environ['AGENTICROS_REGISTRY_URL']+'/robots'))
except Exception as exc:
    rows.append(dict(layer='registration',phase=mode,success=False,error=type(exc).__name__,response_ms=(time.monotonic()-start)*1000))
    p=Path('/workspace/evidence/bench-'+label+'.json');p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows,indent=2))
    raise
rows.append(dict(layer='registration', phase=mode, success=True, response_ms=(time.monotonic()-start)*1000))
namespaces = {x['robot_id']: x['namespace'] for x in roster['robots']}
rclpy.init()
node = rclpy.create_node('network_benchmark_'+str(os.getpid()))
executor = MultiThreadedExecutor(num_threads=4)
executor.add_node(node)
worker = threading.Thread(target=executor.spin, daemon=True)
worker.start()
clients = {(robot, kind): node.create_client(typ, ns+'/driver/'+kind)
           for robot, ns in namespaces.items() for kind, typ in [('status', Trigger), ('observe', Snapshot)]}

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
    if kind=='observe' and hasattr(request,'session_id'):
        request.session_id=next(x['session_id'] for x in roster['robots'] if x['robot_id']==robot)
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
            payload = json.loads(response.message if kind == 'status' else response.snapshot_json)
            if isinstance(payload,dict) and 'error' in payload:raise RuntimeError(payload['error'])
            row['success'] = True
        except Exception:
            row['error'] = 'server_failure'
    row['response_ms'] = (time.monotonic()-start)*1000
    rows.append(row)
    if kind == 'observe' and row['success']:
        start = time.monotonic()
        image = dict(layer='image_download', phase=mode, robot=robot, success=False)
        try:
            data = http(payload['rgb_url'])
            assert hashlib.sha256(data).hexdigest() == payload['artifacts']['rgb.png']['sha256']
            image.update(success=True, bytes=len(data))
        except Exception as exc:
            image['error'] = type(exc).__name__
        image['response_ms'] = (time.monotonic()-start)*1000
        rows.append(image)

try:
    for _ in range(count):
        if mode == 'concurrent':
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(call, ['robot_a', 'robot_b']))
        else:
            call('robot_a')
    if mode != 'cold':
        for _ in range(5):
            call('robot_a', 'observe')
finally:
    executor.shutdown()
    worker.join()
    node.destroy_node()
    rclpy.shutdown()
    p = Path('/workspace/evidence/bench-'+label+'.json')
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(rows, indent=2))
print(json.dumps({'label': label, 'samples': len(rows), 'failed': sum(not r['success'] for r in rows)}), flush=True)
