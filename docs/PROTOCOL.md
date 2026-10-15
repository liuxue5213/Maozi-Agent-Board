# WorkBuddy 串口协议 v0.2

上位机（客户端）↔ ESP32-S3 双向通信。传输层：USB 虚拟串口（原生 USB CDC，Espressif VID `0x303A`）。

- 遥测：纯文本键值对，`KEY=VALUE`，`\n` 结尾，UTF-8。
- 普通字段缺省表示本次未提供；AI 任务槽必须显式发送 `AI0=`～`AI7=` 清空，避免显示已结束任务。焦点用量字段通过空值清除，环境读取失败用 `NA`。
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
| `VOL` | int | 电脑音量 0-100（约 1s 采样；设备音量键后立即补采；平台不支持则省略，屏幕显示 --） |
| `MUT` | int | 静音状态 0/1（省略规则同上） |
| `NP` | csv/空 | 当前媒体会话：`歌名,歌手,位置秒,时长秒,播放中(0/1)`；歌名最多 48 字节，歌手最多 24 字节；后台约 1s 采样、发送前补算进度，固件在新帧之间按本地时钟推进；`NP=` 清除已结束曲目。|
| `AI0`~`AI7` | csv | 并行 AI 会话，最多 8 条：`工具,状态,进度%,用时秒,项目名,会话短 ID,距更新秒`。进度 `-1` 表示未知；每帧发送全部八个槽，空值清位。|
| `AIU` | csv/空 | 焦点工具、模型、用时；`AIU=` 清除旧模型。|
| `AIT` / `AITOK` | int | 今日累计；`AIT` 为千 Token 整数，供旧固件兼容；`AITOK` 为精确 Token 数，当前固件优先使用。|
| `AIOUT` / `AITOTAL` | int/空 | 焦点会话本地已扫描到的累计输出与总 Token（重启前历史不保证补齐）；空值清除旧会话数据。|
| `AITITLE` / `AFM` | UTF-8/空 | 焦点任务摘要、最近消息（最多 60 字节，保证 UTF-8 边界）；空值清除。|
| `REMIND` | str | 下一条提醒：`REMIND=09:30 站会`（设置页里程碑由用户配置） |

发送节奏：遥测 1Hz；`HB` 5s 一次；`CAPS`/`CFG` 连接时与变更时。

## 2. 上行（ESP32 → PC）

| 字段 | 类型 | 说明 |
|---|---|---|
| `FW` | str | 固件版本号 + 芯片信息，连接后立即发送一次；当前页数能力由 `pages:10` 宣告，客户端须按实际握手值校验。|
| `TEMP` | float/NA | AHT20 温度 ℃，失败时回退 DHT11；不可用发 `NA` |
| `HUM` | int/NA | AHT20 湿度 %，失败时回退 DHT11；不可用发 `NA` |
| `ENVSRC` | enum | 当前温湿度来源：`AHT20`、`DHT11` 或 `NA` |
| `PRESSURE` | float/NA | BMP280 气压 hPa；未检测到或读取失败发 `NA` |
| `DISTANCE` | float/NA | VL6180X 距离 mm；未检测到或读取失败发 `NA` |
| `SN` | str | 启动时 I²C 扫描诊断，发送三次；`none` 表示未检测到设备 |
| `[PETPERF]` | 诊断行 | `frame=… load_ms=… psram=…`；客户端在状态接口显示最近一次宠物帧加载耗时和剩余 PSRAM |
| `TOUCH` | enum | `PAGE1|PAGE2|...`（页面切换事件回传记录） |
| `BRIGHT` | int | 亮度档位 0-100（触控调节后回传） |
| `KEY` | enum | 控制中心动作：`VOL+` `VOL-` `MUTE` `PLAY` `NEXT` `PREV`，客户端在电脑上执行 |
| `LAUNCH` | str | 启动器点击的应用 id，客户端按 profile `apps[].command` 打开对应程序 |
| `HB` | int | 设备心跳，Unix 秒 |

交互约定（屏幕竖屏 320×480 使用）：页面**左右滑动**切换；`control` 是独占一页的音乐与控制组件，页面内纵向滚动查看音量、背光和最多 12 个快捷应用。触摸媒体按钮由 `KEY` 上行，快捷应用由 `LAUNCH` 上行，执行结果反映在后续遥测中。

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
IMG=END,<文件名>
ACK=IMG,<文件名>,<字节数>（宠物/相册）或 ACK=IMG,APPLIED（背景）
NAK=IMG,<SIZE|SEQ|DATA|CRC_MISMATCH|STORAGE|DECODE>
IMG=DELETE             → ACK=IMG,DELETED（恢复纯色）
IMG=DELETE,<文件名>    → ACK=IMG,DELETED,<文件名>（已不存在也算成功）
                       或 NAK=IMG,DELETE_FAIL,<文件名>
```

文件名可为 `bg.jpg`、`sv1.jpg` 至 `sv8.jpg`、`pet_<状态>.jpg`。
设备校验长度与 CRC32 后写入 LittleFS；背景立即解码，宠物帧在显示时解码。
单张上限 120KB。整包角色图为 9 张基础帧，可增加 `feeding`、`drinking`、
`playing` 三张触摸反馈帧，以及 `idle_b`、`thinking_b`、`sleeping_b`、
`greeting_b`、`happy_b`、`feeding_b`、`drinking_b`、`playing_b` 动作帧。
固件保留 A 帧的整屏场景，
只对宠物区域做多步过渡：待机短促眨眼、睡眠缓慢呼吸、互动连续动作；
旧包缺少 B 帧时仍显示 A 帧。
客户端在每张 IMG 事务期间暂停遥测与配置下发，等待设备 ACK 后再发送下一张。

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
