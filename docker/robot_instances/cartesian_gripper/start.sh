#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
docker compose up -d --build --no-recreate --wait cartesian_gripper
binding=$(docker compose port cartesian_gripper 8080)
viewer_url="http://localhost:${binding##*:}/vnc.html?autoconnect=1&resize=scale"
viewer_password=$(docker compose exec -T cartesian_gripper cat /viewer-auth/password)
printf '\nRViz2: %s\n连接密码: %s\n' "$viewer_url" "$viewer_password"
echo '请在浏览器中输入以上密码。'
if [[ "$(uname -s)" == Darwin ]] && command -v open >/dev/null 2>&1; then
  open "$viewer_url" >/dev/null 2>&1 &
elif [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$viewer_url" >/dev/null 2>&1 &
else
  echo '未检测到可用的桌面浏览器，请手动打开以上地址。'
fi
