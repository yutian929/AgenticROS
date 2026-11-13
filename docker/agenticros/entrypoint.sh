#!/bin/bash
set -e
source /etc/profile.d/rosbody.sh
mkdir -p /workspace/.agents/skills/setup-body /workspace/evidence /workspace/adapter
cp /opt/rosbody/AGENTS.md /workspace/AGENTS.md
cp /opt/rosbody/contract.md /workspace/contract.md
cp /opt/rosbody/body.md /workspace/body.md
cp /opt/rosbody/skills/setup-body/SKILL.md /workspace/.agents/skills/setup-body/SKILL.md
cp /config/codex.toml "$CODEX_HOME/config.toml"
# No source checkout, reference adapter, host credentials, or Docker socket is mounted.
exec "$@"
