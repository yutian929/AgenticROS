"""LAN enrollment, fail-closed lease and immutable public sensor downloads."""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def post(url, body, token):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer '+token})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=1.5) as response:
        return json.load(response)


class Lease:
    """A missed deadline latches disconnected; late replies cannot re-enable control."""
    def __init__(self, ttl, started=None):
        self.deadline = (time.monotonic() if started is None else started)+ttl
        self.lost = False
        self.lock = threading.Lock()

    def valid(self):
        with self.lock:
            if time.monotonic() >= self.deadline:
                self.lost = True
            return not self.lost

    def renew(self, sent, ttl):
        with self.lock:
            now = time.monotonic()
            if self.lost or now >= self.deadline or now >= sent+ttl:
                self.lost = True
            else:
                self.deadline = sent+ttl


class Client:
    def __init__(self):
        self.session = secrets.token_hex(6)
        self.server_url = os.environ['AGENTICROS_SERVER'].rstrip('/')
        self.host = str(ipaddress.IPv4Address(os.environ['AGENTICROS_CLIENT_HOST']))
        self.port = int(os.environ['AGENTICROS_OBSERVATION_PORT'])
        sent = time.monotonic()
        self.membership = post(self.server_url+'/join', dict(
            instance=os.environ['AGENTICROS_INSTANCE'], robot_id=os.environ['AGENTICROS_ROBOT_ID'],
            session_id=self.session), os.environ['AGENTICROS_JOIN_TOKEN'])
        m = self.membership
        self.lease = Lease(m['lease_seconds'], sent)
        self.stop = threading.Event()
        self.secret = secrets.token_bytes(32)
        self.root = Path(os.getenv('OBSERVATION_ROOT', '/observations')).resolve()
        from lan_profile import configure
        configure(m['discovery_host'], m['discovery_port'], m['discovery_prefix'])
        os.environ['ROS_DOMAIN_ID'] = str(m['domain_id'])
        self.http = self.artifact_server()
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        self.worker = threading.Thread(target=self.heartbeat, daemon=True)
        self.worker.start()

    def identity(self):
        return {k: self.membership[k] for k in ('instance', 'instance_id', 'robot_id', 'namespace')}

    def heartbeat(self):
        while not self.stop.wait(1.):
            if self.lease.valid():
                sent = time.monotonic()
                try:
                    reply = post(self.server_url+'/heartbeat', {k: self.membership[k] for k in
                                 ('robot_id', 'session_id', 'epoch')}, self.membership['lease'])
                    if reply['epoch'] != self.membership['epoch']:
                        raise ValueError('server restarted')
                    self.lease.renew(sent, reply['lease_seconds'])
                except Exception as exc:
                    # Do not log URLs, tokens or request bodies. Preserve the
                    # fail-closed deadline while making transport failures visible.
                    print('LAN heartbeat failed: '+type(exc).__name__, flush=True)
            target = Path('/tmp/lan-health.json')
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(dict(updated=time.time(), connected=self.lease.valid())))
            temporary.replace(target)

    def signature(self, path):
        return hmac.new(self.secret, path.encode(), hashlib.sha256).hexdigest()

    def export(self, snapshot):
        snapshot.update(self.identity())
        snapshot['state']['frame'] = self.membership['namespace'].strip('/')+'/base'
        snapshot['calibration']['base_frame'] = snapshot['state']['frame']
        snapshot['calibration']['optical_frame'] = self.membership['namespace'].strip('/')+'/camera_optical_frame'
        artifacts = {}
        for key, name in [('rgb', 'rgb.png'), ('depth', 'depth.npy'), ('snapshot_json', 'snapshot.json')]:
            path = '/snapshots/'+snapshot['snapshot_id']+'/'+name
            snapshot[key+'_url'] = f'http://{self.host}:{self.port}{path}?token={self.signature(path)}'
            snapshot.pop(key+'_path', None)
            if name != 'snapshot.json':
                data = (self.root/path.lstrip('/')).read_bytes()
                artifacts[name] = dict(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data))
        snapshot['artifacts'] = artifacts
        (self.root/'snapshots'/snapshot['snapshot_id']/'snapshot.json').write_text(json.dumps(snapshot, indent=2))
        return snapshot

    def artifact_server(self):
        client = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = urllib.parse.parse_qs(url.query)
                allowed = re.fullmatch(r'/snapshots/'+client.session+r'-[0-9a-f]{12}/(rgb\.png|depth\.npy|snapshot\.json)', url.path)
                token = query.get('token', [''])[0]
                if not allowed or not hmac.compare_digest(token, client.signature(url.path)):
                    self.send_error(403)
                    return
                path = client.root/url.path.lstrip('/')
                if not path.is_file() or client.root not in path.resolve().parents:
                    self.send_error(404)
                    return
                data = path.read_bytes()
                self.send_response(200)
                self.send_header('Content-Type', 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return ThreadingHTTPServer(('0.0.0.0', self.port), Handler)

    def close(self):
        self.stop.set()
        self.worker.join(timeout=3)
        self.http.shutdown()
        self.http.server_close()
