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
cp /opt/agenticros/AGENTS.md /workspace/AGENTS.md
cp /opt/agenticros/contract.md /workspace/contract.md
cp /opt/agenticros/skills/setup-body/SKILL.md /workspace/.agents/skills/setup-body/SKILL.md
mkdir -p /workspace/.codex/agents
cp /opt/agenticros/agents/arm-executor.toml /workspace/.codex/agents/arm-executor.toml
cp /config/codex.toml "$CODEX_HOME/config.toml"
# No source checkout, reference adapter, host credentials, or Docker socket is mounted.
exec "$@"
