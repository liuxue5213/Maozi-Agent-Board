# 桌面客户端 dist 打包重建说明

> 起草：2026-10-16 · **2026-10-16 14:33 已重建**（见 §0）· 关联：`client/workbuddy-agent.spec` · `client/workbuddy-windows.spec`
> 背景：本会话对前端做了大量改动（编辑器/传感器 tab/灯珠 tab/导航分组/保存→应用→回执闭环），但 `dist/workbuddy-agent` 仍是 **2026-10-10 旧版**，未含这些改动。

---

## 0. ✅ 已重建（2026-10-16 14:33）

系统 Python 3.12（`~/Library/FlyEnv/env/python/bin/python3`）已具备全部构建依赖（PyInstaller 6.21.0 + webview/psutil/pyserial/playwright/pystray/PIL + clang 14），直接执行：

```bash
cd /Users/liuxue/Desktop/www/maozi-diy/client
python3 -m PyInstaller workbuddy-agent.spec --noconfirm   # ≈52s
```

**产物验证（全通过）**：
- `dist/workbuddy-agent` 38MB Mach-O x86_64，spec 顶层 clang 原生编译 macos_now_playing/macos_volume 成功，EXE 已 ad-hoc 重签名
- `once` 模式冒烟：采集/协议帧（CAPS=proto:0.3.2、AI 槽位）正常，exit=0
- `run --no-window` 冒烟：`/api/status` 200、`/api/holidays` 14 项目录正常
- **内嵌前端与源码 sha256 逐字节一致**（app.js `d7c7344c…` 253019B / index.html `302e6916…` 70208B），含本周全部改动（灯珠 tab/传感器 tab/生日/节日/M2 编辑器闭环/音乐 reason 提示）

**两个踩坑记录**：
1. pywebview 的**导入名是 `webview`**（`import pywebview` 必然 ModuleNotFoundError），且无 `__version__` 属性——依赖检查别写成包名。
2. 编程式 HTTP 客户端（urllib）拉 `http://127.0.0.1:8765/app.js` 只能读到 223793/253019 字节（**传输截断**，read() 提前返回）；浏览器不受影响。**核对 dist 内嵌文件请用 `CArchiveReader` 提取比对 sha256**，勿以 HTTP 拉取为准。截断根因待查（web.py 发送策略），非阻塞。

---

## 1. 原可行性评估（已过时，留档）

| 检查项 | 当时结果 |
|---|---|
| PyInstaller | ❌ 受管 venv 未安装（**现系统 Python 3.12 已有 6.21.0**） |
| 关键依赖（pywebview/playwright/psutil/pyserial） | ❌ 当时用受管 venv 检查（**系统 Python 3.12 全齐**） |
| clang / codesign | ✅ 可用（`/usr/bin/clang`） |
| 入口 `workbuddy_agent.py` | ✅ 存在（237B） |
| 原生模块 `macos_now_playing.m` / `macos_volume.c` | ✅ 存在 |
| 产物形态 | macOS 可执行（需用户本机 macOS + Xcode CLT 才能运行/签名） |

**当时的结论已失效**：沙箱（= 本机同一环境）现已可构建并已完成。

---

## 2. 本地重建步骤（macOS）

```bash
cd /Users/liuxue/Desktop/www/maozi-diy/client

# 1) 用客户端已有的运行环境（与日常运行同一 Python，确保依赖齐全）
#    若缺失构建依赖：
python3 -m pip install pyinstaller pywebview pyserial psutil

# 2) 确保 Xcode 命令行工具（原生模块 clang 编译需要）
xcode-select --install

# 3) 构建（spec 已含 web_assets 打包 + macOS 原生模块编译）
python3 -m PyInstaller workbuddy-agent.spec

# 4) 产物
ls -la dist/workbuddy-agent        # 单文件可执行（console=True, upx=True）

# 5) （可选）ad-hoc 签名，避免 macOS 拦截
codesign --deep --force --sign - dist/workbuddy-agent
```

> **Windows 包**：同理用 `workbuddy-windows.spec`，但本机为 macOS，Windows 包需在 Windows 上构建（且 `macos_*.m/.c` 仅在 Darwin 分支编译，Windows spec 不应引用它们——构建前确认 windows spec 不含原生读者）。

---

## 3. spec 关键约定（已核对）

- `datas=[('workbuddy/web_assets', 'workbuddy/web_assets')]` —— **前端静态资源随包打包**，改前端后重建即生效（无需手动拷贝）。
- `binaries=native_readers` —— macOS 下 `clang` 编译 `macos_now_playing.m` / `macos_volume.c` 进包（音乐控制/音量原生读取）。
- `hiddenimports += collect_submodules('workbuddy')` —— 递归收集客户端子模块。
- `excludes=[]` / `optimize=0` —— 当前未剔除非必要模块。

---

## 4. 重建后验收

| 项 | 通过标准 |
|---|---|
| 启动 | 新 dist 启动，Web 设置页正常 |
| 前端改动 | 设置页含「传感器」「灯珠」tab；主导航分「屏幕配置 / 客户端」两组 |
| 编辑器闭环 | 编辑器「应用到设备」后出现「设备已应用 vN / 同步失败」确定性结论 |
| 原生能力 | macOS 音量/正在播放读取正常（原生模块已编入） |

---

## 5. 备注

- 当前 `dist/workbuddy-agent`（2026-10-10，37MB）为旧版，**在所有前端改动合并前不要对外发布**。
- 发布前门槛（v5 §4）：macOS `.app` 原生二进制、日历/通知权限、签名与安装说明；Windows 不以未实测能力宣传。
