#include <Arduino.h>
#include <Preferences.h>
#include "dht11_raw.h"
#include "src/misc/lv_timer.h"
#include "config.h"
#include "display.h"
#include "proto.h"
#include "profile.h"
#include "bgimg.h"
#include "ui.h"

DHT11Raw dht(PIN_DHT);   // 自包含读取器，不用 noInterrupts()（那是 INT_WDT 崩溃根因）
Proto proto;
Preferences prefs;

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
    bgimg_init();        // 挂载确认 + 解码已存背景图（在 ui_init 前完成，首屏即带背景）
    display_init();
    ui_init();
}

void send_fw_handshake() {
    // 真实能力握手（PROTOCOL §5）：客户端据此拒绝不兼容的配置推送。
    // 开机 + 每次"新客户端接入"（链路超时后再次收到数据）都会重发。
    Serial.printf("FW=0.3.1,n16r8,schema:1,pages:%u,widgets:7,apps:12,reminders:20,calendar:50,alarms:16\n",
                  (unsigned)(sizeof(((DeviceProfile*)0)->pages) / sizeof(((DeviceProfile*)0)->pages[0])));
}

void loop() {
    uint32_t iter_t0 = micros();
    uint32_t now = millis();

    // 1) 解析下行
    if (Serial.available() > 0) {
        // 任何下行字节都证明客户端在线（HB/CAPS 等行在 handle 中提前返回，
        // 不能依赖 dirty 标志判断在线）。
        last_client_data = now;
        ever_linked = true;
    }
    while (Serial.available()) proto.feed((char)Serial.read());
    static bool fw_pending = true;   // 开机发一次；链路超时后复位，等待新客户端
    if (fw_pending && ever_linked) {
        send_fw_handshake();
        fw_pending = false;
    }
    if (ever_linked && now - last_client_data > LINK_TIMEOUT_MS) {
        ever_linked = false;         // 链路断开：复位握手，下个客户端接入时重发
        fw_pending = true;
    }
    if (proto.profile_changed) {
        proto.profile_changed = false;
        ui_rebuild();
        display_brightness(g_profile.bright);
    }
    if (proto.bg_changed) {
        proto.bg_changed = false;
        ui_rebuild();
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
        if (proto.t.idledim_changed) {
            proto.t.idledim_changed = false;
            ui_set_idle_dim(proto.t.idledim);
            if (prefs.begin("wb", false)) {
                prefs.putInt("idledim", proto.t.idledim);
                prefs.end();
            }
        }
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
