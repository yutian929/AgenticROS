"""Robot-local identity and optional immutable HTTP sensor files; no registration."""
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class RobotNetwork:
    def __init__(self):
        robot_id=os.environ['AGENTICROS_ROBOT_ID']
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}',robot_id):
            raise ValueError('invalid robot_id')
        self.session=secrets.token_hex(6)
        self.robot_id=robot_id
        self.namespace='/robots/'+robot_id
        self.host=str(ipaddress.IPv4Address(os.environ['AGENTICROS_CLIENT_HOST']))
        self.port=int(os.getenv('AGENTICROS_OBSERVATION_PORT','8800'))
        self.secret=secrets.token_bytes(32)
        self.root=Path(os.getenv('OBSERVATION_ROOT','/observations')).resolve()
        self.http=self.artifact_server()
        self.worker=threading.Thread(target=self.http.serve_forever,daemon=True)
        self.worker.start()

    def identity(self):
        return dict(robot_id=self.robot_id,namespace=self.namespace)

    def signature(self, path):
        return hmac.new(self.secret, path.encode(), hashlib.sha256).hexdigest()

    def export(self, snapshot):
        snapshot.update(self.identity())
        snapshot['state']['frame'] = self.namespace.strip('/')+'/base'
        snapshot['calibration']['base_frame'] = snapshot['state']['frame']
        snapshot['calibration']['optical_frame'] = self.namespace.strip('/')+'/camera_optical_frame'
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
        self.http.shutdown()
        self.worker.join(timeout=3)
        self.http.server_close()
