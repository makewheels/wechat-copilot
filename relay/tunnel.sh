#!/usr/bin/env bash
# 起 Mac -> 跳板机(49.233.60.29) -> 服务器(101.42.94.17) 的 WinRM 端口转发：
#   127.0.0.1:15985  ->  101.42.94.17:5985
# 前提：reverse-http-tunnel 的本地代理(127.0.0.1:28080)已经在跑。
# 直连北京被 GFW/中间盒挡掉，只能经这条反向隧道走。
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ENVF="$HOME/workspace/tools/reverse-http-tunnel/src/mac/local.env"
set -a; source "$ENVF"; set +a

if lsof -nP -iTCP:15985 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "tunnel 已在运行 (127.0.0.1:15985)"; exit 0
fi

export PXUSER="$PROXY_USERNAME" PXPASS="$PROXY_PASSWORD"
ssh -f -N \
  -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -i "$SSH_KEY" \
  -o "ProxyCommand=/usr/bin/python3 $HERE/pconnect.py $TARGET_HOST 22" \
  -L 127.0.0.1:15985:101.42.94.17:5985 \
  "$TARGET_USER@$TARGET_HOST"

sleep 3
if lsof -nP -iTCP:15985 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "tunnel up: 127.0.0.1:15985 -> 101.42.94.17:5985"
else
  echo "tunnel 失败"; exit 1
fi
