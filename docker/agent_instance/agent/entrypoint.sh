#!/bin/bash
set -e
umask 0002
source /etc/profile.d/agenticros.sh
if [[ -n "${AGENTICROS_DDS_HOST:-}" ]]; then
  python3 /opt/agenticros/lan_profile.py
  export FASTRTPS_DEFAULT_PROFILES_FILE=/tmp/agenticros-lan.xml
  unset ROS_DISCOVERY_SERVER
fi
mkdir -p /workspace/.agents/skills/setup-body /workspace/evidence /workspace/ros_ws/src /workspace/capabilities
mkdir -p /workspace/.codex/agents
entrypoint_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$entrypoint_dir/rules.py" ]]; then
  # Host-staged rules also work with an already running, older image.
  python3 "$entrypoint_dir/rules.py" install /workspace
else
  # Starting the instance services must not replace synchronized rules.
  python3 /opt/agenticros/rules.py install /workspace --defaults
fi
cp /config/codex.toml "$CODEX_HOME/config.toml"
# No source checkout, reference adapter, host credentials, or Docker socket is mounted.
exec "$@"
