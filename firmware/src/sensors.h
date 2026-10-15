/**
 * 三传感器集成：VL6180X（测距）+ AHT20/BMP280（温湿压）+ DS1302（RTC）。
 * I2C 传感器共享 Wire1（GPIO1/2）；DS1302 三线（GPIO40/41/42）。
 * AHT20 替代 DHT11 作为主温湿源，DHT11 降为备份。
 */
#pragma once
#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_AHTX0.h>
#include <Adafruit_BMP280.h>
#include <Adafruit_VL6180X.h>
#include <DS1302.h>
#include "config.h"

// ---- 全局环境数据（ui.cpp extern 引用）----
extern float g_temp, g_hum;
extern float g_pressure;   // BMP280 hPa
extern float g_distance;   // VL6180X mm（NAN=不可用）
extern char g_env_source[8];

static Adafruit_AHTX0 aht;
static Adafruit_BMP280 bmp(&Wire1);
static Adafruit_VL6180X tof;
static Ds1302 rtc(PIN_DS1302_CE, PIN_DS1302_CLK, PIN_DS1302_IO);

static bool aht_ok = false, bmp_ok = false, tof_ok = false;
extern char g_i2c_scan[64];   // 诊断：I2C 扫描结果（main.cpp 定义）

// 自恢复：连续读失败达到阈值时重新 begin()（应对 I2C 偶发卡死 / 热插拔 / 掉电）。
// aht_ever_ok / bmp_ever_ok 记录"曾成功通信"，用于抑制从未接线器件的噪声日志。
// bmp_addr 记住 BMP280 命中的地址（0x76/0x77），恢复时复用，避免重新盲猜。
static const uint8_t SENSOR_RECOVER_FAILS = 5;
static int  aht_fail = 0, bmp_fail = 0;
static bool aht_ever_ok = false, bmp_ever_ok = false;
static uint8_t bmp_addr = BMP280_ADDRESS_ALT;

inline void sensors_init() {
    // 自动探测配置的 SDA/SCL 以及反接方向。
    // 哪种扫描到设备就用哪种——不需要用户确认接线方向
    const int COMBOS[2][2] = {{PIN_SSDA, PIN_SSCL}, {PIN_SSCL, PIN_SSDA}};
    char combo_name[2][28];
    snprintf(combo_name[0], sizeof(combo_name[0]), "SDA=%d,SCL=%d", PIN_SSDA, PIN_SSCL);
    snprintf(combo_name[1], sizeof(combo_name[1]), "SDA=%d,SCL=%d", PIN_SSCL, PIN_SSDA);
    int best = -1, best_n = 0;
    char best_addrs[32] = "";

    for (int c = 0; c < 2; ++c) {
        Wire1.begin(COMBOS[c][0], COMBOS[c][1], 100000);
        Wire1.setTimeOut(25);
        delay(50);
        int n = 0;
        char addrs[32] = "";
        char* ap = addrs;
        char* end = addrs + sizeof(addrs);
        for (uint8_t a = 1; a < 127; ++a) {
            Wire1.beginTransmission(a);
            if (Wire1.endTransmission() == 0) {
                ++n;
                if (end - ap > 4)
                    ap += snprintf(ap, (size_t)(end - ap), "%02X ", a);
            }
        }
        *ap = 0;
        Serial.printf("[I2C1] %s → %d devices: %s\n", combo_name[c], n, addrs);
        snprintf(g_i2c_scan, sizeof(g_i2c_scan), "%s:%s", combo_name[c], addrs);
        if (n > best_n) {
            best_n = n; best = c;
            snprintf(best_addrs, sizeof(best_addrs), "%s", addrs);
        }
        Wire1.end();
        delay(10);
    }

    if (best < 0) {
        Serial.println("[I2C1] no devices on either combo");
        snprintf(g_i2c_scan, 64, "none");
        return;
    }
    Serial.printf("[I2C1] using combo %d: %s (%d devices)\n", best, combo_name[best], best_n);
    snprintf(g_i2c_scan, 64, "%s:%s", combo_name[best], best_addrs);
    Wire1.begin(COMBOS[best][0], COMBOS[best][1], 100000);
    Wire1.setTimeOut(25);
    delay(50);

    // 用选定的总线方向初始化传感器
    tof_ok = tof.begin(&Wire1);
    Serial.printf("[SN] VL6180X : %s\n", tof_ok ? "OK @0x29" : "not found");
    aht_ok = aht.begin(&Wire1);
    Serial.printf("[SN] AHT20    : %s\n", aht_ok ? "OK @0x38" : "not found");
    // BMP280 可能挂在 0x76（SDO→GND）或 0x77（SDO→VCC），两个地址都试
    if (bmp.begin(BMP280_ADDRESS_ALT, BMP280_CHIPID)) {
        bmp_ok = true; bmp_addr = BMP280_ADDRESS_ALT;
        Serial.printf("[SN] BMP280   : OK @0x76\n");
    } else if (bmp.begin(BMP280_ADDRESS, BMP280_CHIPID)) {
        bmp_ok = true; bmp_addr = BMP280_ADDRESS;
        Serial.printf("[SN] BMP280   : OK @0x77\n");
    } else {
        bmp_ok = false;
        Serial.printf("[SN] BMP280   : not found\n");
    }
    if (bmp_ok) {
        bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                        Adafruit_BMP280::SAMPLING_X2,
                        Adafruit_BMP280::SAMPLING_X16,
                        Adafruit_BMP280::FILTER_X16,
                        Adafruit_BMP280::STANDBY_MS_63);
    }
}

inline bool sensors_sample_environment() {
    g_temp = NAN; g_hum = NAN;
    snprintf(g_env_source, 8, "NA");
    if (!aht_ok) return false;
    sensors_event_t humidity, temperature;
    aht.getEvent(&humidity, &temperature);
    float t = temperature.temperature, h = humidity.relative_humidity;
    if (isfinite(t) && isfinite(h) && t >= -20 && t <= 80 && h >= 1 && h <= 100) {
        g_temp = t; g_hum = h;
        snprintf(g_env_source, 8, "AHT20");
        aht_ever_ok = true;
        aht_fail = 0;
        return true;
    }
    // 连续读失败达到阈值 → 重新 begin() 自恢复；仅在曾成功通信过的器件上打印，避免从未接线器件的噪声
    if (++aht_fail >= SENSOR_RECOVER_FAILS) {
        aht_fail = 0;
        bool ok = aht.begin(&Wire1);
        if (ok) aht_ever_ok = true;
        if (aht_ever_ok) Serial.printf("[SN] AHT20 re-init: %s\n", ok ? "OK" : "LOST");
    }
    return false;
}

inline void sensors_sample_auxiliary() {
    g_pressure = NAN; g_distance = NAN;
    if (bmp_ok) {
        float t_bmp = bmp.readTemperature();
        float hpa = bmp.readPressure() / 100.0f;
        bool p_valid = isfinite(hpa) && hpa >= 300 && hpa <= 1100;
        bool t_valid = isfinite(t_bmp) && t_bmp >= -20 && t_bmp <= 80;
        if (p_valid) g_pressure = hpa;
        // AHT20 缺失时，用 BMP280 的温度兜底（湿度仍为 NA，屏幕上会标注传感器来源）
        if (!aht_ok && isnan(g_temp) && t_valid) {
            g_temp = t_bmp;
            snprintf(g_env_source, sizeof(g_env_source), "BMP280");
        }
        // 压力或温度任一有效即视为通信正常；连续失败达阈值则重新 begin() 自恢复
        if (p_valid || t_valid) {
            bmp_ever_ok = true;
            bmp_fail = 0;
        } else if (++bmp_fail >= SENSOR_RECOVER_FAILS) {
            bmp_fail = 0;
            bool ok = bmp.begin(bmp_addr, BMP280_CHIPID);
            if (ok) {
                bmp_ever_ok = true;
                bmp.setSampling(Adafruit_BMP280::MODE_NORMAL,
                                Adafruit_BMP280::SAMPLING_X2,
                                Adafruit_BMP280::SAMPLING_X16,
                                Adafruit_BMP280::FILTER_X16,
                                Adafruit_BMP280::STANDBY_MS_63);
            }
            if (bmp_ever_ok) Serial.printf("[SN] BMP280 re-init: %s\n", ok ? "OK" : "LOST");
        }
    }
    if (tof_ok) {
        uint8_t range = tof.readRange();
        if (tof.readRangeStatus() == VL6180X_ERROR_NONE && range <= 200)
            g_distance = range;
    }
}

inline void rtc_get_datetime(int& y, int& m, int& d, int& hh, int& mm, int& ss) {
    Ds1302::DateTime t;
    rtc.getDateTime(&t);
    y = t.year + 2000; m = t.month; d = t.day;
    hh = t.hour; mm = t.minute; ss = t.second;
}
