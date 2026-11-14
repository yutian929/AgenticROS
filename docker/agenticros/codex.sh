#!/bin/bash
set -e
exec /opt/agent-venv/bin/python /usr/local/bin/agenticros exec "$@"
