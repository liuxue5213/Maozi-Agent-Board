#!/bin/zsh
# AI WorkBuddy 状态屏 · 图形工作台启动器
# 双击后：客户端后台运行，自动打开浏览器工作台（页面/主题/提醒/应用全图形化配置）
cd "$(dirname "$0")/client" || exit 1
python3 -m workbuddy run --no-console --open-settings
