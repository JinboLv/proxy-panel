#!/usr/bin/env bash
# Source this from the shell that will launch the program using PROXY.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo '请运行 source ~/proxy-panel/use-proxy.sh，让设置应用到当前终端。' >&2
  exit 1
fi

if ! proxy_panel_url="$(
  set -o pipefail
  curl -qfsS --noproxy '*' --max-time 1 \
    --unix-socket "$HOME/.local/share/proxy-panel/panel.sock" \
    http://127.0.0.1:18080/api/access |
    /usr/bin/python3 -E -c 'import json, sys; print(json.load(sys.stdin)["http"])'
)"; then
  unset proxy_panel_url
  echo '无法读取 PROXY 代理地址。请先启动 ~/proxy-panel/start.sh。' >&2
  return 1
fi

export http_proxy="$proxy_panel_url" https_proxy="$proxy_panel_url"
export HTTP_PROXY="$proxy_panel_url" HTTPS_PROXY="$proxy_panel_url"
export all_proxy="$proxy_panel_url" ALL_PROXY="$proxy_panel_url"
unset proxy_panel_url

proxy_panel_bypass="${no_proxy:-${NO_PROXY:-}}"
for proxy_panel_local in localhost 127.0.0.1 ::1; do
  case ",$proxy_panel_bypass," in
    *",$proxy_panel_local,"*) ;;
    *) proxy_panel_bypass="${proxy_panel_bypass:+$proxy_panel_bypass,}$proxy_panel_local" ;;
  esac
done
export no_proxy="$proxy_panel_bypass" NO_PROXY="$proxy_panel_bypass"
unset proxy_panel_bypass proxy_panel_local
