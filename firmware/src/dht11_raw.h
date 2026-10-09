/**
 * 自包含 DHT11 读取器（零依赖）。
 * 不使用 noInterrupts()——Adafruit DHT 库关中断触发 INT_WDT 崩溃的根因。
 * 时序用 micros() 超时保护，传感器未接/故障时安全返回 NAN。
 */
#pragma once
#include <Arduino.h>

class DHT11Raw {
    uint8_t _pin;
    uint32_t _last_read = 0;
    float _temp = NAN, _hum = NAN;

    // 等待引脚到达目标电平，超时返回 false（不关中断，micros() 正常走）
    bool wait_level(uint8_t pin, uint8_t level, uint32_t timeout_us) {
        uint32_t start = micros();
        while (digitalRead(pin) != level) {
            if (micros() - start > timeout_us) return false;
        }
        return true;
    }

    // 测量引脚保持当前电平的时长（微秒），超时返回 0
    uint32_t pulse_width(uint8_t pin, uint8_t level, uint32_t timeout_us) {
        uint32_t start = micros();
        while (digitalRead(pin) == level) {
            if (micros() - start > timeout_us) return 0;
        }
        return micros() - start;
    }

public:
    explicit DHT11Raw(uint8_t pin) : _pin(pin) {}

    // 返回 true 成功；内部缓存温度和湿度
    bool read() {
        if (millis() - _last_read < 2000) return !isnan(_temp);  // DHT11 最快 2s 一次
        uint8_t data[5] = {};

        // 1) 主机发起：拉低 18ms 再释放
        pinMode(_pin, OUTPUT);
        digitalWrite(_pin, LOW);
        delay(18);
        digitalWrite(_pin, HIGH);
        pinMode(_pin, INPUT_PULLUP);
        delayMicroseconds(30);

        // 2) 传感器响应：低 80us + 高 80us
        if (!wait_level(_pin, LOW, 60)) return fail();
        if (pulse_width(_pin, LOW, 100) == 0) return fail();
        if (!wait_level(_pin, HIGH, 60)) return fail();
        if (pulse_width(_pin, HIGH, 100) == 0) return fail();

        // 3) 读 40 位：每位以 50us 低脉冲开始，高脉冲时长决定 0/1
        for (uint8_t i = 0; i < 40; ++i) {
            if (!wait_level(_pin, LOW, 80)) return fail();
            uint32_t width = pulse_width(_pin, HIGH, 100);
            if (width == 0) return fail();
            data[i / 8] <<= 1;
            if (width > 40) data[i / 8] |= 1;   // >40us = 1
        }

        // 4) 校验和
        if (data[4] != ((data[0] + data[1] + data[2] + data[3]) & 0xFF)) return fail();

        _hum = data[0] + data[1] * 0.1f;
        _temp = data[2] + (data[3] & 0x0F) * 0.1f;
        if ((data[2] & 0x80)) _temp = -_temp;  // 负温标志
        _last_read = millis();
        return true;
    }

    float getTemperature() const { return _temp; }
    float getHumidity() const { return _hum; }

private:
    bool fail() { _temp = NAN; _hum = NAN; return false; }
};
