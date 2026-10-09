# WorkBuddy 串口协议 v0.2

上位机（客户端）↔ ESP32-S3 双向通信。传输层：USB 虚拟串口（原生 USB CDC，Espressif VID `0x303A`）。

- 遥测：纯文本键值对，`KEY=VALUE`，`\n` 结尾，UTF-8。
- 普通字段缺省表示本次未提供；AI 任务槽必须显式发送 `AI0=`～`AI3=` 清空，避免显示已结束任务。环境读取失败用 `NA`，不得重发旧测量值。
- 图片/主题资源传输：独立二进制分帧通道（firmware 里程碑实现，见 §4）。

## 1. 下行（PC → ESP32）

| 字段 | 类型 | 说明 |
|---|---|---|
| `HB` | int | 心跳，Unix 秒。≥10s 未收到视为链路断开 |
| `CAPS` | kv | 连接后发一次，能力位图：`CAPS=cpu_temp:0,gpu:1,fan:0,bat:1` |
| `CFG` | kv | 配置推送：`CFG=theme:dark,lang:cn,font:24,bright:80,pages:1,2,3` |
| `CPU` | int | CPU 占用 %（0-100） |
| `CPUFREQ` | int | CPU 频率 MHz（不可用时省略） |
| `CPUTEMP` | float | CPU 温度 ℃（不可用时省略，屏幕显示 N/A） |
| `MEM` | int | 内存占用 % |
| `MEMUSED` / `MEMTOTAL` | float | 已用/总内存 GB |
| `GPU` / `GPUMEM` / `GPUTEMP` | int/int/float | GPU 负载% / 显存% / 温度℃ |
| `DISK` | int | 磁盘占用 % |
| `DISKFREE` | float | 剩余空间 GB |
| `BAT` | int | 电池 %（无电池省略） |
| `NETRX` / `NETTX` | float | 网络下行/上行 Mbps |
| `UPTIME` | int | 系统运行秒数 |
| `TIME` | int | 当前时刻 HHMMSS（如 214953），客户端 1Hz 推送，设备显示时钟 |
| `DATE` | str | 本地日期 `YYYY-MM-DD`（日历/提醒/闹钟按天判断） |
| `VOL` | int | 电脑音量 0-100（3s 节流采样；平台不支持则省略，屏幕显示 --） |
| `MUT` | int | 静音状态 0/1（省略规则同上） |
| `AI0`~`AI3` | kv | 并行 AI 任务列表（按活跃度排序，最多 4 条）：<br>`AI0=zcode,GENERATING,-1,145,maozi-diy` = 工具,状态,进度%,秒数,项目名（项目名仅 ASCII，可空）。`-1` 表示进度未知，屏幕隐藏进度条；空值如 `AI0=` 清空该槽。每帧发送全部四个槽。 |
| `REMIND` | str | 下一条提醒：`REMIND=09:30 站会`（设置页里程碑由用户配置） |
| `AISTATE` | enum | 旧版单任务字段（兼容保留）：`IDLE` `THINKING` `GENERATING` `TOOL` `BUILD` `DONE` `ERROR` |
| `AITOOL` | enum | `claude` `codex` `zcode` |
| `AITASK` | enum | `CODE` `RUN` `RESEARCH` `EXPLORE` `DOC` |
| `AIPROG` | int | 真实进度；当前日志来源仅在 `DONE` 时发 100，其余发 -1（未知） |
| `AITIME` | int | 本次任务已运行秒数 |
| `AIERR` | str | 仅 ERROR 时发送。单行化、截断 48 字符 |

发送节奏：遥测 1Hz；`HB` 5s 一次；`CAPS`/`CFG` 连接时与变更时。

## 2. 上行（ESP32 → PC）

| 字段 | 类型 | 说明 |
|---|---|---|
| `FW` | str | 固件版本号 + 芯片信息，连接后立即发送一次：`FW=0.3.0,n16r8,schema:1,pages:6` |
| `TEMP` | float/NA | DHT11 温度 ℃；读取失败时发 `NA` |
| `HUM` | int/NA | DHT11 湿度 %；读取失败时发 `NA` |
| `TOUCH` | enum | `PAGE1|PAGE2|...`、`LAUNCHER_OPEN`、`CONTROL_OPEN`（页面/面板事件回传记录） |
| `BRIGHT` | int | 亮度档位 0-100（触控调节后回传） |
| `KEY` | enum | 控制中心动作：`VOL+` `VOL-` `MUTE` `PLAY` `NEXT` `PREV`，客户端在电脑上执行 |
| `LAUNCH` | str | 启动器点击的应用 id，客户端按 profile `apps[].command` 打开对应程序 |
| `HB` | int | 设备心跳，Unix 秒 |

交互约定（屏幕竖屏 320×480 使用）：页面**左右滑动**切换；**顶部下拉**打开应用启动器（apps 由 profile 配置，最多 12 个，点击图标客户端执行对应命令）；**底部上滑**打开控制中心（音量±/静音、媒体播放控制、屏幕背光±）。面板各自反方向滑动收起。

## 2.1 版本握手与固件升级（OTA）

- 客户端解析 `FW` 并与随客户端分发的固件版本比对：
  - 主版本一致 → 静默兼容；
  - 不一致 → 托盘提示"固件有新版本，一键升级"。
- 升级走 §4 二进制分帧通道（`TYPE=0x20 OTA_BEGIN / 0x21 OTA_DATA / 0x22 OTA_END`），
  设备写入备用 OTA 分区后回 `ACK=ota_ok` 并重启切换；失败回 `NAK=<reason>`，原固件不受影响。
- 设计原则：**功能变更尽量放客户端**；固件只承载渲染能力，长期保持小版本迭代。

## 2.2 背景图传输（IMG 事务）

与 CFG 同风格的文本分帧（每片 90 原始字节 base64），客户端预缩放为 320×480 JPEG：

```text
IMG=BEGIN,<len>,<CRC32>
IMG=DATA,<seq>,<base64>
IMG=END,bg.jpg
ACK=IMG,APPLIED   /   NAK=IMG,<SIZE|SEQ|DATA|CRC_MISMATCH|STORAGE|DECODE>
IMG=DELETE            → ACK=IMG,DELETED（恢复纯色）
```

设备校验长度与 CRC32 后原子写入 LittleFS /bg.jpg，立即解码到 PSRAM 并整屏重建。
上限 120KB；解码失败回 NAK=DECODE（文件保留但不生效）。

## 3. 链路规则

- 客户端每 3s 扫描串口，识别 VID `0x303A` 或 `usbmodem/CH340` 命名，自动连接、断线自动重连。
- ESP32 端：10s 无下行 `HB` → 判定离线，进入等待重连提示页；期间温湿度走本地记录。
- 客户端：15s 无上行数据 → 标记设备离线（UI 托盘变灰）。

## 4. 资源传输（firmware 里程碑）

主题/背景图下行用二进制分帧，不复用文本通道：

```
0xAA 0x55 | TYPE(1) | SEQ(2) | LEN(2) | PAYLOAD | CRC16(2)
TYPE: 0x01=IMG_BEGIN(name,size) 0x02=IMG_DATA(base64 or raw) 0x03=IMG_END 0x10=FONT_BEGIN...
```

设备校验 CRC 通过后写入 LittleFS，回 `ACK=<name>`。
