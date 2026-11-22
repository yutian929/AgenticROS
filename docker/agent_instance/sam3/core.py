"""ROS-independent request validation, durable records and model subprocess."""
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class RequestError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def validate(request, session):
    if request['session_id'] != session:
        raise RequestError('INVALID_REQUEST', 'service session changed; rediscover before submitting')
    if not request['image_topic'].startswith('/') or len(request['image_topic']) > 256:
        raise RequestError('INVALID_REQUEST', 'image_topic must be an absolute ROS topic')
    if request['frame_policy'] not in ('LATEST', 'EXACT'):
        raise RequestError('INVALID_REQUEST', 'frame_policy must be LATEST or EXACT')
    if not request['text_prompt'].strip() or len(request['text_prompt']) > 512:
        raise RequestError('INVALID_REQUEST', 'text_prompt must contain 1..512 characters')
    for key, low, high in [('max_image_age_sec', .01, 60), ('image_timeout_sec', .01, 30), ('score_threshold', 0, 1)]:
        value = request[key]
        if not math.isfinite(value) or not low <= value <= high:
            raise RequestError('INVALID_REQUEST', key+' outside supported bounds')
    if not 1 <= request['max_instances'] <= 32:
        raise RequestError('INVALID_REQUEST', 'max_instances must be 1..32')


def check_age(stamp_ns, now_ns, max_age):
    if stamp_ns <= 0 or stamp_ns > now_ns + 100_000_000 or now_ns-stamp_ns > max_age*1e9:
        raise RequestError('STALE_FRAME', 'source stamp is zero, future or stale; verify clock configuration')


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Records:
    def __init__(self, root, session):
        self.root = Path(root)/session
        self.root.mkdir(parents=True, exist_ok=False)
        self.lock = threading.RLock()

    def create(self, key, request):
        folder = self.root/key
        folder.mkdir(exist_ok=False)
        atomic_json(folder/'request.json', request)
        self.event(key, 'ACCEPTED')
        return folder

    def event(self, key, stage, **details):
        with self.lock, (self.root/key/'events.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(at=time.time(), stage=stage, **details), allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())

    def finish(self, key, result):
        atomic_json(self.root/key/'result.json', result)
        self.event(key, result['code'])


class Backend:
    """One eager-loaded worker, one inference at a time. No automatic replay."""
    def __init__(self):
        self.state = 'LOADING'
        self.error = ''
        self.metadata = {}
        self.process = None
        self.reader = None
        self.responses = queue.Queue()
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self._load, daemon=True)
        self.thread.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        except Exception as exc:
            self.responses.put({'state': 'FAILED', 'error': str(exc)})
        finally:
            self.responses.put({'state': 'FAILED', 'error': 'model worker exited'})

    def _load(self):
        try:
            self.process = subprocess.Popen([os.environ.get('SAM3_PYTHON', '/opt/inference/bin/python'),
                                             '/opt/sam3/worker.py'], stdin=subprocess.PIPE,
                                            stdout=subprocess.PIPE, text=True, bufsize=1)
            self.reader = threading.Thread(target=self._read, daemon=True)
            self.reader.start()
            deadline = time.monotonic()+600
            while not self.stopping.is_set():
                response = self.responses.get(timeout=max(.01, deadline-time.monotonic()))
                self.state = response['state']
                if self.state == 'FAILED':
                    raise RuntimeError(response.get('error', 'model startup failed'))
                if self.state == 'READY':
                    self.metadata = response
                    return
            self.close()
        except Exception as exc:
            self.fail(str(exc))

    def status(self):
        if self.process is not None and self.process.poll() is not None and self.state != 'FAILED':
            self.state, self.error = 'FAILED', 'model worker exited'
        return dict(model_state=self.state, model=self.metadata, error=self.error)

    def fail(self, error):
        self.state, self.error = 'FAILED', error
        print('SAM3 model FAILED: '+error, file=sys.stderr, flush=True)
        self.close()

    def infer(self, folder, request):
        # Caller holds lock; a timeout kills and reaps the worker, never resubmits.
        try:
            self.process.stdin.write(json.dumps(dict(folder=str(folder), **request))+'\n')
            self.process.stdin.flush()
            response = self.responses.get(timeout=180)
            if response.get('error'):
                raise RuntimeError(response['error'])
            return response['instances']
        except Exception as exc:
            self.fail(str(exc))
            raise

    def close(self):
        self.stopping.set()
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.reader is not None and self.reader is not threading.current_thread():
            self.reader.join(timeout=2)
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout):
                if stream is not None:
                    stream.close()
