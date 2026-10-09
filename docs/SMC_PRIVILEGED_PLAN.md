# 开发方向与需求规划：T2 机型温度/风扇特权采集

> 状态：**规划稿（待确认，未开发）**
> 日期：2026-10-09
> 关联结论：本机 MacBookPro16,1（2019 Intel 16"，带 T2 安全芯片），用户态直读 SMC 一律返回
> `kIOReturnNotPrivileged (0xe00002c7)`（已用 ctypes 探针实测定论）。
> 竞品（iStat Menus / TG Pro / Macs Fan Control）能读，是因为持有 Apple 签发的 SMC
> entitlement 或以 root 特权 helper 身份读取。

---

## 1. 目标一句话

让用户**一次性授权**后，客户端能拿到 CPU 温度与风扇转速并下发小屏显示；
**不授权则维持现状**（`CAPS=cpu_temp:0,fan:0`，设备自动隐藏对应数据位，绝不显示占位假值）。

## 2. 范围与不做的事

| 做 | 不做（本期） |
|---|---|
| macOS T2/Apple Silicon 的温度、风扇**只读**采集 | 风扇**调速**（写 SMC 键风险高，划出范围） |
| 一次性管理员授权安装特权 helper | 向 Apple 申请 SMC entitlement（个人项目大概率不批，不可控） |
| 未授权时的优雅降级（现状已具备） | Windows/Linux 等效功能（psutil 已覆盖，无需特权） |
| helper 的安装/卸载/状态自检 | 自动静默升级 helper（随客户端整体打包更新） |

## 3. 需求清单

### 功能需求

| 编号 | 需求 | 验收标准 |
|---|---|---|
| F1 | 授权引导入口 | 设置页新增「温度与风扇」卡片，说明用途与权限；仅 macOS 显示，非 T2/AS 机型直接显示"无需授权已可读取"或隐藏 |
| F2 | 一键安装 helper | 点「启用（需管理员授权）」→ 弹系统管理员密码框（AuthorizationServices）→ 安装 helper 到 `/Library/PrivilegedHelperTools/` 并注册 launchd → 自动试读一次成功 |
| F3 | 数据接入 | helper 运行期间，遥测新增 `ct`（CPU 温度 ℃）与 `fr`（风扇 RPM）；caps 握手变 `cpu_temp:1,fan:1` |
| F4 | 设备显示 | 小屏「电脑」卡片区域显示温度/风扇（固件侧新增渲染，需 pio 编译验证） |
| F5 | 状态可视化 | 设置页实时显示 helper 状态：未安装 / 已安装运行中 / 异常（附原因）；概览页同步一处小徽标 |
| F6 | 一键卸载 | 点「停用」→ launchd 卸载 + 删除 helper 文件 + caps 回退，设备卡片消失 |
| F7 | 故障降级 | helper 崩溃/被杀/被删：客户端 3 个采样周期内检测到，自动回退 caps=0 并在设置页标红提示，**不显示假值** |

### 非功能需求

| 编号 | 需求 |
|---|---|
| N1 | **最小攻击面**：helper 只暴露"读两个数"一个接口，无 root shell、无任意命令执行；本地 UNIX socket 权限 0600，仅当前用户可连 |
| N2 | **隐私**：数据只在本机（串口下发小屏），不出网、不落盘日志 |
| N3 | **性能**：helper 内部 1s 节流缓存，客户端每采集 tick 一次 IPC 查询（2.5s），杜绝高频 SMC 调用 |
| N4 | **可卸载**：卸载后不残留 launchd plist / socket 文件 / helper 二进制 |
| N5 | **不阻塞主流程**：helper 不可用时采集链路零额外开销（连接失败即回退，超时 ≤200ms） |

## 4. 技术方案对比与推荐

| 方案 | 原理 | 优点 | 缺点 | 结论 |
|---|---|---|---|---|
| A. Apple SMC entitlement | 打包时申请专属 entitlement | 用户体验最好，无安装步骤 | 需付费开发者账号 + Apple 审批，个人项目大概率不批；不可控 | ❌ 放弃 |
| B. **launchd 特权 helper + 本地 socket（推荐）** | 编译一个 ~200 行的 C 小工具，经管理员授权装入 `/Library/PrivilegedHelperTools/`，由 launchd 按需拉起，客户端经 UNIX domain socket 读 JSON | 不依赖 Apple 审批；代码量小、可审计；标准 macOS 分发模式（Macs Fan Control 同类做法）；卸载干净 | 需要一次管理员授权；需编译 C（xcode CLI 即可） | ✅ **推荐** |
| C. sudoers NOPASSWD 白名单命令 | 配置 `sudo` 免密执行一个只读探测命令 | 实现最快 | 修改用户 sudoers 观感差、易被安全软件标记；授权粒度糙 | ❌ 备选不推荐 |
| D. SMJobBless / SMAppService | Apple 官方特权 helper 安装框架 | 流程最正规 | SMJobBless 已废弃；SMAppService 需 macOS 13+ 且要求签名与 Info.plist 契合，工程量大于收益 | ❌ 本期不用（可作为 B 的后续演进） |

**推荐架构（方案 B）：**

```
┌─ 用户态客户端 (python, collect/macos.py) ─────────┐
│  SMCBridge: 连接 /var/run/maozi-smc.sock          │
│  成功 → t["ct"]=57.2, t["fr"]=2662, caps 置 1      │
│  失败/超时 → 维持 caps 0（现状）                    │
└──────────────┬────────────────────────────────────┘
               │ UNIX socket (0600, JSON 行协议)
┌──────────────▼────────────────────────────────────┐
│  maozi-smcd (C, ~200 行, root, launchd 按需拉起)   │
│  IOKit AppleSMC → TC0P/Tp09(温) F0Ac(风扇)         │
│  1s 内部缓存；只应答 {"cmd":"read"} 一种请求        │
└───────────────────────────────────────────────────┘
```

- socket 路径：`/var/run/maozi-smc.sock`
- 协议：请求 `{"cmd":"read"}\n`；应答 `{"ok":1,"temp_c":57.25,"fan_rpm":2662}\n` 或 `{"ok":0,"err":"..."}`（单行 JSON，防粘包最简）
- 安装动作：拷贝二进制 + 写 `/Library/LaunchDaemons/com.maozi.smc.plist`（含 `Bonjour`/socket 激活或 `RunAtLoad`）+ `launchctl kickstart`
- 温度键策略：优先 `TC0P`（CPU 近似温度），读不到再试 `Tp09`/`Tp01` 等，多键取首个成功值（在 helper 内固化，客户端不感知）

## 5. 授权与状态机（UX）

```
[未安装] ──点「启用」──▶ 管理员密码框 ──成功──▶ [运行中]（caps=1，屏幕显示）
   ▲                        │失败/取消              │
   └────── 点「停用」 ◀──────┴──── helper 异常 ──▶ [异常]（caps=0，设置页标红）
```

- 授权目的文案（写进设置页，需明确）：「安装一个只读的系统组件，用于读取 CPU 温度与风扇转速。数据仅发送到你自己的小屏，不会联网。」
- 未授权时概览/设置页显示：「温度与风扇：未启用（小屏隐藏该项）」，**不是错误态**，避免用户误以为故障。

## 6. 里程碑（M1 是可行性闸门，必须最先做）

| 里程碑 | 内容 | 验证方式 | 备注 |
|---|---|---|---|
| **M1 可行性闸门** | 用 `sudo` 手动跑一次 SMC 探针，确认 root 下 TC0P/F0Ac 可读 | `sudo python3 probe_smc.py` 输出温度/转速 | **若 root 也读不到（系统进一步收紧），整个项目终止，不产生任何后续工作量** |
| M2 helper 原型 | maozi-smcd.c + 手动编译 + 手动 launchd 加载 | 命令行 socket 客户端读到 JSON | 纯手工，不进打包 |
| M3 安装/卸载 | AuthorizationServices 授权 + 安装脚本 + 卸载 | 全新流程一遍装好、一遍卸净 | |
| M4 客户端接入 | collect/macos.py SMCBridge + caps 联动 + 设置页/概览卡片 + 单测（mock socket） | pytest 全绿 + 真机 caps 翻转 | |
| M5 固件显示 | 遥测 `ct/fr` 解析 + 电脑卡片区渲染 | **需 pio 编译 + 实机烧录**（沿用既有约束：本环境不编译固件） | |
| M6 打包 | helper 二进制随 dist 打包 + 安装脚本 | dist 产物在第二台机器可复装 | 打包链路目前本就待重做，顺势合并 |

## 7. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| root 下 SMC 仍被拒（macOS 进一步收紧） | 高 | M1 闸门先行，5 分钟出结论，沉没成本最小 |
| helper 被安全软件误报 | 中 | 代码极小可审计；不混淆不加壳；文档说明用途 |
| 固件侧无编译环境 | 既有 | M5 沿用「静态核验 + 待实机」约束，与现状一致 |
| 用户拒绝授权 | 预期内 | 未授权=现状，产品不残缺 |

## 8. 待你确认的决策点

1. **方案 B（launchd helper + 本地 socket）是否认可？** 认可则按 M1→M6 推进。
2. **M1 探针需要一次 sudo**：届时我会给出一条可直接复制的探针命令，由你在终端执行（AI 沙箱内不持有 sudo）。
3. 温度/风扇在小屏上的呈现位置：**电脑页顶部两枚小徽标**（推荐，改动最小）还是并入「环境」卡？默认取前者。
4. 命名：socket/daemon 用 `com.maozi.smc` 还是 `com.workbuddy.smc`？默认前者（与仓库名一致）。

---

### 附：现状能力备忘（已实测定论，勿重复排查）

- 磁盘 93%、磁盘可用/总量（GiB 口径）、网速（Mbps）均已可采；仅温度/风扇缺。
- `CAPS=cpu_temp:0,fan:0` 能力握手已让设备自动隐藏对应数据位——未授权场景体验是完好的。
- 详见 `.workbuddy/memory/MEMORY.md` 中「遥测能力核验」条目。
