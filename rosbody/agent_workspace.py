"""Prepare local agent workspaces and the ROS environment without reading secrets."""
import json
import os
from pathlib import Path
import shlex
import sys

ROOT = Path(__file__).resolve().parents[1]


def body_environment(domain, source=None):
    env = dict(os.environ if source is None else source)
    prefix = ROOT / "install" / "rosbody_interfaces"
    py = prefix / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if not py.is_dir():
        raise RuntimeError("ROS interfaces missing. Run `pixi run build` first.")
    for key, value in {"PYTHONPATH": str(py), "AMENT_PREFIX_PATH": str(prefix),
                       "DYLD_LIBRARY_PATH": str(prefix / "lib"),
                       "LD_LIBRARY_PATH": str(prefix / "lib"),
                       "PATH": str(Path(sys.executable).parent)}.items():
        env[key] = value + (os.pathsep + env[key] if env.get(key) else "")
    env.update(ROS_DISTRO="jazzy", ROS_VERSION="2", ROS_PYTHON_VERSION="3",
               ROS_DOMAIN_ID=str(domain), ROS_LOCALHOST_ONLY="1")
    return env


def save_metadata(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    tmp.replace(path)


def prepare_workspace(path, domain):
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    env = body_environment(domain)
    env["ROS_LOG_DIR"] = str(path / "ros_logs")
    # A deterministic wrapper avoids requiring the agent to rediscover ROS activation.
    wrapper = "#!/usr/bin/env bash\nset -euo pipefail\n"
    wrapper += "export PATH=" + shlex.quote(str(Path(sys.executable).parent)) + ":\"$PATH\"\n"
    wrapper += "source " + shlex.quote(str(ROOT / "scripts" / "ros_env.sh")) + "\n"
    wrapper += f"export ROS_DOMAIN_ID={domain} ROS_LOCALHOST_ONLY=1\n"
    wrapper += "export ROS_LOG_DIR=" + shlex.quote(env["ROS_LOG_DIR"]) + "\n"
    # Restore loader paths AFTER entering the shell, before exec replaces it.
    # macOS system shells strip inherited DYLD_* variables.
    for name, arguments in (("body", ' -m rosbody.cli'), ("body-python", '')):
        target = path / name
        target.write_text(wrapper + "exec " + shlex.quote(sys.executable) + arguments + ' "$@"\n')
        target.chmod(0o755)
    contract = (ROOT / "setup_body.md").read_text()
    start, discover = contract.index("## Start"), contract.index("## Discover and check")
    contract = contract[:start] + contract[discover:]
    contract = contract.replace("pixi run bash scripts/client.sh", "./body")
    contract = contract.replace("pixi run ros2", "ros2")
    contract = contract.replace("Run programs with `pixi run bash -c 'source scripts/ros_env.sh; python runs/my_stage.py'`.",
                                "Run programs with `./body-python runs/my_stage.py`. This launcher restores the ROS environment before starting Python.")
    (path / "setup_body.md").write_text(contract + f'''

## Embedded agent session overrides

You are running INSIDE ROSBody's independent Codex session. The host manages the simulator.
Do not launch another simulator, nested Codex, or change ROS_DOMAIN_ID. Your simulation domain is {domain}.
If the server is unavailable, report it; do not connect to a different domain or real hardware.
Your working directory is {path}. Write stage programs and evidence here.
Use `./body status`, `./body snapshot`, `./body move X Y Z`, `./body grip WIDTH`.
Run Python programs with `./body-python path/to/program.py`, `./body-python -m module`,
or `./body-python -c '...'`. Do not invoke the bare Python executable for ROS programs:
macOS system shells strip inherited DYLD_LIBRARY_PATH. The launcher restores it and
executes Python directly; your program still calls rclpy/Robot directly, without CLI action subprocesses.
Use `from rosbody.client import Robot` and `from rosbody.perception import locate_color`.
Image files returned by the snapshot service are outside your writable workspace but readable.
Use your native image reader to inspect them. Do not read simulator source, task diagnostics,
or evaluator files. Do not infer coordinates from repository scene definitions.
Do not reset a task to conceal failure. Ask for a new episode when recovery is impossible.
''')
    (path / "AGENTS.md").write_text('''# ROSBody embedded agent

Read setup_body.md before the first body operation. You are the task-executing agent, not
an advisor: carry out authorized simulation tasks through ./body or rosbody.client.Robot.
Run ROS Python programs via ./body-python so dynamic libraries resolve on macOS.
Use fresh observations, check action receipts, and inspect images before visually dependent
steps. Generate your own programs. Never read hidden simulator truth or copy diagnostic plans.
An image path printed by a program is not an inspected image. Use the native image viewer.
Do not claim object capture from gripper closure alone. Never replay timed-out physical goals.
Do not start nested agents, reset without a user request, access hardware, or change your domain.
Write only inside this workspace. Keep responses in the user's language and report evidence.
''')
    return env
