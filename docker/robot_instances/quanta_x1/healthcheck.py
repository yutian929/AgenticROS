import json
import time
from pathlib import Path
state = json.loads(Path('/tmp/quanta-health.json').read_text())
assert time.time() - state['reported_at_unix'] < 15, 'simulation feedback stale'
assert state['simulation_ready'], 'simulation not ready'
assert not state['fault'], state['fault']
