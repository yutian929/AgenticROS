"""Single Server identity leases and discovery service; never sends robot goals."""
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Registry:
    """HTTP owns unique identity/control leases, never ROS readiness.

    Presence combines enrollment and renewal. A new session cannot displace a live
    lease; expired sessions cannot re-enroll. Restart the robot explicitly instead.
    """
    def __init__(self, domain, host, dds_port, prefix, ttl=6, transport='tcp'):
        self.config = dict(domain_id=domain, discovery_host=host, discovery_port=dds_port,
                           discovery_prefix=prefix, discovery_transport=transport)
        self.epoch = secrets.token_hex(16)
        self.ttl = ttl
        self.members = {}
        self.retired = set()
        self.lock = threading.Lock()

    def presence(self, body, token=''):
        robot, session = body.get('robot_id', ''), body.get('session_id', '')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', robot) or not re.fullmatch(r'[0-9a-f]{12,32}', session):
            raise ValueError('invalid robot_id or session_id')
        with self.lock:
            now = time.monotonic()
            old = self.members.get(robot)
            if body.get('epoch'):
                if (not old or body['epoch'] != self.epoch or session != old['session_id']
                        or not hmac.compare_digest(token, old['lease'])):
                    raise PermissionError('identity changed or server restarted; explicitly restart robot')
                if body.get('control_lease_lost'):
                    old['deadline']=min(old['deadline'],now)
                elif old['deadline'] <= now:
                    raise PermissionError('lease expired; explicitly restart robot')
            elif old and old['session_id'] == session:
                # An initial reply may have been lost. Return the same live grant.
                if old['deadline'] <= now:
                    raise PermissionError('expired session cannot re-enroll')
            else:
                if (robot, session) in self.retired:
                    raise PermissionError('retired session')
                if old and old['deadline'] > now:
                    raise FileExistsError('robot_id already in use')
                if old:
                    self.retired.add((robot, old['session_id']))
                old = dict(session_id=session, lease=secrets.token_hex(32), deadline=now+self.ttl, last_seen=now)
                self.members[robot] = old
            old['last_seen']=now
            if body.get('epoch') and not body.get('control_lease_lost'):
                old['deadline'] = now+self.ttl
            return dict(self.config, epoch=self.epoch, lease=old['lease'],
                        lease_seconds=max(0, old['deadline']-now), robot_id=robot,
                        session_id=session, namespace='/robots/'+robot)

    def roster(self):
        with self.lock:
            return dict(self.config, epoch=self.epoch, robots=[
                dict(robot_id=k, namespace='/robots/'+k, session_id=v['session_id'],
                     process_alive=True if time.monotonic()-v['last_seen'] < self.ttl else None,
                     control_lease_valid=v['deadline'] > time.monotonic())
                for k, v in sorted(self.members.items())])


def http_server(registry, address):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log credentials or observation URLs.

        def reply(self, status, value):
            data = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, dict(registry.config, epoch=registry.epoch))
            elif self.path == '/robots':
                self.reply(200, registry.roster())
            else:
                self.reply(404, {'error': 'not found'})

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('invalid request size')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('object required')
                token = self.headers.get('Authorization', '').removeprefix('Bearer ')
                method = {'/presence': registry.presence}.get(self.path)
                if not method:
                    return self.reply(404, {'error': 'not found'})
                self.reply(200, method(body, token))
            except PermissionError as exc:
                self.reply(403, {'error': str(exc)})
            except FileExistsError as exc:
                self.reply(409, {'error': str(exc)})
            except (ValueError, TypeError):
                self.reply(400, {'error': 'invalid request'})

    return ThreadingHTTPServer(address, Handler)


def main():
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    from lan_profile import configure
    configure(os.environ['AGENTICROS_DDS_HOST'], os.environ['AGENTICROS_DDS_PORT'],
              os.environ['AGENTICROS_DDS_PREFIX'], server=True)
    registry = Registry(int(os.environ['ROS_DOMAIN_ID']), os.environ['AGENTICROS_DDS_HOST'],
                        int(os.environ['AGENTICROS_DDS_PORT']), os.environ['AGENTICROS_DDS_PREFIX'],
                        transport=os.getenv('AGENTICROS_DDS_TRANSPORT', 'tcp'))
    if registry.config['discovery_transport'] == 'tcp':
        # Retain discovery metadata across registry-process restarts inside this
        # container. Memberships and control leases deliberately remain volatile.
        from pathlib import Path
        cache = Path('/tmp/agenticros-discovery')
        cache.mkdir(exist_ok=True)
        os.chdir(cache)
    rclpy.init()
    node = Node('server_registry', namespace='/agenticros')
    pub = node.create_publisher(String, 'robots', 1)
    node.create_timer(1., lambda: pub.publish(String(data=json.dumps(registry.roster()))))
    server = http_server(registry, ('0.0.0.0', int(os.environ['AGENTICROS_REGISTRY_PORT'])))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        rclpy.spin(node)
    finally:
        server.shutdown()
        server.server_close()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
