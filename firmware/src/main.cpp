#include <Arduino.h>
#include "dht11_raw.h"
#include "src/misc/lv_timer.h"
#include "config.h"
#include "display.h"
#include "proto.h"
#include "profile.h"
#include "ui.h"

DHT11Raw dht(PIN_DHT);   // 自包含读取器，不用 noInterrupts()（那是 INT_WDT 崩溃根因）
Proto proto;

// 环境数据（ui.cpp extern 引用）
float g_temp = NAN, g_hum = NAN;

static uint32_t last_uplink = 0, last_dht = 0, last_hb = 0;
static uint32_t last_client_data = 0;
static bool ever_linked = false;

void send_touch(const char* page) {
    Serial.print(uplink_touch(page));
}

void setup() {
    Serial.begin(115200);
    delay(200);
    profile_init();
    display_init();
    ui_init();
    Serial.println("FW=0.3.0,n16r8,schema:1,pages:6");
}

void loop() {
    uint32_t iter_t0 = micros();
    uint32_t now = millis();

    // 1) 解析下行
    while (Serial.available()) proto.feed((char)Serial.read());
    if (proto.profile_changed) {
        proto.profile_changed = false;
        ui_rebuild();
        display_brightness(g_profile.bright);
    }
    if (proto.dirty) {
        proto.dirty = false;
        last_client_data = now;
        ever_linked = true;
        if (proto.t.theme_changed) {
            proto.t.theme_changed = false;
            ui_apply_theme(proto.t.theme_bg, proto.t.theme_card, proto.t.theme_accent,
                           proto.t.theme_text, proto.t.theme_sub, proto.t.theme_grad);
        }
        ui_update(proto.t);
        if (proto.t.bright >= 0) display_brightness(proto.t.bright);
    }

    // 2) DHT11 每 2.5s；DHT11Raw.read() 返回 true 成功，失败安全返回不崩溃
    if (now - last_dht > 2500) {
        last_dht = now;
        g_temp = NAN;
        g_hum = NAN;
        if (dht.read()) {
            g_temp = dht.getTemperature();
            g_hum = dht.getHumidity();
        }
        ui_update(proto.t);
    }

    // 3) 上行环境 1Hz
    if (now - last_uplink > 1000) {
        last_uplink = now;
        Serial.print(uplink_env(g_temp, g_hum));
    }

    // 4) 心跳 5s
    if (now - last_hb > 5000) {
        last_hb = now;
        Serial.print(uplink_hb(now / 1000));
    }

    // 5) 链路在线判定
    ui_set_online(ever_linked && (now - last_client_data < LINK_TIMEOUT_MS));
    ui_theme_tick();

    lv_timer_handler();

    // 慢循环探针：单圈超过 300ms 说明有阻塞，上报便于远程诊断
    uint32_t elapsed = micros() - iter_t0;
    if (elapsed > 300000) Serial.printf("SLOW=%u us\n", (unsigned)elapsed);
    delay(2);
}
