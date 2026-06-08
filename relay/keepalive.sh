#!/bin/bash
# relay_watch 看门狗：死了自动拉起来，确保消息一条不少
set -e

cd "$(dirname "$0")/.."

while true; do
    if ! pgrep -f "relay_watch.py" > /dev/null; then
        echo "[$(date '+%m-%d %H:%M:%S')] relay_watch 挂了，重启…"
        nohup uv run --with pypsrp --with sherpa-onnx python -u relay/relay_watch.py > /tmp/relay_watch_stdout.log 2>&1 &
        disown
    fi
    sleep 10
done
