"""ALOHA readiness: the shared simulation is producing fresh camera data."""
from pathlib import Path
import time

ready = Path('/tmp/body-camera-ready')
assert ready.exists() and time.time()-ready.stat().st_mtime < 15, 'ALOHA camera stalled'
