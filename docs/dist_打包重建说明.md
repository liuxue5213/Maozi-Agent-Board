# 桌面客户端 dist 打包重建说明

> 起草：2026-10-16 · 关联：`client/workbuddy-agent.spec` · `client/workbuddy-windows.spec`
> 背景：本会话对前端做了大量改动（编辑器/传感器 tab/灯珠 tab/导航分组/保存→应用→回执闭环），但 `dist/workbuddy-agent` 仍是 **2026-10-10 旧版**，未含这些改动。

---

## 1. 可行性结论（本环境无法构建）

| 检查项 | 结果 |
|---|---|
| PyInstaller | ❌ 受管 venv 未安装 |
| 关键依赖（pywebview/playwright/psutil/pyserial） | ❌ 当前 Python 不可导入 |
| clang / codesign | ✅ 可用（`/usr/bin/clang`） |
| 入口 `workbuddy_agent.py` | ✅ 存在（237B） |
| 原生模块 `macos_now_playing.m` / `macos_volume.c` | ✅ 存在 |
| 产物形态 | macOS 可执行（需用户本机 macOS + Xcode CLT 才能运行/签名） |

**结论：无法在沙箱内构建**（缺 PyInstaller 与依赖，且产物是 macOS `.app` 在本环境无意义）。必须由用户在本机 macOS 上重建。

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
