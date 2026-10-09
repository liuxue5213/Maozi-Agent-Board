#!/bin/zsh
# maozi-smcd 安装/卸载（SMC_PRIVILEGED_PLAN 方案 B / M3 手工版）
# 必须用 sudo 运行。安装：./install-smcd.sh install   卸载：./install-smcd.sh uninstall
# M1 闸门未通过（sudo python3 probes/mac/probe_smc.py 读不到）时不要安装。
set -euo pipefail
cd "$(dirname "$0")"

HELPER_DST="/Library/PrivilegedHelperTools/maozi-smcd"
PLIST_DST="/Library/LaunchDaemons/com.maozi.smc.plist"

install() {
  [ "$(id -u)" = "0" ] || { echo "需要 sudo 运行"; exit 1; }
  cc -O2 -Wall -framework IOKit -framework CoreFoundation -o /tmp/maozi-smcd maozi-smcd.c
  install -m 0755 -o root -g wheel /tmp/maozi-smcd "$HELPER_DST"
  install -m 0644 -o root -g wheel com.maozi.smc.plist "$PLIST_DST"
  launchctl bootstrap system "$PLIST_DST" 2>/dev/null || launchctl load -w "$PLIST_DST"
  sleep 1
  echo "--- 自检（应返回 {"ok":1,...}）---"
  python3 -c "
import socket
s = socket.socket(socket.AF_UNIX)
s.settimeout(2)
s.connect('/var/run/maozi-smc.sock')
s.sendall(b'{\"cmd\":\"read\"}\n')
print(s.recv(256).decode())
"
}

uninstall() {
  [ "$(id -u)" = "0" ] || { echo "需要 sudo 运行"; exit 1; }
  launchctl bootout system/com.maozi.smc 2>/dev/null || \
    launchctl unload -w "$PLIST_DST" 2>/dev/null || true
  rm -f "$PLIST_DST" "$HELPER_DST" /var/run/maozi-smc.sock
  echo "已卸载干净（plist / helper / socket 无残留）。"
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  *) echo "用法：sudo $0 install|uninstall"; exit 1 ;;
esac
