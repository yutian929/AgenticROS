#!/bin/bash
set -e
exec /opt/agent-venv/bin/python -c 'import os,sys; from codex_cli_bin import bundled_codex_path; p=str(bundled_codex_path()); os.execv(p,[p,*sys.argv[1:]])' "$@"
