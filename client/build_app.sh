#!/bin/zsh
# 打包独立客户端（用户电脑无需安装 Python）
# 产物：client/dist/workbuddy-agent（单文件可执行）
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --quiet pyinstaller 2>&1 | tail -1 || true

extra_args=()
if [[ "$(uname -s)" == "Darwin" ]]; then
  clang -fobjc-arc -fblocks workbuddy/macos_now_playing.m \
    -framework Foundation -o workbuddy/macos_now_playing
  extra_args+=(--add-binary "workbuddy/macos_now_playing:workbuddy")
  clang -std=c11 workbuddy/macos_volume.c -framework CoreAudio \
    -framework AudioToolbox -o workbuddy/macos_volume
  extra_args+=(--add-binary "workbuddy/macos_volume:workbuddy")
fi

python3 -m PyInstaller --onefile --name workbuddy-agent \
  --add-data "workbuddy/web_assets:workbuddy/web_assets" \
  "${extra_args[@]}" \
  --collect-submodules workbuddy \
  -y workbuddy_agent.py

echo "✅ 打包完成：$(pwd)/dist/workbuddy-agent"
echo "   测试：./dist/workbuddy-agent once"
