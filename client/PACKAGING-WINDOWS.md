# Windows 打包与分发说明

## 构建（必须在 Windows 机器上，PyInstaller 不能跨平台）

```bat
cd client
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller workbuddy-windows.spec --noconfirm
```

产物：`client\dist\workbuddy-agent\workbuddy-agent.exe`（onedir，整目录可拷贝分发）。

自检：

```bat
dist\workbuddy-agent\workbuddy-agent.exe once
```

启动：双击 `start_workbuddy.bat`（托盘常驻），或把它放进 `shell:startup` 开机自启；
客户端内「设置 → 系统 → 开机自动启动」会写 HKCU Run 注册表，与脚本二选一即可。

## 各功能依赖

| 功能 | 依赖 | 缺失时表现 |
|---|---|---|
| 遥测/会话/串口/Web | 已在 requirements.txt | 不启动 |
| 音量读写 | `pycaw` + `comtypes`（requirements 已带 win32 标记） | 回退虚拟键：能调，屏上音量格 `--` |
| 当前曲目/SMTC | `winsdk`（requirements 已带 win32 标记） | 设置页显示「不支持读取」，媒体键仍有效 |
| GPU（仅 N 卡） | `pynvml` | 诚实隐藏 GPU 卡 |
| 温度/风扇 | [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases)，**以管理员运行一次**后重启客户端 | 回退系统热区；拿不到则自检面板标黄并提示 |
| 托盘 | `pystray` + `Pillow` | 退回控制台常驻 |
| 迷你悬浮窗 `--mini` | `pywebview`（走 EdgeChromium 后端） | 退回浏览器打开 /mini |

## 已知注意事项

- WebView2 运行时：Win10/11 绝大多数已预装，缺失时 pywebview 不可用（仅影响 `--mini`）。
- 杀软误报：PyInstaller onefile 常被误报，这里用 onedir + 无 UPX 压缩已降低概率；
  首次分发建议加数字签名。
- 首次运行 8765 端口仅绑定 127.0.0.1，不出本机；如需局域网访问要改 web.py。
