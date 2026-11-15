"""One instance's LAN membership and discovery service; never sends robot goals."""
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Registry:
    def __init__(self, instance, instance_id, domain, host, dds_port, prefix, join_token, ttl=6):
        self.config = dict(instance=instance, instance_id=instance_id, domain_id=domain,
                           discovery_host=host, discovery_port=dds_port, discovery_prefix=prefix)
        self.join_token = join_token
        self.epoch = secrets.token_hex(16)
        self.ttl = ttl
        self.members = {}
        self.lock = threading.Lock()

    def join(self, body, token):
        if not hmac.compare_digest(token, self.join_token):
            raise PermissionError('invalid join token')
        if body.get('instance') != self.config['instance']:
            raise PermissionError('instance mismatch')
        robot = body.get('robot_id', '')
        session = body.get('session_id', '')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', robot) or not re.fullmatch(r'[0-9a-f]{12,32}', session):
            raise ValueError('invalid robot_id or session_id')
        with self.lock:
            old = self.members.get(robot)
            if old and old['deadline'] > time.monotonic():
                raise FileExistsError('robot_id already has an active client')
            lease = secrets.token_hex(32)
            self.members[robot] = dict(session_id=session, lease=lease,
                                       deadline=time.monotonic()+self.ttl)
            return dict(self.config, epoch=self.epoch, lease=lease, lease_seconds=self.ttl,
                        robot_id=robot, session_id=session, namespace='/robots/'+robot+'/s_'+session)

    def heartbeat(self, body, token):
        with self.lock:
            member = self.members.get(body.get('robot_id'))
            if (not member or body.get('epoch') != self.epoch
                    or body.get('session_id') != member['session_id']
                    or not hmac.compare_digest(token, member['lease'])
                    or member['deadline'] <= time.monotonic()):
                raise PermissionError('lease expired or identity changed; explicit client restart required')
            member['deadline'] = time.monotonic()+self.ttl
            return dict(lease_seconds=self.ttl, epoch=self.epoch)

    def roster(self):
        with self.lock:
            return dict(self.config, epoch=self.epoch, robots=[
                dict(robot_id=k, namespace='/robots/'+k+'/s_'+v['session_id'], session_id=v['session_id'],
                     connected=v['deadline'] > time.monotonic()) for k, v in sorted(self.members.items())])


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
                method = {'/join': registry.join, '/heartbeat': registry.heartbeat}.get(self.path)
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
    registry = Registry(os.environ['AGENTICROS_INSTANCE'], os.environ['AGENTICROS_INSTANCE_ID'],
                        int(os.environ['ROS_DOMAIN_ID']), os.environ['AGENTICROS_DDS_HOST'],
                        int(os.environ['AGENTICROS_DDS_PORT']), os.environ['AGENTICROS_DDS_PREFIX'],
                        os.environ['AGENTICROS_JOIN_TOKEN'])
    rclpy.init()
    node = Node('instance_registry', namespace='/agenticros')
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
