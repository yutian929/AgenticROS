#!/bin/bash
set -e
umask 0002
source /etc/profile.d/agenticros.sh
mkdir -p /workspace/.agents/skills/setup-body /workspace/evidence /workspace/adapter
cp /opt/agenticros/AGENTS.md /workspace/AGENTS.md
cp /opt/agenticros/contract.md /workspace/contract.md
cp /opt/agenticros/skills/setup-body/SKILL.md /workspace/.agents/skills/setup-body/SKILL.md
cp /config/codex.toml "$CODEX_HOME/config.toml"
# No source checkout, reference adapter, host credentials, or Docker socket is mounted.
exec "$@"
