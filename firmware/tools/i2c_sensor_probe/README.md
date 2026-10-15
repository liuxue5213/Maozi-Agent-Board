# I2C 传感器探测程序（AHT20 + BMP280）

独立 PlatformIO 工程，用于**单独验证 ESP32-S3 上 AHT20（温湿度）与 BMP280（气压/温度）的接线、I2C 地址与通信状态，并实时打印读数**。不依赖主固件，验证完成后刷回主固件即可。

## 1. 接线

| 传感器 | SDA | SCL | VCC | GND |
|---|---|---|---|---|
| AHT20 | GPIO2 | GPIO1 | 3.3V | GND |
| BMP280 | GPIO2 | GPIO1 | 3.3V | GND |

- 两个传感器并联在同一组 I2C 上（SDA 接 SDA、SCL 接 SCL）。
- AHT20 固定地址 `0x38`；BMP280 地址 `0x76`（SDO 接 GND）或 `0x77`（SDO 接 VCC），程序两个都试。
- **SDA/SCL 接反也能用**：程序自动尝试 `{SDA=1,SCL=2}` 与 `{SDA=2,SCL=1}` 两种接法，扫到设备就用哪一种。
- 若模块板未带上拉电阻，需在 SDA/SCL 对 3.3V 各接一颗 4.7kΩ 上拉。

## 2. 编译与烧录

```bash
cd firmware/tools/i2c_sensor_probe
pio run -t upload
pio device monitor -b 115200     # 本工程波特率 115200
```

> ⚠️ 烧录本程序会**临时覆盖**开发板上的主固件，仅作探测用。
> 注意：主固件串口是 **921600**，本探测工程是 **115200**，看日志别用错波特率。

## 3. 预期串口输出（验收判据）

启动阶段：

```
================ I2C SENSOR PROBE ================
[I2C] combo 0 (SDA=1, SCL=2  (swapped)): 0 device(s) found
[I2C] combo 1 (SDA=2, SCL=1  (config.h / your wiring)): 2 device(s) found
       0x38  AHT20   (temp + humidity)
       0x76  BMP280  (pressure + temp)
>>> Using combo 1 (SDA=2, SCL=1  ...): 2 device(s)
  AHT20  : OK (0x38)
  BMP280 : OK (0x76/0x77)
=================================================
Live readings start below (every 2.0 s):
```

实时读数阶段（每 2 秒一行）：

```
  Uptime      AHT20 Temp   Humidity    BMP280 Press    Temp
  ----------  ---------    --------    -----------     -----
  00:00:03    24.31 C     48.6 %     1013.2 hPa   24.40 C
```

**通过标准**：两个 combo 至少一个扫到 `0x38` 与 `0x76/0x77`，`AHT20 : OK` 且 `BMP280 : OK`，读数行数值随时间合理变化（温度/湿度/气压非 `--`、非固定值）。

## 4. 排错速查

| 现象 | 可能原因 / 处理 |
|---|---|
| `No I2C device found on either combo` | 供电不足、SDA/SCL 虚焊/松动、缺上拉电阻；用万用表量 SDA/SCL 静态电平应接近 3.3V |
| 只扫到一个设备 | 检查对应模块的供电与 SDA/SCL 是否可靠连接；两传感器务必并联 |
| `BMP280 : NOT FOUND` 但扫描有 0x76/0x77 | BMP280 芯片 ID 异常或坏件，换模块试 |
| 读数长时间不变 | 传感器处于连续转换模式卡住，按复位键；或检查 I2C 线过长/干扰 |
| 串口无输出 | 确认波特率 115200、USB 口与复位时机；部分板子需在烧录后按 EN |

## 5. 验证完成后

刷回主固件（主固件会自动探测接法、采样并显示温湿压，波特率 921600）：

```bash
cd ../.. && pio run -t upload
pio device monitor -b 921600
```

主固件启动会打印 `[SN] AHT20 : OK @0x38`、`[SN] BMP280 : OK @0x76`，并以 1Hz 上行 `TEMP/HUM/PRESSURE/ENVSRC`。
