"""Validated ROSBody settings; CLI overrides the selected TOML file."""
from pathlib import Path
import tomllib

from .agent_workspace import ROOT

DEFAULT_CONFIG = ROOT / 'rosbody.toml'
DEFAULTS = {'model': None, 'reasoning_effort': None, 'viewer': False, 'verbose': False, 'seed': 0}
EFFORTS = ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')


def configure(args):
    path = Path(args.config).expanduser().resolve() if args.config else DEFAULT_CONFIG
    values = dict(DEFAULTS)
    if path.exists():
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            raise RuntimeError(f'Invalid ROSBody config {path}: {exc}') from exc
        unknown = data.keys() - values.keys()
        if unknown:
            raise RuntimeError(f'Unknown ROSBody config keys: {", ".join(sorted(unknown))}')
        values.update(data)
    elif args.config:
        raise RuntimeError(f'Config file does not exist: {path}')
    for key, value in values.items():
        if key in ('viewer', 'verbose') and type(value) is not bool:
            raise RuntimeError(f'{key} must be a TOML boolean (true/false)')
        if key == 'seed' and (type(value) is not int or value < 0):
            raise RuntimeError('seed must be a nonnegative integer')
        if key == 'model' and value is not None and (not isinstance(value, str) or not value.strip()):
            raise RuntimeError('model must be a nonempty string')
        if key == 'reasoning_effort' and value is not None and value not in EFFORTS:
            raise RuntimeError(f'reasoning_effort must be one of {EFFORTS}')
        override = getattr(args, key, None)
        setattr(args, key, value if override is None else override)
    if args.seed < 0:
        raise RuntimeError('seed must be nonnegative')
    args.config_path = str(path) if path.exists() else None
    return args
