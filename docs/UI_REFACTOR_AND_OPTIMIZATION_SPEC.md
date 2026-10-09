# 开发说明文档 · AI Agent 状态屏 UI 重构与优化

> 文档范围：基于竞品「GlanceMate Bridge」的界面复刻重构方案 + 最初 20 条优化建议的归集与落地规划。
> 适用项目：maozi-diy（ESP32-S3 触控状态屏 + 桌面上位机 `client/workbuddy/`）。
> 配套文档：`docs/SMC_PRIVILEGED_PLAN.md`（T2 温度/风扇特权采集规划）、`docs/NEXT_MILESTONE.md`。
> 最后更新：2026-10-09

---

## 0. 背景与目标

现状：原有 Web 设置页为单页堆叠，显示效果差、不可用；设备屏（固件 `ui.cpp`）从未编译，配色通道待实机定论。
目标：以「方便操作编辑器、直观看各 agent 实时进度、类似电脑副屏的多窗口可视化与快捷方式、提升操作效率」为核心，复刻竞品信息架构，并落地 20 条优化项，使界面达可直接投入使用质量。

### 0.1 现有能力基线（重构前已具备）
- 后端：HTTP 轮询（`GET /api/status`，默认 2.5s）、`aiwatch` 聚合 claude/codex/zcode/workbuddy 四来源、psutil 硬件采集、`telemetry_age` 新鲜度、SSE 尚未接入。
- 客户端：系统托盘常驻（`tray.py`）、开机自启（`autostart.py`）、背景图上传（CSP 已放开 `blob:`）、OTA/诊断导出。
- 设备协议：纯文本 KV 串口（telemetry / CFG / IMG / OTA / HB）。

### 0.2 关键约束（务必遵守）
- **固件从未编译**：所有 C++ 改动仅静态/数学核验，`ui.cpp` 视觉效果需 `pio run` 真机定论。
- **G/B 通道待定**：不盲改 `rgb_order`，需 `PANEL_COLOR_SELFTEST=1` 烧录判读。
- **字库硬约束**：改 UI 文案或字号后必须重跑 `tools/gen_fonts.py`；非 GB2312 符号须写进 `EXTRA`。
- **CSP**：`web.py` 仅 `script-src 'self'`，禁止内联 `<script>`/`<style>`/`on*=`。
- **样式守卫**：`tests/test_style_tokens.py` 锁死三层令牌（原色层→语义层→组件）、深色映射、id 完整性；新增样式不得硬编码色值。
- **T2 机型**：本机 MacBookPro16,1（T2）无法读取 CPU 温度/风扇，caps 自动隐藏，需特权 helper（见 SMC 规划）。

---

## 1. 竞品复刻 UI 重构（GlanceMate Bridge）

### 1.1 竞品信息架构还原（7 张截图）
竞品为「桌面状态中心」类应用，左侧固定导航 + 右侧分区工作区：

| 竞品模块 | 还原要点 | 本项目对应 |
|---|---|---|
| 左侧导航 | 概览 / 任务视图 / 息屏显示 / 其他视图 / 历史记录 / 设置 | ✅ 已建 6 导航（见 1.5） |
| 任务视图·状态聚焦 | 单/多 agent 状态卡，含步骤、用时、模型 | ✅ 布局 focus |
| 任务视图·任务时间线 | 横向时间轴，事件/用时标注 | ✅ 布局 timeline |
| 任务视图·角色陪伴 | 场景插画 + 氛围开关 | ✅ 布局 companion（4 场景） |
| 任务视图·数据看板 | 指标组合 + 用量/统计 | ✅ 布局 dashboard |
| 主题库 | 默认主题 + 自定义主题双 Tab | ✅ 已建（preset/my 双 Tab） |
| 深度编辑器 | 逐页面/组件编辑 | ✅ 设置页页面编排 |
| 息屏表盘库 | 多款息屏表盘卡片 | ✅ 息屏显示 + 表盘卡片 |
| 其他视图 | 提醒/日程/闹钟/倒计时/应用 | ✅ extras 视图 |

### 1.2 功能模块映射表
| 模块 | 文件 | 状态 | 说明 |
|---|---|---|---|
| 概览 Overview | `index.html#view-overview` + `app.js renderOverview` | ✅ 已建 | 设备状态 + 实时会话 + 电脑状态 + 快捷操作四宫格 |
| 任务视图 Tasks | `#view-tasks` + `renderTaskView` | ✅ 已建 | 左：电脑方案预览（小屏镜像）；右：布局方案/主题库 双 Tab |
| 息屏显示 Saver | `#view-saver` + `renderSaverView` | ✅ 已建 | 显示方式 + 原有表盘库 + 表盘细节 + 预览 |
| 其他视图 Extras | `#view-extras` + 表单 | ✅ 已建 | 提醒/日历/闹钟/倒计时/快捷应用/背景图/天气城市 |
| 历史记录 History | `#view-history` + `renderHistory` | ✅ 已建 | localStorage 存保存/同步/主题/升级事件 |
| 设置 Settings | `#view-settings` + `renderSettings` | ✅ 已建 | 页面编排/组件库/模板/属性/主题配色/客户端维护 |
| 主题系统 | `themePresets`(5) + `galleryThemes`(7) + `themeLibrary` | ✅ 已建 | 预设+画廊+自定义，校验对比度后下放 |
| 配色令牌 | `app.css`（旧，911 行） | ❌ 待重写 | 新类无样式，须按三层令牌体系重构 |

### 1.3 新配色方案
- **主方案（建议新增浅色档）**：暖纸白 `#F7F4EC` 背景 + 黛蓝 `#1F3A5F` 主色，替代当前默认深色，降低长时盯屏疲劳（对应优化#19）。
- **深色预设（沿用并校验）**：深蓝科技 / 紫夜 / 苍翠 / 暖阳 / 玫瑰，均已满足卡片↔背景 ≥1.5:1、sub 可读。
- **画廊主题（7 套，程序化绘制）**：极光 / 星空 / 像素花园 / 深海波浪 / 墨韵 / 山峦黄昏 / 熔岩余烬。
- **令牌纪律**：所有色值经 `validate_profile()` 三项对比度校验；CSS 严禁硬编码，走 `--bg-*`/`--text-*`/`--accent`/`--ok|warn|err|info` 语义层。

### 1.4 超出竞品的补充功能（已规划/可实现）
1. **真机高保真同源预览**：预览区 `applyDeviceTheme()` 从 profile 实时下发 `--dev-*`，与设备同源（优化#12）。
2. **硬件遥测卡片**：CPU/内存/磁盘/网络/电池/开机时长，竞品无。
3. **AI 会话实时识别**：含 workbuddy 来源识别（已修 bug），竞品仅 claude/cursor。
4. **SSE 实时推送**：替代 HTTP 轮询，亚秒级刷新（优化#3）。
5. **系统托盘常驻 + 开机自启**：竞品多为纯看板，无。
6. **特权温度/风扇采集**：授权用户可获取（见 SMC 规划），竞品普遍无。

### 1.5 当前实现状态与文件清单
| 文件 | 行数 | 状态 | 说明 |
|---|---|---|---|
| `web_assets/index.html` | 569 | ✅ 已重写 | 语义化宿主页，47 个 JS 引用 id 全部对应 |
| `web_assets/app.js` | 1178 | ✅ 已重写 | 视图路由 + 4 布局 + 主题库 + 息屏 + 历史 + 保存/同步/轮询；已修 3 个致命缺陷（themeLibrary TDZ、数组闭合 `};`→`];`、draft 未加载渲染崩溃） |
| `web_assets/app.css` | 911 | ❌ 待重写 | 旧样式仍在，新导航/布局/主题类无样式 → **当前视觉未达标的主因** |
| `tests/test_style_tokens.py` 等 | — | ✅ 97 用例全绿 | node --check 通过；Playwright 全视图冒烟 pageerror=none |
| 配置存储 | — | ⚠️ localStorage | `workbuddy-taskview`/`saver`/`mythemes`/`history` 存布局，未动 `profile` 协议 |

### 1.6 剩余实施步骤
1. **重写 `app.css`**：语义 tokens、响应式（桌面/窄视口无溢出，用容器查询 `cqw` 而非 `vw`）、深色/浅色三态、组件样式（sidebar/nav/panel/metric/task-card/theme-card/dial-card/history）、加载态与 stale 压暗、`:focus-visible`/`:active`/`:disabled`/`aria-invalid`。
2. **真机验证**：`pio run` 编译固件，烧录 `PANEL_COLOR_SELFTEST=1` 判读 G/B 通道。
3. **重打包**：`dist/workbuddy-agent` 为旧产物，前端改动后需重新打包（spec 已含 web_assets）。
4. **回归**：`cd client && python -m pytest tests/ -q` + Playwright 五档视口冒烟。

---

## 2. 20 条优化建议（结构化归集）

> 分组依据：A 立即提升操作感（前端+少量后端，本机可测）｜B 副屏多窗口（核心可视化）｜C 进度深化（直观看进度）｜D 配置效率（少点击）｜E 体验打磨。
> 状态：⬜ 未开始 ｜ 🟡 部分（UI 骨架已建，待 CSS/后端） ｜ ✅ 已实现。

### A 组 · 立即提升操作感
**#1 Agent 实时仪表盘（多窗格）**
- 功能：Web 端独立「Agent 监控」面板，按来源分栏排列活跃会话卡片。
- 效果：一眼看清各 agent 在做什么，不必切设备屏/终端。
- 要点：复用 `/api/status` 的 `tasks` + `aiwatch.poll_all`；CSS grid 多列；状态色用语义 token。
- 状态：🟡 概览页已有 `ov-task-list`，可升级为多窗格。

**#2 Agent 进度详情抽屉**
- 功能：点击 agent 卡 → 侧滑抽屉展示耗时/工具/项目/最近错误。
- 效果：无需开终端判断卡点。
- 要点：`status_snapshot()` 已返回 `tasks[].tool/state/elapsed/project/err`；新增 drawer 组件。
- 状态：⬜

**#3 实时推送（SSE/WebSocket）替代轮询**
- 功能：主循环 `on_tick` 经 SSE 推 telemetry 到前端。
- 效果：亚秒级刷新、去掉 stale 检测与退避、CPU 更低。
- 要点：`web.py` 加 `/api/stream`（SSE）；保留 `/api/status` 兜底；前端 `EventSource`。
- 状态：⬜（当前为 2.5s 轮询）

**#5 键盘快捷键体系**
- 功能：`R` 刷新、`T` 显示主界面、`1–6` 切设备页、`?` 帮助。
- 效果：常用操作免鼠标。
- 要点：`app.js` 监听 `keydown`；`T` 复用 `TrayController.show_window`，`R` 复用补拉。
- 状态：⬜

**#6 命令面板（Command Palette）**
- 功能：`Ctrl/Cmd+K` 唤起搜索框，输入「同步配置／上传背景／切 AI 页／静音」即执行。
- 效果：深藏功能一键直达。
- 要点：前端 action map + 已有 `/api/*`；纯前端交互。
- 状态：⬜

**#10 完成 / 出错通知**
- 功能：agent 状态 `working→completed/error` 时弹 toast + 托盘图标闪动。
- 效果：不必盯着屏，关键节点主动提醒。
- 要点：前端对比相邻两次 `tasks` 求 diff；托盘 `icon.notify`（`_notify_about` 可复用）。
- 状态：⬜

### B 组 · 副屏多窗口可视化
**#4 置顶迷你悬浮窗（类副屏）**
- 功能：always-on-top 透明小窗，显示 AI 进度条 + 时钟 + 在线状态。
- 效果：像副屏随时瞥一眼。
- 要点：新增 `mini.py` 用 pywebview 开置顶透明窗，走同一 SSE；关闭复用托盘隐藏逻辑。
- 状态：⬜（已有 `tray.py` 常驻基础）

**#11 多预览窗格（真·多窗口可视化）**
- 功能：Web 并排开多个预览窗格，分别绑定不同视图（AI 作战室/系统监控/时钟）。
- 效果：一块大屏同时看多组信息，对标多显示器。
- 要点：先做到「预览窗格×N」虚拟多视图（单设备）；多物理设备需 `SerialLink` 多实例 + 多 profile（后续）。
- 状态：⬜

### C 组 · 进度深化（直观看进度）
**#7 Agent 聚焦模式**
- 功能：双击某 agent 放大为全屏卡，其余淡出；`Esc` 退出。
- 效果：盯长任务不被其他会话干扰。
- 要点：CSS `.focus` class + 前端状态；数据源不变。
- 状态：⬜

**#8 按来源 / 状态过滤**
- 功能：顶部筛选条：全部/claude/codex/zcode/workbuddy，或只看出错/等待我。
- 效果：多 agent 并发不混乱，快定位异常。
- 要点：前端对 `tasks` 做 filter；设备屏仍按优先级取前 4。
- 状态：⬜

**#9 Pin 置顶重要 Agent**
- 功能：右键某 agent 可 pin，永远排最前。
- 效果：关键会话不被新任务挤走。
- 要点：前端 `localStorage` 存 pin 列表，渲染优先排序。
- 状态：⬜

**#13 Agent 活动时间线**
- 功能：每个 agent 一条横向时间线，标注思考/工具调用/等待确认阶段。
- 效果：直观理解瓶颈在「想」还是「等」。
- 要点：扩展 `usage.py` 或新增聚合；`aiwatch` 已有 `_bursts` 记录起始，补阶段事件。
- 状态：⬜（任务视图 timeline 布局为概览版，可深化）

**#14 AI 用量图表**
- 功能：把 `usage.py` 的今日 token + 各工具模型做成折线/占比图。
- 效果：量化效率，看哪款最烧 token。
- 要点：`/api/status` 扩展返回 `usage`；前端 Chart（CSP 已放开本地脚本）。
- 状态：⬜（已有 `ov-usage` 文本）

### D 组 · 配置效率（少点击）
**#15 一键启动 Agent / 应用**
- 功能：设置页「应用」区做成可点击卡片，1 键启动命令。
- 效果：监控到操作闭环，不回终端。
- 要点：`control.launch()` 已具备；前端加按钮调新增 `/api/launch` → `app._launch_app`。
- 状态：⬜（extras 有快捷应用录入，缺一键启动）

**#16 托盘右键增强菜单**
- 功能：右键直接切设备页、暂停/恢复采集、快速静音。
- 效果：不打开窗口也能控，贴合副屏常驻。
- 要点：`tray.py` 的 `Menu` 增项；回调挂 `app` 方法。
- 状态：⬜

**#17 全局搜索 / 跳转**
- 功能：命令面板基础上，搜页面名/组件/提醒并跳到编辑区。
- 效果：配置项多时不迷路。
- 要点：前端索引 `profile` 结构，点击滚动到锚点。
- 状态：⬜

**#18 布局模板一键套用**
- 功能：内置「极简时钟/AI 作战室/系统监控」模板，一键套用。
- 效果：新用户 10 秒配好。
- 要点：内置 template JSON；复用 `save_profile()` + `/api/sync`。
- 状态：⬜（设置页有 `start-templates` 占位）

**#19 高对比 / 护眼主题档**
- 功能：在 `[data-theme]` 基础上加高对比/护眼档。
- 效果：长时盯屏不累，弱光可读。
- 要点：设置页加档位选择，下放 `profile.theme`；校验守卫已能拦过低方案。
- 状态：⬜（见 1.3 暖纸白+黛蓝主方案）

**#20 操作撤销栈 + 变更摘要**
- 功能：配置改动进历史栈，`Cmd/Ctrl+Z` 撤销；诊断包附变更摘要。
- 效果：敢改不怕错，回滚一键完成。
- 要点：前端 history 栈 + 每次保存前 `/api/validate`；`diagnose.build_bundle` 追加 diff。
- 状态：⬜

### E 组 · 体验打磨
**#12 预览与真机高保真同源**
- 功能：预览区滚动同步、点击模拟触摸（发 `TOUCH` 上行）、字号/圆角 1:1。
- 效果：所见即所得，减少翻拍才发现问题。
- 要点：扩展 `applyDeviceTheme()`；模拟触摸复用 `SerialLink.send("TOUCH=...")`。
- 状态：🟡（`applyDeviceTheme` 已建，模拟触摸待加）

---

## 3. 附录 A · 竞品机制分析（GitHub 三项目）

> 上一轮对 EdgeGlow / cursor-light / agent-board 的对比结论摘要，用于指导进度获取架构选型。

| 项目 | 进度获取方式 | 核心特色 |
|---|---|---|
| **EdgeGlow**（Swift/Cocoa + Windows C#） | **Hook 回调 Push**：Agent 的 hook 触发 `curl 127.0.0.1:9876/{start\|pulse\|stop}`，本地 `ControlServer`（NWListener，GET-only，仅 127.0.0.1）驱动；零轮询 | 屏幕边缘四层霓虹流光、5 主题、菜单栏常驻、多显示器、CPU~0%；集成 Claude Code + Hermes |
| **cursor-light**（Tauri/Rust） | **Cursor Hook 直接回调 exe**：hook 命令 `"Cursor Light.exe" --hook --event=… --status=yellow` 切灯色；事件驱动，无轮询 | 角落三色灯（红/黄/绿）、始终置顶+边缘吸附+横竖切换、自动配置 Cursor hooks、Tauri 小体积 exe |
| **agent-board**（Python FastAPI + GSAP） | **前端 5s 轮询 `/api/status`** + 后端 30s 快照；只读扫描 ccusage/`ps aux`/TCC.db/SQLite | Bloomberg 终端风、AGENT TAPE 滚动、集群轨道图、Token ¥ 成本+预算告警、屏幕权限雷达、任务快讯；纯只读 |

### 3.1 对本项目的借鉴
- **补 Hook Push 快通道**（学 EdgeGlow/cursor-light）：在 `~/.workbuddy` 支持 Claude Code 风格 `UserPromptSubmit→start` / `Stop→stop` hook，直接打本地轻量端点，让设备屏在 Agent **开始瞬间**亮（当前轮询 2.5s 有盲区，长回合靠 `sessions/<pid>.json` 心跳补，已做）。
- **预算/Token 模块**（学 agent-board）：`usage.py` 已有今日 token，加 ¥ 成本与超预算告警（对应优化#14）。
- **边缘流光视觉母版**（学 EdgeGlow）：作为优化#4 置顶迷你悬浮窗的视觉参考（已有 `tray.py` + pywebview 透明窗基础）。
- **架构选择**：本项目当前走 agent-board 式轮询（跨 Agent 通用、天然 stale 检测），建议保留轮询兜底 + 增 SSE（#3）+ 可选 Hook Push 快通道，三者互补。

---

## 3.2 Windows 客户端上位机可行性借鉴

> 目标：maozi-diy 后续要推出 **Windows 上位机**。以下基于三竞品技术栈与本项目现状，逐项给出「可借鉴 / 参考实现 / 需适配」结论，并标注 Windows 独有机会与约束。当前上位机核心（Python 后端 + pywebview 本地 Web 设置页 + pystray 托盘）天然跨平台，Windows 侧增量集中在硬件采集与系统交互。

### 3.2.1 跨平台壳与渲染层
| 项 | 竞品参照 | 本项目现状 | Windows 结论 |
|---|---|---|---|
| 渲染壳 | cursor-light 用 Tauri（原生壳+Web 渲染）；agent-board 用浏览器+FastAPI | 已用 pywebview（macOS=Cocoa WebView，Windows=Edge WebView2） | **直接复用**：pywebview 与 Tauri 思路一致，Windows 上渲染是相同的 WebView2，无需换壳 |
| 透明/置顶窗 | EdgeGlow Windows 用 WPF `AllowsTransparency`+`Topmost` | mini.py（规划中）拟用 pywebview 透明窗 | **参考实现**：Windows 用 `frameless=True`+透明，或 pywin32 分层窗口（`WS_EX_LAYERED`） |

注意：Windows 需确保 **Edge WebView2 Runtime** 已装（Win10/11 多自带；缺失时打包需引导安装）。这与 macOS 需 WebView 不同，是 Windows 新增的前置依赖。

### 3.2.2 Hook Push 快通道（OS 无关，一次实现两端复用）
- Claude Code 的 hooks 配置（`settings.json` 的 `UserPromptSubmit` / `Stop`）**跨平台一致**，在 Windows 上同样触发。
- 借鉴 EdgeGlow/cursor-light：在 `web.py` 增加 `/hook/start`、`/hook/stop`、`/hook/pulse` 端点（仅 127.0.0.1，GET/POST），Windows 端 Claude Code 配同一套 hook 即可。
- **结论：直接复用**，无需按 OS 分支；macOS/Windows 共用同一接收端。

### 3.2.3 屏幕边缘流光 / 置顶悬浮窗（EdgeGlow Windows 移植参考）
- EdgeGlow 已做 macOS→Windows 移植，用 C#/WPF 实现全屏透明覆盖层 + 多显示器枚举（`Screen.AllScreens`）+ 边缘霓虹（4 层 `ShapeLayer` + `CIFilter`）。
- 我们的等价实现：Python + pywebview 透明窗，流光用 WebView 内 **CSS `box-shadow` / SVG 滤镜** 绘制（不必复刻原生 ShapeLayer）。多屏包围参考其显示器枚举，用 `screeninfo`（跨平台）取显示器几何。
- **结论：参考其实现模式（全屏透明覆盖 + 多屏包围 + 边缘霓虹），用 Web 技术替换原生绘制**，对应优化#4/#11。

### 3.2.4 系统托盘（已具备 Windows 分支）
- cursor-light Windows 用任务栏驻留 + 右键菜单，与 pystray 的 `Menu` / `MenuItem` 一致。
- 我们 `tray.py` 已是跨平台 pystray，**Windows 走 pywin32、无额外运行时**；仅需把图标换成 `.ico`、确认 pywin32 打包进 exe。
- **结论：直接复用**，逻辑零改动。

### 3.2.5 温度/风扇：Windows 天然可得，规避 T2 难题（重大差异）
- macOS T2 机型 SMC 被拒（`kIOReturnNotPrivileged`），需特权 helper（见 `SMC_PRIVILEGED_PLAN.md` 方案 B）。
- **Windows 无此限制**：普通用户进程即可经 **WMI** 读取：
  - `Win32_TemperatureProbe` / `MSAcpi_ThermalZoneTemperature`（CPU 温度）
  - `Win32_Fan`（风扇转速）
  - 或更全的 OpenHardwareMonitor WMI 命名空间（`root\OpenHardwareMonitor` 的 `Sensor` 类，含 CPU/GPU 温度与风扇）
- **结论：Windows 上位机天然补齐 macOS 缺失的温度/风扇能力，且无需特权 helper**。`collect/windows.py` 新增 WMI 采集，与 `macos.py` 的 SMC 路径并列；`caps` 在 Windows 默认 `cpu_temp:1,fan:1`。macOS 的 SMC 规划仍保留作 macOS 专属路径。

### 3.2.6 Token / 预算模块（agent-board，跨平台）
- agent-board 用 `bunx ccusage daily --json` 聚合 token，Node/bun 跨平台，Windows 同样可用。
- 我们 `usage.py` 已有今日 token，可加 ¥ 成本（需单价/汇率配置）+ 超预算告警（默认 ¥50/天，对应优化#14）。
- **结论：直接复用**，仅新增成本换算与告警 UI。

### 3.2.7 硬件遥测与多显示器
- `psutil` 跨平台：CPU/内存/磁盘/网络通用；电池在 Windows 笔记本有、台式机无（caps 隐藏）。
- 多显示器枚举：EdgeGlow Windows 的多屏包围可借鉴，本端用 `screeninfo`（跨平台）即可，无需原生 API。

### 3.2.8 打包分发与签名
- cursor-light 用 Tauri 打包小体积 exe；我们用 **PyInstaller → exe**（`dist/workbuddy-agent`）。
- Windows 新增约束：**SmartScreen** 对未签名 exe 会拦截（macOS 对应是 TCC/公证）。建议打包时做代码签名或引导用户放行。
- 可借鉴 cursor-light 的「自动配置 hooks + 备份 `.bak`」思路，做 Windows 下 Claude Code hooks 自动注入向导。

### 3.2.9 权限 / 安全新约束
| 平台 | 监控系统/权限 | 本项目应对 |
|---|---|---|
| macOS | TCC（屏幕录制/全盘访问） | `diagnose` 已处理；温度走 SMC 特权 helper |
| Windows | SmartScreen（未签名 exe 拦截）、UAC（安装需提权） | 打包代码签名；安装流程引导用户允许；温度走 WMI 无需提权 |

### 3.2.10 能力 × 竞品来源 × Windows 借鉴方式 汇总
| 能力 | 来源竞品 | Windows 借鉴方式 | 现状/工作量 |
|---|---|---|---|
| 跨平台 Web 壳 | cursor-light / agent-board | 直接复用 pywebview（WebView2） | 已具备，补 WebView2 引导 |
| Hook Push 快通道 | EdgeGlow / cursor-light | 直接复用（OS 无关端点） | web.py 加 3 端点，小 |
| 边缘流光/悬浮窗 | EdgeGlow（WPF 移植） | 参考模式，Web 替换原生绘制 | mini.py 规划中，中 |
| 系统托盘 | cursor-light | 直接复用 pystray | 已具备，换 .ico |
| 温度/风扇 | （本项目补齐） | Windows WMI 天然可得 | windows.py 新增，中（无需特权） |
| Token/预算 | agent-board | 直接复用 ccusage | usage.py 扩展，小 |
| 多显示器包围 | EdgeGlow | 参考枚举，screeninfo | 小 |
| 打包/签名 | cursor-light | PyInstaller + 签名引导 | 中 |

---

## 4. 优先级与里程碑建议

### Phase 1 · 最短路径（可达「可用」）
- 重写 `app.css`（1.6-1）→ 界面达可用质量。
- 优化#3 SSE 推送（替代轮询，提升实时性）。
- 优化#1 仪表盘升级 + #5 快捷键 + #6 命令面板（纯前端）。
- 优化#10 完成/出错通知（前端 diff + 托盘 notify）。
- 验证：pytest 97 + Playwright 五档冒烟。

### Phase 2 · 副屏多窗口
- 优化#4 置顶迷你悬浮窗（`mini.py`）。
- 优化#11 多预览窗格。
- 优化#16 托盘右键增强。

### Phase 3 · 进度深化
- 优化#2 详情抽屉、#7 聚焦、#8 过滤、#9 Pin。
- 优化#13 活动时间线、#14 用量图表。
- 优化#15 一键启动、#17 搜索、#18 模板、#19 护眼档、#20 撤销栈。

### Phase 4 · 真机与打包
- `pio run` 编译 + `PANEL_COLOR_SELFTEST=1` 判读 G/B 通道。
- T2 特权温度/风扇（见 `SMC_PRIVILEGED_PLAN.md` M1→M6）。
- 重打包 `dist/workbuddy-agent`。

### 风险与待决
- 固件未编译 → 设备屏视觉只能代码级保证，需真机定论。
- T2 温度/风扇需用户授权（特权 helper），不授权自动隐藏。
- 改 CSS/文案后必须重跑字体生成与样式守卫测试。

### Phase 5 · Windows 上位机（跨平台扩展）
- 复用 §3.2 结论：Web 设置页与后端逻辑（Python）基本跨平台，Windows 侧增量集中在 `collect/windows.py`（WMI 温度/风扇）、托盘图标 `.ico`、WebView2 引导。
- **关键收益**：Windows 无 T2 限制，WMI 直接拿温度/风扇，**无需 SMC 特权 helper**；`caps` 在 Win 默认 `cpu_temp:1,fan:1`，天然补齐 macOS 缺失能力。
- 复用优先级：先落地 OS 无关项（Hook Push 端点、SSE、仪表盘/快捷键/通知、Token 预算 UI，见 Phase 1–3），再补 Windows 专属（WMI 采集、WebView2 引导、exe 打包+签名）。
- 打包：PyInstaller → exe，附 WebView2 Runtime 检测引导 + 代码签名（规避 SmartScreen）；参考 cursor-light 做 Claude Code hooks 自动注入向导。
- 验证：Windows 虚拟机/实机跑 `pytest` + Playwright；温度/风扇字段真实性用 `workbuddy once` 核验。
