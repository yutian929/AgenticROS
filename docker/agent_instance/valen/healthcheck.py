"""Query live readiness; startup waiting exits immediately on model failure."""
import argparse
import json
import sys
import time
import rclpy
from std_srvs.srv import Trigger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--wait', action='store_true')
    parser.add_argument('--timeout', type=float, default=900)
    args = parser.parse_args()
    rclpy.init()
    node = rclpy.create_node('valen_healthcheck')
    last_state = None
    deadline = time.monotonic()+args.timeout
    try:
        client = node.create_client(Trigger, '/decision/valen/status')
        while True:
            state = 'DISCOVERING'
            data = {'model_state':state}
            if client.wait_for_service(timeout_sec=3):
                future = client.call_async(Trigger.Request())
                rclpy.spin_until_future_complete(node, future, timeout_sec=3)
                if future.done():
                    data = json.loads(future.result().message)
                    state = data['model_state']
                else:
                    client.remove_pending_request(future)
                    data = {'model_state':'UNRESPONSIVE'}
                    state = data['model_state']
            if args.status or state == 'FAILED' or (args.wait and state != last_state):
                print(json.dumps(data), flush=True)
            if state == 'READY':
                return 0
            if state == 'FAILED':
                return 2
            if not args.wait:
                return 1
            if time.monotonic() >= deadline:
                print('Valen readiness timed out; last state: '+state, file=sys.stderr)
                return 1
            last_state = state
            time.sleep(.5)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    sys.exit(main())
