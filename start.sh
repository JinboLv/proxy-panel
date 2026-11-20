#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
panel_root="$PWD"
panel_session="proxy-panel"
panel_python="/usr/bin/python3"
if ! "$panel_python" -E -c 'from os import pidfd_open; import yaml' >/dev/null 2>&1; then
  echo 'PROXY 需要 Ubuntu 系统 Python（/usr/bin/python3），并需要 PyYAML 和 pidfd_open 支持。' >&2
  exit 1
fi
if tmux has-session -t "=$panel_session" 2>/dev/null; then
  echo "PROXY 已在 tmux 会话 $panel_session 中运行。"
else
  echo '正在启动 PROXY 后台服务…'
  printf -v panel_launch 'cd %q && exec %q -E -u -m proxy_panel' "$panel_root" "$panel_python"
  for panel_argument in "$@"; do
    printf -v panel_quoted '%q' "$panel_argument"
    panel_launch+=" $panel_quoted"
  done
  tmux new-session -d -s "$panel_session" "$panel_launch"
fi
"$panel_python" -E -m proxy_panel --wait-ready "$@"

if [[ -t 0 && -t 1 ]]; then
  echo 'Ctrl+C 停止代理。按 Ctrl+B 再按 D 可返回 SSH 终端，代理继续运行。'
  if [[ -n ${TMUX:-} ]]; then
    exec tmux switch-client -t "=$panel_session"
  fi
  exec tmux attach-session -t "=$panel_session"
fi
