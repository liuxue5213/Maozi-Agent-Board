# WorkBuddy ESP32-S3 固件

4寸 ST7796 电容触摸屏（480×320）+ DHT11 状态屏固件。PlatformIO 工程。

组件插槽渲染器、日历和闹钟的下一里程碑设计见 [NEXT_MILESTONE.md](../docs/NEXT_MILESTONE.md)；该功能尚未实现。

## 硬件

- ESP32-S3 N16R8（16MB flash / 8MB PSRAM）
- ST7796 SPI 屏 + FT6236 触摸（引脚见 `src/config.h`，与需求文档引脚表一致）
- DHT11 数据脚 GPIO4

## 构建 & 烧录

```bash
pip3 install platformio
python3 -m platformio run -d .                 # 编译
python3 -m platformio run -d . -t upload --upload-port /dev/cu.usbserial-XXXX   # 烧录
python3 -m platformio device-monitor -p /dev/cu.usbserial-XXXX                  # 串口日志
```

macOS 下 FT232/CH340 串口形如 `/dev/cu.usbserial-*`；原生 USB 口形如 `/dev/cu.usbmodem*`。
烧录时若无法自动复位：按住 BOOT 再按一下 RST，进入下载模式后重试。

## 功能

- **页面1 AI**：任务状态大字（Idle/Thinking/Generating/Working/Done/Error）+ 任务类型/耗时 + 温湿度角标；仅有真实进度时显示进度条
- **页面2 硬件**：CPU / 内存 / GPU / 磁盘四卡片（数据缺失自动 N/A）
- **页面3 环境**：温湿度大字、链路在线状态、亮度、系统运行时长
- **手势**：左右滑动切换页面（LVGL gesture）
- **串口**：115200，协议见 `docs/PROTOCOL.md`；收到 `CFG=bright:N` 实时调背光
- **上行**：TEMP/HUM 1Hz（DHT11 每 2.5s 采样）、心跳 5s、切页 TOUCH 事件

## 中文字库

使用 [Noto Sans CJK 简体](https://github.com/notofonts/noto-cjk) 的字形，许可证见 [OFL-NotoSansCJK.txt](src/fonts/OFL-NotoSansCJK.txt)。16/24 像素字库覆盖 GB2312 的 6763 个汉字；32/48 像素覆盖界面文案，并在缺字时回退至 24 像素。新增界面文案后运行 `./tools/gen_fonts.sh` 重新生成。非常用字、扩展汉字和表情仍可能缺字。

## 待办（后续里程碑）

- [ ] 主题/背景图传输（二进制分帧通道）
- [ ] PIR 无人熄屏（GPIO7，引脚已留）、4 路 LED（GPIO1/2/3/5 已留）
- [ ] 断连离线温湿度记录（NVS/LittleFS）
