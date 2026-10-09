#include <Arduino.h>
#include <LovyanGFX.h>
#include <lvgl.h>
#include "lv_conf.h"

// 与产品固件相同的引脚定义
#define PIN_SCK   12
#define PIN_MOSI  11
#define PIN_CS    10
#define PIN_DC    13
#define PIN_RST   14
#define PIN_BL    6
#define PIN_TSDA  8
#define PIN_TSCL  9

class LGFX_Diag : public lgfx::LGFX_Device {
    lgfx::Panel_ST7796 _panel;
    lgfx::Bus_SPI _bus;
    lgfx::Light_PWM _light;
    lgfx::Touch_FT5x06 _touch;

public:
    LGFX_Diag() {
        auto bus = _bus.config();
        bus.spi_host = SPI2_HOST;
        bus.spi_mode = 0;
        bus.freq_write = 40000000;   // 自检故意降速：排除高速信号完整性问题
        bus.freq_read = 16000000;
        bus.spi_3wire = false;
        bus.use_lock = true;
        bus.dma_channel = 0;
        bus.pin_sclk = PIN_SCK;
        bus.pin_mosi = PIN_MOSI;
        bus.pin_miso = -1;
        bus.pin_dc = PIN_DC;
        _bus.config(bus);

        _panel.setBus(&_bus);
        auto panel = _panel.config();
        panel.pin_cs = PIN_CS;
        panel.pin_rst = PIN_RST;
        panel.pin_busy = -1;
        panel.panel_width = 320;
        panel.panel_height = 480;
        panel.memory_width = 320;
        panel.memory_height = 480;
        panel.offset_rotation = 0;
        panel.invert = false;
        panel.rgb_order = false;
        panel.readable = false;
        _panel.config(panel);

        auto light = _light.config();
        light.pin_bl = PIN_BL;
        light.invert = false;
        light.freq = 25000;
        light.pwm_channel = 7;
        _light.config(light);
        _panel.setLight(&_light);

        auto touch = _touch.config();
        touch.i2c_port = 0;
        touch.i2c_addr = 0x38;
        touch.freq = 400000;
        touch.pin_sda = PIN_TSDA;
        touch.pin_scl = PIN_TSCL;
        touch.pin_int = -1;
        touch.bus_shared = false;
        touch.x_min = 0; touch.x_max = 319;
        touch.y_min = 0; touch.y_max = 479;
        touch.offset_rotation = 0;
        _touch.config(touch);
        _panel.setTouch(&_touch);

        setPanel(&_panel);
    }
};

LGFX_Diag gfx;

// ---- 阶段 1：旋转方向选择器（每方向 6 秒）----
void draw_rot_page(uint8_t rot) {
    gfx.fillScreen(0x000000);
    gfx.setTextColor(0x07E0, 0x000000);
    gfx.setFont(&fonts::Font7);
    gfx.setCursor(60, 120);
    gfx.printf("ROT %u", (unsigned)rot);
    gfx.setTextColor(0xFFFF, 0x000000);
    gfx.setFont(&fonts::Font4);
    gfx.setCursor(30, 260);
    gfx.print("TOP marker is here");
    gfx.fillTriangle(150, 20, 170, 20, 160, 60, 0x22D3EE);   // 顶部三角 = 内容的"上"
    gfx.setFont(&fonts::Font2);
    gfx.setTextColor(0xFBBF24, 0x000000);
    gfx.setCursor(30, 300);
    gfx.print("hold panel as YOUR front;");
    gfx.setCursor(30, 322);
    gfx.print("note the ROT number that");
    gfx.setCursor(30, 344);
    gfx.print("shows upright text");
    Serial.printf("DIAG: showing ROT %u\n", (unsigned)rot);
}

void stage_rotation_pick() {
    for (uint8_t r = 0; r < 4; ++r) {
        gfx.setRotation(r);
        draw_rot_page(r);
        delay(6000);
    }
    gfx.setRotation(0);
}

// ---- 阶段 2：LVGL 大字 + 触摸光标 ----
lv_disp_draw_buf_t draw_buf;
lv_color_t buf1[320 * 40];
lv_color_t buf2[320 * 40];
lv_disp_drv_t disp_drv;
lv_indev_drv_t indev_drv;
lv_obj_t* touch_label;
lv_obj_t* touch_dot;

void flush_cb(lv_disp_drv_t* drv, const lv_area_t* area, lv_color_t* color_p) {
    uint32_t w = area->x2 - area->x1 + 1, h = area->y2 - area->y1 + 1;
    gfx.startWrite();
    gfx.pushImage(area->x1, area->y1, w, h, (uint16_t*)color_p);
    gfx.endWrite();
    lv_disp_flush_ready(drv);
}

void touch_cb(lv_indev_drv_t*, lv_indev_data_t* data) {
    uint16_t x, y;
    if (gfx.getTouch(&x, &y)) {
        data->point.x = x;
        data->point.y = y;
        data->state = LV_INDEV_STATE_PRESSED;
        static char pos[32];
        snprintf(pos, sizeof(pos), "touch: %u , %u", (unsigned)x, (unsigned)y);
        lv_label_set_text(touch_label, pos);
        lv_obj_set_pos(touch_dot, x - 15, y - 15);
        lv_obj_clear_flag(touch_dot, LV_OBJ_FLAG_HIDDEN);
        Serial.printf("DIAG: touch %u,%u\n", (unsigned)x, (unsigned)y);
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}

void stage_lvgl() {
    Serial.println("DIAG: stage2 LVGL starting");
    lv_init();
    lv_disp_draw_buf_init(&draw_buf, buf1, buf2, 320 * 40);
    lv_disp_drv_init(&disp_drv);
    disp_drv.hor_res = 320;
    disp_drv.ver_res = 480;
    disp_drv.flush_cb = flush_cb;
    disp_drv.draw_buf = &draw_buf;
    disp_drv.antialiasing = true;
    lv_disp_drv_register(&disp_drv);

    lv_indev_drv_init(&indev_drv);
    indev_drv.type = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = touch_cb;
    lv_indev_drv_register(&indev_drv);

    lv_obj_t* scr = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(scr, lv_color_hex(0x0B1220), 0);
    lv_obj_set_style_border_width(scr, 0, 0);

    lv_obj_t* title = lv_label_create(scr);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_48, 0);
    lv_obj_set_style_text_color(title, lv_color_hex(0x22D3EE), 0);
    lv_label_set_text(title, "STAGE2");
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 40);

    lv_obj_t* sub = lv_label_create(scr);
    lv_obj_set_style_text_font(sub, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(sub, lv_color_hex(0xF3F7FF), 0);
    lv_label_set_text(sub, "LVGL text OK if you read this");
    lv_obj_align(sub, LV_ALIGN_TOP_MID, 0, 120);

    lv_obj_t* cn = lv_label_create(scr);
    lv_obj_set_style_text_font(cn, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(cn, lv_color_hex(0x34D399), 0);
    lv_label_set_text(cn, "BOOT OK");
    lv_obj_align(cn, LV_ALIGN_TOP_MID, 0, 170);

    touch_label = lv_label_create(scr);
    lv_obj_set_style_text_font(touch_label, &lv_font_montserrat_24, 0);
    lv_obj_set_style_text_color(touch_label, lv_color_hex(0xFBBF24), 0);
    lv_label_set_text(touch_label, "touch: - , -");
    lv_obj_align(touch_label, LV_ALIGN_BOTTOM_MID, 0, -60);

    touch_dot = lv_obj_create(scr);
    lv_obj_set_size(touch_dot, 30, 30);
    lv_obj_set_style_radius(touch_dot, 15, 0);
    lv_obj_set_style_bg_color(touch_dot, lv_color_hex(0xFBBF24), 0);
    lv_obj_set_style_border_width(touch_dot, 0, 0);
    lv_obj_add_flag(touch_dot, LV_OBJ_FLAG_HIDDEN);

    lv_scr_load(scr);
    Serial.println("DIAG: stage2 LVGL loaded");
}

void setup() {
    Serial.begin(115200);
    delay(300);
    Serial.println("DIAG: boot");
    gfx.init();
    gfx.setRotation(0);          // 竖屏 native
    gfx.fillScreen(0x000000);
    gfx.setBrightness(90);

    stage_rotation_pick();       // 24 秒轮播 4 个方向
    stage_lvgl();
}

void loop() {
    lv_timer_handler();
    delay(5);
}
