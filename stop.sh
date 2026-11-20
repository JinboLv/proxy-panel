#!/usr/bin/env bash
set -euo pipefail
if ! tmux has-session -t '=proxy-panel' 2>/dev/null; then
  echo 'PROXY 未在 proxy-panel 会话中运行。'
  exit 0
fi
tmux send-keys -t '=proxy-panel:' C-c
echo '已向 PROXY 发送退出信号；等待当前测速结束后，代理会一并关闭。'
