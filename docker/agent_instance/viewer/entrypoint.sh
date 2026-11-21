#!/bin/bash
set -eo pipefail
source /opt/ros/humble/setup.bash
set -u
# X11 abstract sockets also share the host network namespace. Each viewer needs
# its own display number, even though its filesystem and /tmp are private.
export DISPLAY=":${AGENTICROS_VNC_PORT}"
exec python3 /opt/viewer/desktop.py
