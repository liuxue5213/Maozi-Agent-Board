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
static bool bright_auto = false;   // true=按时间段自动调亮度
static uint8_t bright_manual_val = 90;

void send_touch(const char* page) {
    Serial.print(uplink_touch(page));
}

void setup() {
    // OTA 在接收每个 512B 分片后会同步写 flash；擦写抖动期间仍有 UART
    // 数据到达。2KB 在持续 921600 波特下会溢出，8KB 可覆盖多个分片。
    Serial.setRxBufferSize(8192);
    // 921600：链路是 FT232R 桥（VID 0x0403），115200 时整帧实拍要 ~10s 且
    // 76KB 的背景图下发同样被带宽卡死。FT232R 与 ESP32 UART 都原生支持
    // 921600，客户端 serial_link.py 同步改；烧录走 ROM 下载模式不受影响。
    Serial.begin(921600);
    delay(200);
    profile_init();
    bgimg_init();        // 挂载确认 + 解码已存背景图（在 ui_init 前完成，首屏即带背景）
    display_init();
    ui_init();
}

void send_fw_handshake() {
    // 真实能力握手（PROTOCOL §5）：客户端据此拒绝不兼容的配置推送。
    // 开机 + 每次"新客户端接入"（链路超时后再次收到数据）都会重发。
    Serial.printf("FW=0.3.2,n16r8,schema:1,pages:%u,widgets:10,apps:12,reminders:20,calendar:50,alarms:16\n",
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
    bool rebuilt_this_pass = false;
    if (proto.profile_changed) {
        proto.profile_changed = false;
        ui_rebuild();
        display_brightness(g_profile.bright);
        rebuilt_this_pass = true;
    }
    if (proto.bg_changed) {
        proto.bg_changed = false;
        Serial.printf("[BG] bg_changed -> rebuild\n");
        ui_rebuild();
        rebuilt_this_pass = true;
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
        if (proto.t.svint_changed) {
            proto.t.svint_changed = false;
            ui_set_saver_interval(proto.t.svint);
            if (prefs.begin("wb", false)) { prefs.putUShort("svint", proto.t.svint); prefs.end(); }
        }
        if (proto.t.svfx_changed) {
            proto.t.svfx_changed = false;
            ui_set_saver_fx(proto.t.svfx);
            if (prefs.begin("wb", false)) { prefs.putUChar("svfx", proto.t.svfx); prefs.end(); }
        }
        if (proto.t.pint_changed) {
            proto.t.pint_changed = false;
            ui_set_poem_interval(proto.t.pint);
            if (prefs.begin("wb", false)) { prefs.putUChar("pint", proto.t.pint); prefs.end(); }
        }
        if (proto.t.saver_changed) {
            proto.t.saver_changed = false;
            ui_set_saver((uint8_t)proto.t.saver);
            if (prefs.begin("wb", false)) {
                prefs.putInt("saver_style", proto.t.saver);
                prefs.end();
            }
        }
        if (proto.t.savermsg_changed) {
            proto.t.savermsg_changed = false;
            ui_set_saver_msg(proto.t.savermsg);
            if (prefs.begin("wb", false)) {
                prefs.putString("saver_msg", proto.t.savermsg);
                prefs.end();
            }
        }
        if (proto.t.topbar_changed) {
            proto.t.topbar_changed = false;
            ui_set_topbar(proto.t.top_clock_cfg != 0, proto.t.top_wx_cfg != 0);
            if (prefs.begin("wb", false)) {
                prefs.putBool("top_clock", proto.t.top_clock_cfg != 0);
                prefs.putBool("top_wx", proto.t.top_wx_cfg != 0);
                prefs.end();
            }
        }
        if (proto.t.cardopa_changed) {
            proto.t.cardopa_changed = false;
            ui_set_card_opa((uint8_t)proto.t.cardopa_cfg);
            if (prefs.begin("wb", false)) {
                prefs.putUChar("card_opa", (uint8_t)proto.t.cardopa_cfg);
                prefs.end();
            }
        }
        if (proto.t.page_req_changed) {
            proto.t.page_req_changed = false;
            // PAGE 为 1 基；ui_set_page 自带越界保护
            if (proto.t.page_req > 0) ui_set_page((uint8_t)(proto.t.page_req - 1));
        }
        if (proto.t.inbox_changed) {
            proto.t.inbox_changed = false;
            ui_set_inbox(proto.t.inbox);
        }
        if (proto.t.snap_req_changed && !rebuilt_this_pass) {
            // 实拍：阻塞约 10s（串口回传），期间主循环停顿属预期。
            // ⚠️ 重建当拍必须推迟：ui_rebuild 删建控件后同一拍立即
            // lv_snapshot 曾触发 Core1 LoadProhibited panic（真机复现，
            // 表现为「配置应用后立刻实拍→设备静默重启」）。保留标志位，
            // 下一拍再拍。
            proto.t.snap_req_changed = false;
            ui_send_snapshot();
        }
        if (proto.t.pet_action_changed) {
            proto.t.pet_action_changed = false;
            pet_interact_event(proto.t.pet_action);
        }
        if (proto.t.bright >= 0) {
            bright_manual_val = proto.t.bright;
            display_brightness(proto.t.bright);
            if (prefs.begin("wb", false)) {
                prefs.putUChar("bright_manual", bright_manual_val);
                prefs.end();
            }
        }
        if (proto.t.brightauto_changed) {
            proto.t.brightauto_changed = false;
            bright_auto = proto.t.bright_auto_val;
            if (prefs.begin("wb", false)) {
                prefs.putBool("bright_auto", bright_auto);
                prefs.putUChar("bright_manual", bright_manual_val);
                prefs.end();
            }
        }
    }

    // 2) DHT11 每 2.5s；DHT11Raw.read() 返回 true 成功，失败安全返回不崩溃
    if (now - last_dht > 2500) {
        last_dht = now;
        g_temp = NAN;
        g_hum = NAN;
        if (dht.read()) {
            const float temp = dht.getTemperature();
            const float hum = dht.getHumidity();
            // 断线或采样失败有时被 DHT11 读成 0/0；别把它当成真实室内环境。
            if (temp >= -20 && temp <= 60 && hum >= 1 && hum <= 100) {
                g_temp = temp;
                g_hum = hum;
            }
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
