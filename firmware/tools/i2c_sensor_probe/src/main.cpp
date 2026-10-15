/**
 * I2C 传感器探测程序（独立工程，用于验证接线与读数）
 * ------------------------------------------------------------------
 * 目标传感器：
 *   - AHT20  : 温度 + 湿度      默认 I2C 地址 0x38
 *   - BMP280 : 气压 + 温度      默认 I2C 地址 0x76（SDO 接地） / 0x77（SDO 接 VCC）
 *
 * 接线说明：
 *   你本次实际接线：SCL = GPIO1, SDA = GPIO2（即 SDA=GPIO2, SCL=GPIO1）。
 *   主固件 firmware/src/config.h 现已对齐：PIN_SSDA=2, PIN_SSCL=1（一致）。
 *   本程序仍会自动尝试两种接法，写反也能识别，彻底消除 SCL/SDA 歧义。
 *
 *   本程序不依赖主固件，也不假定哪个脚是 SDA/SCL：
 *   它会自动尝试两种接法（{SDA=1,SCL=2} 与 {SDA=2,SCL=1}），
 *   扫到设备即用哪一种，并在串口打印最终生效的接法。
 *   这能彻底消除“SCL/SDA 到底谁接几号脚”的歧义。
 *
 * 用途：
 *   1) 烧录本程序 → 打开串口监视器（115200），查看 I2C 扫描结果与设备地址；
 *   2) 若两传感器均识别，程序每 2 秒打印一次温/湿/压实时读数；
 *   3) 验证完成后，重新 `pio run -t upload` 刷回主固件即可。
 *
 * 注意：烧录本程序会临时覆盖开发板上的主固件，仅作探测用。
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <Adafruit_BMP280.h>

// 传感器挂载在 Wire1（与主固件 sensors.h 保持一致）
static Adafruit_AHTX0 aht;
static Adafruit_BMP280 bmp(&Wire1);

// 常见器件地址
static const uint8_t ADDR_AHT20 = 0x38;
static const uint8_t ADDR_BMP280_ALT = 0x76;  // SDO -> GND
static const uint8_t ADDR_BMP280     = 0x77;  // SDO -> VCC

// 两种待探测的引脚接法：{SDA, SCL}（与主固件 sensors.h 一致，自动选用扫到设备的那组）
// combo1 与 config.h 首选一致：SDA=2, SCL=1（你的实测接线）
static const int PIN_COMBO[2][2] = {{1, 2}, {2, 1}};
static const char* COMBO_DESC[2] = {
    "SDA=1, SCL=2  (swapped)",
    "SDA=2, SCL=1  (config.h / your wiring)"
};

static int g_best_combo = -1;       // 最终选用的引脚接法
static bool g_aht_ok = false;
static bool g_bmp_ok = false;

// 在指定引脚组合上扫描总线，返回找到的设备数，并把地址写入 out[]
static int scan_bus(int sda, int scl, uint8_t* out, int max_out) {
    Wire1.begin(sda, scl, 100000);
    Wire1.setTimeOut(25);
    delay(50);
    int n = 0;
    for (uint8_t a = 1; a < 127; ++a) {
        Wire1.beginTransmission(a);
        uint8_t rc = Wire1.endTransmission();
        if (rc == 0) {
            if (n < max_out) out[n] = a;
            ++n;
        }
        // 忽略其他返回码（NACK / 超时），继续下一个地址
    }
    return n;
}

// 把地址翻译成可读的器件名
static const char* describe_addr(uint8_t a) {
    if (a == ADDR_AHT20)        return "AHT20   (temp + humidity)";
    if (a == ADDR_BMP280_ALT || a == ADDR_BMP280) return "BMP280  (pressure + temp)";
    return "unknown / other";
}

void setup() {
    Serial.begin(115200);
    while (!Serial && millis() < 2000) { delay(10); }
    Serial.println();
    Serial.println("================ I2C SENSOR PROBE ================");
    Serial.println("Probing both pin combinations to resolve SDA/SCL...");
    Serial.println("-------------------------------------------------");

    uint8_t addrs[32];
    int best_n = 0;

    for (int c = 0; c < 2; ++c) {
        int sda = PIN_COMBO[c][0];
        int scl = PIN_COMBO[c][1];
        int n = scan_bus(sda, scl, addrs, 32);
        Serial.printf("[I2C] combo %d (%s): %d device(s) found\n",
                      c, COMBO_DESC[c], n);
        for (int i = 0; i < n; ++i) {
            Serial.printf("       0x%02X  %s\n", addrs[i], describe_addr(addrs[i]));
        }
        if (n > best_n) {
            best_n = n;
            g_best_combo = c;
        }
        Wire1.end();
        delay(10);
    }

    Serial.println("-------------------------------------------------");
    if (g_best_combo < 0) {
        Serial.println("[FAIL] No I2C device found on either combo.");
        Serial.println("       Check wiring / pull-ups / power, then reset.");
        return;
    }

    int sda = PIN_COMBO[g_best_combo][0];
    int scl = PIN_COMBO[g_best_combo][1];
    Serial.printf(">>> Using combo %d (%s): %d device(s)\n",
                  g_best_combo, COMBO_DESC[g_best_combo], best_n);
    Wire1.begin(sda, scl, 100000);
    Wire1.setTimeOut(25);
    delay(50);

    // 初始化传感器（基于选中总线的引脚方向）
    g_aht_ok = aht.begin(&Wire1);
    g_bmp_ok = bmp.begin(ADDR_BMP280_ALT, BMP280_CHIPID) ||
               bmp.begin(ADDR_BMP280,     BMP280_CHIPID);
    if (g_bmp_ok) {
        bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                        Adafruit_BMP280::SAMPLING_X2,
                        Adafruit_BMP280::SAMPLING_X16,
                        Adafruit_BMP280::FILTER_X16,
                        Adafruit_BMP280::STANDBY_MS_63);
    }

    Serial.println("-------------------------------------------------");
    Serial.printf("  AHT20  : %s\n", g_aht_ok ? "OK (0x38)"   : "NOT FOUND");
    Serial.printf("  BMP280 : %s\n", g_bmp_ok ? "OK (0x76/0x77)" : "NOT FOUND");
    Serial.println("=================================================");

    if (!g_aht_ok && !g_bmp_ok) {
        Serial.println("[FAIL] Neither sensor responded. Verify address / wiring.");
        return;
    }
    Serial.println("Live readings start below (every 2.0 s):");
}

void loop() {
    if (g_best_combo < 0 || (!g_aht_ok && !g_bmp_ok)) {
        delay(2000);
        return;
    }

    // 表头（每 20 行重打一次，便于长时间观察）
    static unsigned long count = 0;
    if (count % 20 == 0) {
        Serial.println();
        Serial.println("  Uptime      AHT20 Temp   Humidity    BMP280 Press    Temp");
        Serial.println("  ----------  ---------    --------    -----------     -----");
    }
    ++count;

    // 读取 AHT20
    float t_aht = NAN, h_aht = NAN;
    if (g_aht_ok) {
        sensors_event_t humidity, temperature;
        aht.getEvent(&humidity, &temperature);
        t_aht = temperature.temperature;
        h_aht = humidity.relative_humidity;
        if (!isfinite(t_aht) || !isfinite(h_aht)) { t_aht = NAN; h_aht = NAN; }
    }

    // 读取 BMP280
    float p_bmp = NAN, t_bmp = NAN;
    if (g_bmp_ok) {
        p_bmp = bmp.readPressure() / 100.0f;   // Pa -> hPa
        t_bmp = bmp.readTemperature();
        if (!isfinite(p_bmp) || p_bmp < 300 || p_bmp > 1100) p_bmp = NAN;
        if (!isfinite(t_bmp)) t_bmp = NAN;
    }

    char buf[16];
    // 开机计时（ESP32 未配 NTP 时 wall-clock 为 1970，故用 uptime 更直观）
    unsigned long tot = millis() / 1000UL;
    snprintf(buf, sizeof(buf), "%02u:%02u:%02u",
             (unsigned)(tot / 3600), (unsigned)((tot / 60) % 60), (unsigned)(tot % 60));

    // 每个字段 snprintf 成定宽字符串（注意：不能直接用 String 指针相加拼接）
    char s_temp[16], s_hum[16], s_press[16], s_btemp[16];
    if (g_aht_ok) {
        snprintf(s_temp,  sizeof(s_temp),  "%7.2f C", t_aht);
        snprintf(s_hum,   sizeof(s_hum),   "%7.1f %%", h_aht);
    } else {
        snprintf(s_temp, sizeof(s_temp), "  -- C");
        snprintf(s_hum,  sizeof(s_hum),  "  -- %");
    }
    if (g_bmp_ok) {
        snprintf(s_press, sizeof(s_press), "%10.1f hPa", p_bmp);
        snprintf(s_btemp, sizeof(s_btemp), "%7.2f C", t_bmp);
    } else {
        snprintf(s_press, sizeof(s_press), "  -- hPa");
        snprintf(s_btemp, sizeof(s_btemp), "  -- C");
    }

    Serial.printf("  %s  %s  %s   %s  %s\n",
                  buf, s_temp, s_hum, s_press, s_btemp);

    delay(2000);
}
