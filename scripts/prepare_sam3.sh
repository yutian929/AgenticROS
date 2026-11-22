#!/bin/bash
# First-time server preparation. Daily startup remains: bash agent.sh sam3
set -euo pipefail
cd "$(dirname "$0")/.."
check_only=false
arguments=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check-only) check_only=true; shift ;;
    --domain-id)
      [[ $# -ge 2 ]] || { echo '--domain-id requires a value' >&2; exit 1; }
      [[ "$2" =~ ^[0-9]{1,3}$ ]] && (( 10#$2 <= 101 )) || {
        echo '--domain-id must be 0..101' >&2; exit 1;
      }
      arguments+=(--domain-id "$2"); shift 2 ;;
    -h|--help)
      echo 'Usage: bash scripts/prepare_sam3.sh [--domain-id 42] [--check-only]'
      echo 'Default: check GPU prerequisites, download SAM3 weights from ModelScope, build image, verify CUDA.'
      echo 'If NVIDIA Container Toolkit is missing/unconfigured, ask before installing/configuring it on local Debian/Ubuntu.'
      echo 'Installation restarts Docker only when no containers are running. It does not install CUDA Toolkit or GPU drivers.'
      echo '--check-only: report missing prerequisites without downloading, building, or changing host configuration.'
      exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done
if "$check_only"; then
  exec python3 scripts/setup_sam3.py "${arguments[@]}"
fi
command -v docker >/dev/null || { echo 'Install Docker and its Compose plugin first.' >&2; exit 1; }
docker info >/dev/null
docker compose version >/dev/null
if ! docker info --format '{{json .Runtimes}}' | python3 -c 'import json,sys; sys.exit(0 if "nvidia" in json.load(sys.stdin) else 1)'; then
  command -v nvidia-smi >/dev/null && nvidia-smi -L || {
    echo 'Install a working NVIDIA host GPU driver first; this script does not replace drivers.' >&2; exit 1;
  }
  command -v apt-get >/dev/null || { echo 'Automatic Toolkit installation requires Debian/Ubuntu.' >&2; exit 1; }
  endpoint="${DOCKER_HOST:-$(docker context inspect --format '{{.Endpoints.docker.Host}}')}"
  [[ "$endpoint" == unix:///var/run/docker.sock ]] || {
    echo 'Automatic Toolkit configuration supports only local rootful Docker at /var/run/docker.sock.' >&2; exit 1;
  }
  [[ -z "$(docker ps -q)" ]] || {
    echo 'Refusing Docker restart: containers are running. Stop/reconcile them explicitly before preparation.' >&2; exit 1;
  }
  echo 'NVIDIA Container Toolkit is missing or not configured for Docker.'
  echo 'This will install/configure NVIDIA Container Toolkit on this host and restart Docker.'
  echo 'It will NOT install CUDA Toolkit or replace the GPU driver. sudo may be required.'
  if [[ ! -t 0 ]]; then
    echo 'Installation requires confirmation in an interactive terminal; exiting without changes.' >&2
    exit 1
  fi
  answer=''
  read -r -p 'Install/configure NVIDIA Container Toolkit now? [y/N] ' answer || true
  case "$answer" in
    y|Y|yes|YES) ;;
    *) echo 'Installation declined; exiting without changes.'; exit 1 ;;
  esac
  privilege=()
  if [[ $(id -u) != 0 ]]; then
    command -v sudo >/dev/null || { echo 'sudo is required to configure NVIDIA Toolkit.' >&2; exit 1; }
    privilege=(sudo)
  fi
  echo 'Installing NVIDIA Container Toolkit and configuring Docker. Docker will restart after a second idle check.'
  "${privilege[@]}" apt-get update
  "${privilege[@]}" apt-get install -y --no-install-recommends ca-certificates curl gnupg
  temporary=$(mktemp -d)
  trap 'rm -rf "$temporary"' EXIT
  curl --proto '=https' -fsSL https://nvidia.github.io/libnvidia-container/gpgkey |
    gpg --dearmor > "$temporary/nvidia-container-toolkit-keyring.gpg"
  curl --proto '=https' -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list |
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' > "$temporary/nvidia-container-toolkit.list"
  "${privilege[@]}" install -m 644 "$temporary/nvidia-container-toolkit-keyring.gpg" /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  "${privilege[@]}" install -m 644 "$temporary/nvidia-container-toolkit.list" /etc/apt/sources.list.d/nvidia-container-toolkit.list
  "${privilege[@]}" apt-get update
  "${privilege[@]}" apt-get install -y nvidia-container-toolkit
  "${privilege[@]}" nvidia-ctk runtime configure --runtime=docker
  [[ -z "$(docker ps -q)" ]] || {
    echo 'Containers appeared during setup. Toolkit configured, but Docker was NOT restarted; reconcile them first.' >&2; exit 1;
  }
  "${privilege[@]}" systemctl restart docker
  rm -rf "$temporary"
  trap - EXIT
fi
exec python3 scripts/setup_sam3.py "${arguments[@]}" --download --build
