# WorkBuddy 客户端

AI WorkBuddy 桌面智能状态屏的电脑端（macOS / Windows / Linux，Python）。

页面自定义、Web 设置页与提醒组件的下一里程碑设计见 [NEXT_MILESTONE.md](../docs/NEXT_MILESTONE.md)；该功能尚未实现。

## 快速开始

```bash
cd client
pip3 install -r requirements.txt

python3 -m workbuddy once        # 单次采集，打印协议帧（不依赖设备）
python3 -m workbuddy run         # 常驻运行，自动扫描串口，插上设备即推送
python3 -m workbuddy ports       # 列出串口设备
python3 -m workbuddy run --no-serial   # 纯控制台预览模式
```

配置文件在 `~/.workbuddy/config.json`（首次运行不生成，用默认值）。

## 功能状态

| 模块 | 状态 |
|---|---|
| 硬件采集（CPU/内存/磁盘/电池/网络/运行时长） | ✅ 三平台（psutil） |
| macOS 温度/风扇（SMC） | ✅ 非 T2 机器；T2 自动降级 N/A（实测受限） |
| Windows CPU 温度/风扇 | ⏳ LHM 集成（下一里程碑，需管理员） |
| GPU（N 卡 NVML / A 卡 Linux sysfs） | ✅ 装了 pynvml 自动启用 |
| AI 任务检测（Claude/Codex/ZCode 零配置日志监听） | ✅ 本机实测（见下） |
| 串口推送 + 断线重连 + ESP32 上行解析 | ✅ 代码完成，待设备联调 |
| Web 设置页 / 主题 / 开机自启 / 打包 | ⏳ 后续里程碑 |

## AI 检测原理（零配置）

监听各工具本地会话日志的实时变化，推断状态机
`IDLE → THINKING → GENERATING → TOOL → DONE/ERROR`：

- Claude Code：`~/.claude/projects/**/*.jsonl`
- Codex：`~/.codex/sessions/**/rollout-*.jsonl`
- ZCode：`~/.zcode/cli/`（rollout 模型调用 + exec 命令日志 + artifacts 工具结果）

本地日志只提供阶段信号；运行中不显示百分比，明确完成时显示 100%。真实进度来源后续接入。

## 打包（发布阶段）

```bash
pip3 install pyinstaller
pyinstaller --onefile --name WorkBuddy -m workbuddy   # 待发布里程碑细化图标/签名
```
