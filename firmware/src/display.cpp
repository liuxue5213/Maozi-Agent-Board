#include "display.h"
#include "config.h"
#include <lvgl.h>

namespace {
constexpr int SCR_W = 320;
constexpr int SCR_H = 480;

class LGFX_WorkBuddy : public lgfx::LGFX_Device {
    lgfx::Panel_ST7796 _panel;
    lgfx::Bus_SPI _bus;
    lgfx::Light_PWM _light;
    lgfx::Touch_FT5x06 _touch;

public:
    LGFX_WorkBuddy() {
        {   // SPI 总线
            auto cfg = _bus.config();
            cfg.spi_host = SPI2_HOST;
            cfg.spi_mode = 0;
            // 80MHz 已在真机验证出现文字边缘撕裂/重影（80MHz 逼近 ST7796 上限，
            // 杜邦线 + 面包板寄生电容导致 SCL 采样沿不稳，字形笔画出现"重影"）。
            // 降到 40MHz 实测文字清晰，代价是全屏刷新变慢（见 flush_cb 分区优化）。
            cfg.freq_write = 40000000;
            cfg.freq_read = 16000000;
            cfg.spi_3wire = false;
            cfg.use_lock = true;
            cfg.dma_channel = 0;
            cfg.pin_sclk = PIN_SCK;
            cfg.pin_mosi = PIN_MOSI;
            cfg.pin_miso = -1;
            cfg.pin_dc = PIN_DC;
            _bus.config(cfg);
        }
        _panel.setBus(&_bus);
        {   // ST7796 面板（native 320x480 竖屏）
            auto cfg = _panel.config();
            cfg.pin_cs = PIN_CS;
            cfg.pin_rst = PIN_RST;
            cfg.pin_busy = -1;
            cfg.panel_width = 320;
            cfg.panel_height = 480;
            cfg.memory_width = 320;
            cfg.memory_height = 480;
            cfg.offset_rotation = 0;
            cfg.invert = false;          // 该屏不需要反色（反了会变黄底）
            cfg.rgb_order = false;       // 若红蓝互换改为 true
            cfg.readable = false;
            _panel.config(cfg);
        }
        {   // 背光 LEDC PWM（25kHz 超出人耳范围，避免电流音）
            auto cfg = _light.config();
            cfg.pin_bl = PIN_BL;
            cfg.invert = false;
            cfg.freq = 25000;
            cfg.pwm_channel = 7;
            _light.config(cfg);
        }
        _panel.setLight(&_light);
        {   // FT6236 触摸（FT5x06 寄存器兼容）
            auto cfg = _touch.config();
            cfg.i2c_port = 0;
            cfg.i2c_addr = 0x38;
            cfg.freq = 400000;
            cfg.pin_sda = PIN_TSDA;
            cfg.pin_scl = PIN_TSCL;
            cfg.pin_int = -1;          // 轮询模式
            cfg.bus_shared = false;
            cfg.x_min = 0; cfg.x_max = 319;   // 面板 native 坐标
            cfg.y_min = 0; cfg.y_max = 479;
            cfg.offset_rotation = 0;
            _touch.config(cfg);
        }
        _panel.setTouch(&_touch);
        setPanel(&_panel);
    }
};

LGFX_WorkBuddy _gfx;
lv_disp_draw_buf_t _draw_buf;
lv_color_t _buf1[SCR_W * 40];
lv_color_t _buf2[SCR_W * 40];
lv_disp_drv_t _disp_drv;
lv_indev_drv_t _indev_drv;
int _brightness = DEFAULT_BRIGHT;

void flush_cb(lv_disp_drv_t* drv, const lv_area_t* area, lv_color_t* color_p) {
    int32_t w = area->x2 - area->x1 + 1;
    int32_t h = area->y2 - area->y1 + 1;
    _gfx.startWrite();
    // LVGL 在 flush_ready 后会立即复用缓冲区；同步传输可避免 DMA 尚未读完时
    // 缓冲区被下一帧覆盖，造成闪烁和撕裂。
    _gfx.pushImage(area->x1, area->y1, w, h, (uint16_t*)color_p);
    _gfx.endWrite();
    lv_disp_flush_ready(drv);
}

void touch_cb(lv_indev_drv_t*, lv_indev_data_t* data) {
    uint16_t x, y;
    if (_gfx.getTouch(&x, &y)) {
        data->point.x = x;
        data->point.y = y;
        data->state = LV_INDEV_STATE_PRESSED;
    } else {
        data->state = LV_INDEV_STATE_RELEASED;
    }
}
}  // namespace

lgfx::LGFX_Device* gfx() { return &_gfx; }

void display_init() {
    _gfx.init();
    _gfx.setRotation(2);   // 竖屏 180°：用户把屏幕倒置，画面旋转 180° 补偿（触摸坐标 LovyanGFX 自动重映射）
    _gfx.fillScreen(COL_BG);
    _gfx.setBrightness(DEFAULT_BRIGHT);

    lv_init();
    lv_disp_draw_buf_init(&_draw_buf, _buf1, _buf2, SCR_W * 40);
    lv_disp_drv_init(&_disp_drv);
    _disp_drv.hor_res = SCR_W;
    _disp_drv.ver_res = SCR_H;
    _disp_drv.flush_cb = flush_cb;
    _disp_drv.draw_buf = &_draw_buf;
    _disp_drv.antialiasing = true;
    lv_disp_drv_register(&_disp_drv);

    lv_indev_drv_init(&_indev_drv);
    _indev_drv.type = LV_INDEV_TYPE_POINTER;
    _indev_drv.read_cb = touch_cb;
    lv_indev_drv_register(&_indev_drv);
}

void display_brightness(int percent) {
    percent = constrain(percent, 0, 100);
    if (percent == _brightness) return;
    _brightness = percent;
    _gfx.setBrightness(_brightness);
}

int display_brightness_get() { return _brightness; }
