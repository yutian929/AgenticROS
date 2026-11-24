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


def strict_json(value):
    def invalid(value):
        raise ValueError('non-finite JSON value: '+value)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: '+key)
            result[key] = value
        return result
    result = json.loads(value, parse_constant=invalid, object_pairs_hook=pairs)
    # Also catches exponent overflow such as 1e999.
    json.dumps(result, allow_nan=False)
    return result


def questions(value):
    if not isinstance(value, dict) or not 1 <= len(value) <= 8:
        raise ValueError('questions must contain 1..8 named questions')
    for key, question in value.items():
        if not isinstance(key, str) or not key or len(key) > 64 or not isinstance(question, dict):
            raise ValueError('invalid question ID or question object')
        if set(question) - {'type', 'instructions', 'criteria', 'image'}:
            raise ValueError('unsupported question fields')
        instruction = question.get('instructions')
        if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4096:
            raise ValueError('instructions must contain 1..4096 characters')
        kind, criteria = question.get('type'), question.get('criteria')
        if kind != 'bbox' and 'image' in question:
            raise ValueError('image index is only supported for bbox')
        if kind == 'choice':
            if not isinstance(criteria, dict) or not 2 <= len(criteria) <= 32:
                raise ValueError('choice needs 2..32 named options')
            if any(not key or len(key) > 64 for key in criteria):
                raise ValueError('invalid choice option name')
            descriptions = list(criteria.values())
        elif kind == 'score':
            if not isinstance(criteria, list) or not 2 <= len(criteria) <= 16:
                raise ValueError('score needs 2..16 ordered descriptions')
            descriptions = criteria
        elif kind in ('noul', 'bbox'):
            if criteria is not None:
                raise ValueError('put '+kind+' criteria in instructions')
            if kind == 'bbox' and (type(question.get('image', 0)) is not int or question.get('image', 0) != 0):
                raise ValueError('bbox image must be 0 for the single input image')
            descriptions = []
        else:
            raise ValueError('supported types: choice, noul, score, bbox')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 2048 for v in descriptions):
            raise ValueError('criteria must be nonempty text, at most 2048 characters each')
    return value


def validate(request, session):
    try:
        if request['session_id'] != session:
            raise ValueError('service session changed; rediscover before submitting')
        for field in ('state_json', 'questions_json'):
            if len(request[field].encode()) > 32768:
                raise ValueError(field+' exceeds 32 KiB')
        state = strict_json(request['state_json'])
        qs = questions(strict_json(request['questions_json']))
        topic = request['image_topic']
        if any(q['type'] == 'bbox' for q in qs.values()) and not topic:
            raise ValueError('bbox requires image_topic')
        if topic:
            if not topic.startswith('/') or len(topic) > 256:
                raise ValueError('image_topic must be an absolute ROS topic')
            if request['frame_policy'] not in ('LATEST', 'EXACT'):
                raise ValueError('frame_policy must be LATEST or EXACT')
            for key, low, high in [('max_image_age_sec', .01, 60), ('image_timeout_sec', .01, 30)]:
                if not math.isfinite(request[key]) or not low <= request[key] <= high:
                    raise ValueError(key+' outside supported bounds')
            stamp = request['image_stamp']
            if request['frame_policy'] == 'EXACT' and (
                    stamp['sec'] < 0 or not 0 <= stamp['nanosec'] < 1_000_000_000
                    or stamp['sec']*1_000_000_000+stamp['nanosec'] <= 0):
                raise ValueError('EXACT needs a positive valid timestamp')
        return state, qs
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise RequestError('INVALID_REQUEST', str(exc)) from exc


def validate_answers(answers, qs):
    """Never allow a model response to expand the caller's action space."""
    if not isinstance(answers, dict) or set(answers) != set(qs):
        raise ValueError('answer IDs differ from questions')
    def probability(value):
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('invalid probability/confidence')
    for key, question in qs.items():
        answer = answers[key]
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            raise ValueError('answer type differs from question')
        kind = question['type']
        if kind == 'noul':
            probability(answer.get('noul'))
            continue
        if kind == 'bbox':
            box, point = answer.get('bbox'), answer.get('point')
            if not isinstance(box, list) or len(box) != 4 or not isinstance(point, list) or len(point) != 2:
                raise ValueError('bbox requires four coordinates and a two-coordinate point')
            for coordinate in box + point:
                probability(coordinate)
            x1, y1, x2, y2 = box
            if not (x1 < x2 and y1 < y2):
                raise ValueError('bbox edges must satisfy x1<x2 and y1<y2')
            if not all(math.isclose(value, center, abs_tol=1e-4) for value, center in
                       zip(point, ((x1+x2)/2, (y1+y2)/2))):
                raise ValueError('bbox point must be the normalized box center')
            probability(answer.get('confidence'))
            continue
        keys = set(question['criteria']) if kind == 'choice' else {str(i) for i in range(len(question['criteria']))}
        probs = answer.get('probabilities')
        if not isinstance(probs, dict) or set(probs) != keys:
            raise ValueError('probabilities must cover exactly the supplied options')
        for p in probs.values():
            probability(p)
        if not math.isclose(sum(probs.values()), 1, abs_tol=1e-6):
            raise ValueError('probabilities must sum to one')
        probability(answer.get('confidence'))
        if kind == 'choice':
            if answer.get('choice') not in keys or probs[answer['choice']] < max(probs.values()):
                raise ValueError('choice must select a highest-probability supplied option')
        else:
            score = answer.get('score')
            expected = sum(int(k)*p for k, p in probs.items())
            if type(score) not in (int, float) or not math.isfinite(score) or not math.isclose(score, expected, abs_tol=1e-6):
                raise ValueError('score differs from expected ordinal level')
    return answers



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
            self.process = subprocess.Popen([os.environ.get('VALEN_PYTHON', '/opt/inference/bin/python'),
                                             '/opt/valen/worker.py'], stdin=subprocess.PIPE,
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
        print('Valen model FAILED: '+error, file=sys.stderr, flush=True)
        self.close()

    def infer(self, folder, request):
        # Caller holds lock; a timeout kills and reaps the worker, never resubmits.
        try:
            self.process.stdin.write(json.dumps(dict(folder=str(folder), **request))+'\n')
            self.process.stdin.flush()
            response = self.responses.get(timeout=180)
            if response.get('error'):
                raise RuntimeError(response['error'])
            return response['answers']
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
