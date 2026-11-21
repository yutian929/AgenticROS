"""Persistent, device-agnostic ROS clients behind a local Unix socket.

This is transport, not task orchestration. Types and endpoints come from callers
and the public ROS graph; the image contains no device IDL or task programs.
"""
import argparse
import fcntl
import json
import os
import re
from pathlib import Path
import socket
import signal
import socketserver
import sys
import threading
import time
import uuid

SOCKET = '/tmp/agenticros-ros.sock'


class Failure(Exception):
    def __init__(self, category, **details):
        self.category, self.details = category, details
        super().__init__(category)


def wait(future, timeout):
    event = threading.Event()
    future.add_done_callback(lambda _: event.set())
    if not event.wait(timeout):
        raise Failure('request_timeout')
    try:
        return future.result()
    except Exception as exc:
        raise Failure('server_failure', detail=type(exc).__name__) from exc


class Journal:
    """Reserve UUID durably before dispatch, including across client restarts."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def reserve(self, goal_id, record):
        goal_id = uuid.UUID(goal_id).hex
        with self.lock:
            try:
                with (self.root/(goal_id+'.json')).open('x') as stream:
                    json.dump(record, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                fd = os.open(self.root, os.O_RDONLY)
                try: os.fsync(fd)
                finally: os.close(fd)
            except FileExistsError:
                raise Failure('duplicate_uuid', goal_id=goal_id)
        return goal_id

    def load(self, goal_id):
        return json.loads((self.root/(uuid.UUID(goal_id).hex+'.json')).read_text())


class Clients:
    def __init__(self):
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from rclpy.callback_groups import ReentrantCallbackGroup
        rclpy.init()
        self.node = rclpy.create_node('agent_transport')
        self.group = ReentrantCallbackGroup()
        self.executor = MultiThreadedExecutor(num_threads=4)
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self.executor.spin, daemon=True)
        self.lock = threading.RLock()
        self.services, self.actions, self.subscriptions, self.samples, self.goals = {}, {}, {}, {}, {}
        self.sessions = {}
        self.service_calls = {}
        self.journal = Journal('/workspace/evidence/transport/goals')
        self.thread.start()

    def type(self, name, kind):
        from rosidl_runtime_py.utilities import get_message, get_service, get_action
        try:
            return {'msg': get_message, 'srv': get_service, 'action': get_action}[kind](name)
        except (ImportError, AttributeError) as exc:
            raise Failure('type_unavailable', type=name,
                          detail='Build public IDL in /workspace/ros_ws, then reload-clients') from exc

    def service(self, endpoint, typename):
        key = endpoint, typename
        with self.lock:
            if key not in self.services:
                self.services[key] = self.node.create_client(self.type(typename, 'srv'), endpoint, callback_group=self.group)
            return self.services[key]

    def action(self, endpoint, typename):
        from rclpy.action import ActionClient
        key = endpoint, typename
        with self.lock:
            if key not in self.actions:
                self.actions[key] = ActionClient(self.node, self.type(typename, 'action'), endpoint, callback_group=self.group)
            return self.actions[key]

    @staticmethod
    def fields(message):
        from rosidl_runtime_py.convert import message_to_ordereddict
        return message_to_ordereddict(message)

    @staticmethod
    def populate(message, data):
        from rosidl_runtime_py.set_message import set_message_fields
        set_message_fields(message, data)
        return message

    def call(self, request):
        c = self.service(request['endpoint'], request['type'])
        start = time.monotonic()
        if not c.wait_for_service(timeout_sec=request.get('discovery_timeout', 10)):
            with self.lock:
                self.service_calls[request['endpoint']]=dict(at=time.monotonic(), callable=False, server_success=None, error='discovery_failure')
            raise Failure('discovery_failure', discovery_ms=(time.monotonic()-start)*1000)
        discovered = time.monotonic()
        future = c.call_async(self.populate(c.srv_type.Request(), request.get('request', {})))
        try:
            result = wait(future, request.get('timeout', 10))
        except Failure as exc:
            with self.lock:
                self.service_calls[request['endpoint']]=dict(at=time.monotonic(), callable=None, server_success=None, error=exc.category)
            c.remove_pending_request(future)
            exc.details.update(discovery_ms=(discovered-start)*1000, response_ms=(time.monotonic()-discovered)*1000)
            raise
        with self.lock:
            self.service_calls[request['endpoint']] = dict(at=time.monotonic(), callable=True, server_success=getattr(result,'success',None), error='server_failure' if getattr(result,'success',None) is False else None)
        if getattr(result,'success',None) is False:
            raise Failure('server_failure', response=self.fields(result), discovery_ms=(discovered-start)*1000, response_ms=(time.monotonic()-discovered)*1000)
        return dict(response=self.fields(result), discovery_ms=(discovered-start)*1000,
                    response_ms=(time.monotonic()-discovered)*1000,
                    server_success=getattr(result, 'success', None))

    def subscribe(self, endpoint, typename):
        from rclpy.qos import qos_profile_sensor_data
        key = endpoint, typename
        with self.lock:
            if key not in self.subscriptions:
                def receive(msg):
                    with self.lock:
                        self.samples[key] = (time.monotonic(), self.fields(msg))
                self.subscriptions[key] = self.node.create_subscription(
                    self.type(typename, 'msg'), endpoint, receive, qos_profile_sensor_data, callback_group=self.group)
            sample = self.samples.get(key)
        return dict(received=sample is not None, age_seconds=time.monotonic()-sample[0] if sample else None,
                    message=sample[1] if sample else None)

    def graph(self):
        from rclpy.action import get_action_names_and_types
        return dict(topics=self.node.get_topic_names_and_types(), services=self.node.get_service_names_and_types(),
                    actions=get_action_names_and_types(self.node))

    def status(self):
        graph = self.graph()
        identities = set()
        for name,_ in graph['topics']+graph['services']:
            match=re.match(r'^/robot/([a-z][a-z0-9_]{0,95})/',name)
            if match: identities.add(match.group(1))
        result=[]
        for robot_id in sorted(identities):
            ns='/robot/'+robot_id
            topics=[(n,t) for n,t in graph['topics'] if n.startswith(ns+'/')]
            services=[n for n,t in graph['services'] if n.startswith(ns+'/')]
            # Our own retained subscriptions keep topic names in the graph.
            # Require a publisher or service endpoint as remote discovery evidence.
            discoverable=bool(services) or any(self.node.count_publishers(n)>0 for n,t in topics)
            samples=[]
            for name,types in topics:
                if 'std_msgs/msg/String' not in types: continue
                sample=self.subscribe(name,'std_msgs/msg/String')
                if not sample['message']: continue
                try:data=json.loads(sample['message']['data'])
                except (ValueError,KeyError):continue
                if isinstance(data,dict) and 'session_id' in data and 'state' in data:
                    samples.append(dict(endpoint=name,age_seconds=sample['age_seconds'],**data))
            fresh=[s for s in samples if s['age_seconds']<2]
            sessions={s['session_id'] for s in fresh}
            session=next(iter(sessions)) if len(sessions)==1 else None
            with self.lock:
                if session and self.sessions.get(robot_id)!=session:
                    self.service_calls={n:v for n,v in self.service_calls.items() if not n.startswith(ns+'/')}
                    self.sessions[robot_id]=session
                calls=list(self.service_calls.items())
            result.append(dict(robot_id=robot_id,namespace=ns,session_id=session,
                process_alive=True if fresh else None,ros_discoverable=discoverable,
                session_conflict=len(sessions)>1,observation_fresh=bool(fresh) and len(sessions)==1,
                service_callable={n:dict(age_seconds=time.monotonic()-v['at'],callable=v['callable'] if time.monotonic()-v['at']<3 else None,
                    server_success=v['server_success'],last_error=v['error']) for n,v in calls if n.startswith(ns+'/')},
                observations=samples,
                control_available={s['endpoint']:bool(s in fresh and len(sessions)==1 and s.get('control_available',False)) for s in samples}))
        return dict(robots=result,client_counts=dict(services=len(self.services),actions=len(self.actions),subscriptions=len(self.subscriptions)))

    def reconcile(self, record):
        """One read-only status RPC; no retry, no implicit cancel, no goal replay."""
        try:
            response = self.call(dict(endpoint=record['status_endpoint'], type=record['status_type'], timeout=3, discovery_timeout=3))
            return response
        except Failure as exc:
            return dict(error=exc.category, **exc.details)

    def goal(self, request):
        session = request.get('request', {}).get('session_id')
        if not session or not request.get('goal_id') or not request.get('status_endpoint'):
            raise ValueError('goal requires goal_id, request.session_id and public status_endpoint')
        c = self.action(request['endpoint'], request['type'])
        if not c.wait_for_server(timeout_sec=request.get('discovery_timeout', 10)):
            raise Failure('discovery_failure')
        message = self.populate(self.type(request['type'], 'action').Goal(), request['request'])
        record = {k:request[k] for k in ('endpoint','type','status_endpoint','goal_id')}
        record.update(session_id=session, request=request['request'], reserved_at=time.time(),
                      status_type=request.get('status_type', 'std_srvs/srv/Trigger'))
        gid = self.journal.reserve(request['goal_id'], record)
        from unique_identifier_msgs.msg import UUID
        future = c.send_goal_async(message, goal_uuid=UUID(uuid=list(uuid.UUID(gid).bytes)))
        # Keep late acceptance/results for reconciliation even when the caller times out.
        def accepted(f):
            try:
                handle = f.result()
                with self.lock:
                    self.goals[gid] = dict(handle=handle, result=handle.get_result_async() if handle.accepted else None)
            except Exception:
                pass
        future.add_done_callback(accepted)
        try:
            handle = wait(future, request.get('timeout', 10))
            return dict(goal_id=gid, accepted=handle.accepted, session_id=session)
        except Failure as exc:
            return dict(error=exc.category, goal_id=gid, outcome='unknown', replay_allowed=False,
                        reconciliation=self.reconcile(record))

    def result(self, request):
        gid = uuid.UUID(request['goal_id']).hex
        record = self.journal.load(gid)
        with self.lock: goal = self.goals.get(gid)
        if goal and goal['result'] is not None:
            try:
                response = wait(goal['result'], request.get('timeout', 10))
                return dict(goal_id=gid, session_id=record['session_id'], result=self.fields(response))
            except Failure as exc:
                error = exc.category
        else:
            error = 'goal_handle_unavailable'
        return dict(error=error, goal_id=gid, outcome='unknown', replay_allowed=False,
                    reconciliation=self.reconcile(record))

    def cancel(self, request):
        # Cancel only the reserved UUID; works after the transport process restarts.
        gid = uuid.UUID(request['goal_id']).hex
        record = self.journal.load(gid)
        try:
            response = self.call(dict(endpoint=record['endpoint']+'/_action/cancel_goal',
                                     type='action_msgs/srv/CancelGoal', request={'goal_info': {'goal_id': {'uuid': list(uuid.UUID(gid).bytes)}}},
                                     timeout=request.get('timeout', 10)))
        except Failure as exc:
            return dict(error=exc.category, goal_id=gid, outcome='unknown', replay_allowed=False,
                        reconciliation=self.reconcile(record))
        return dict(goal_id=gid, cancel=response, reconciliation=self.reconcile(record))

    def dispatch(self, request):
        op = request['op']
        if op == 'graph': return self.graph()
        if op == 'status': return self.status()
        if op == 'read': return self.subscribe(request['endpoint'], request['type'])
        if op == 'service': return self.call(request)
        if op in ('goal','result','cancel'): return getattr(self, op)(request)
        raise ValueError('unknown operation')

    def close(self):
        import rclpy
        self.executor.shutdown()
        self.thread.join()
        self.node.destroy_node()
        if rclpy.ok(): rclpy.shutdown()


def request(data):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(float(data.get('timeout',10))+float(data.get('discovery_timeout',10))+15)
        stream.connect(SOCKET)
        stream.sendall((json.dumps(data)+'\n').encode())
        return json.loads(stream.makefile('rb').readline(16*1024*1024))


def serve():
    clients = Clients()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            try:
                data = self.rfile.readline(1024*1024)
                if not data.endswith(b'\n'): raise ValueError('request too large')
                result = clients.dispatch(json.loads(data))
            except Failure as exc: result = dict(error=exc.category, **exc.details)
            except Exception as exc: result = dict(error='invalid_request', detail=str(exc))
            try: self.wfile.write((json.dumps(result)+'\n').encode())
            except OSError: pass
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    Path(SOCKET).unlink(missing_ok=True)
    try:
        with Server(SOCKET, Handler) as server:
            os.chmod(SOCKET,0o600)
            def stop(*_):
                threading.Thread(target=server.shutdown,daemon=True).start()
            signal.signal(signal.SIGTERM,stop)
            signal.signal(signal.SIGINT,stop)
            server.serve_forever()
    finally:
        clients.close()
        Path(SOCKET).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=('serve','graph','status','request'))
    parser.add_argument('json',nargs='?')
    args=parser.parse_args()
    if args.operation=='serve':
        with open('/tmp/agenticros-ros.lock','a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            serve()
    else:
        data=json.loads(args.json or sys.stdin.read()) if args.operation=='request' else dict(op=args.operation)
        result=request(data)
        print(json.dumps(result,ensure_ascii=False))
        if result.get('error'): sys.exit(1)

if __name__=='__main__':
    try: main()
    except (OSError,ValueError) as exc:
        print(json.dumps(dict(error='client_unavailable',detail=str(exc))))
        sys.exit(1)
