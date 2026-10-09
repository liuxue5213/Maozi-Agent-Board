#!/bin/zsh
# 打包独立客户端（用户电脑无需安装 Python）
# 产物：client/dist/workbuddy-agent（单文件可执行）
set -euo pipefail
cd "$(dirname "$0")"

python3 -m pip install --quiet pyinstaller 2>&1 | tail -1 || true

python3 -m PyInstaller --onefile --name workbuddy-agent \
  --add-data "workbuddy/web_assets:workbuddy/web_assets" \
  --collect-submodules workbuddy \
  -y workbuddy_agent.py

echo "✅ 打包完成：$(pwd)/dist/workbuddy-agent"
echo "   测试：./dist/workbuddy-agent once"
