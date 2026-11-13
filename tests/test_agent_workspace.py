"""Exercise the same system-shell boundary used by the agent on macOS."""
import json
import shlex
import subprocess
import sys

import pytest

from rosbody.agent_workspace import prepare_workspace


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS loader regression')
def test_python_launcher_restores_loader_paths(tmp_path):
    workspace = tmp_path / 'workspace with spaces'
    env = prepare_workspace(workspace, 79)
    # Simulate the inherited environment after a system shell strips DYLD_*.
    for key in list(env):
        if key.startswith('DYLD_'):
            env.pop(key)
    script = workspace / 'check with spaces.py'
    script.write_text('''import ctypes, json, os, sys
ctypes.CDLL('librosbody_interfaces__rosidl_typesupport_fastrtps_c.dylib')
from rosbody.client import Robot
with Robot() as robot:
    print(json.dumps({'domain': os.environ['ROS_DOMAIN_ID'], 'args': sys.argv[1:]}))
''')
    result = subprocess.run(['/bin/zsh', '-lc', './body-python ' + shlex.quote(script.name) + ' "argument with spaces"'],
                            cwd=workspace, env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'domain': '79', 'args': ['argument with spaces']}
    result = subprocess.run(['/bin/zsh', '-lc', "./body-python -c 'import sys; sys.exit(7)'"],
                            cwd=workspace, env=env, capture_output=True, timeout=10)
    assert result.returncode == 7
