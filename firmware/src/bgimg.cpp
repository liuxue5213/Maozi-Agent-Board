#include "bgimg.h"
#include <LittleFS.h>
#include <JPEGDEC.h>
#include <mbedtls/base64.h>
#include <esp_heap_caps.h>
#include <lvgl.h>

namespace {
bool fs_ready = false;
bool decoded = false;
String staging;
uint32_t stage_len = 0, stage_crc = 0;
uint16_t stage_seq = 0;
bool stage_active = false;
constexpr uint32_t MAX_IMG = 120 * 1024;

constexpr int IMG_W = 320, IMG_H = 480;
uint16_t* pixels = nullptr;          // PSRAM RGB565，320×480 = 300KB
lv_img_dsc_t* dsc = nullptr;
uint16_t* sv_pixels = nullptr;       // 屏保相册独立缓冲（不占用页面背景）
lv_img_dsc_t* sv_dsc = nullptr;

uint32_t crc32(const uint8_t* data, size_t length) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int j = 0; j < 8; ++j)
            crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320 : 0);
    }
    return crc ^ 0xFFFFFFFF;
}

void reject(const char* reason) {
    Serial.printf("NAK=IMG,%s\n", reason);
    staging = ""; stage_active = false;
}

// JPEGDEC 逐 MCU 回调：把 8×8/16×16 块拷进 PSRAM 输出缓冲
int draw_mcu(JPEGDRAW* draw) {
    if (!pixels) return 0;
    int x = draw->x, y = draw->y, w = draw->iWidth, h = draw->iHeight;
    if (x + w > IMG_W) w = IMG_W - x;
    if (y + h > IMG_H) h = IMG_H - y;
    if (w <= 0 || h <= 0) return 1;
    for (int row = 0; row < h; ++row) {
        memcpy(pixels + (size_t)(y + row) * IMG_W + x,
               draw->pPixels + (size_t)row * draw->iWidth, w * 2);
    }
    return 1;
}

int draw_mcu_sv(JPEGDRAW* draw) {
    if (!sv_pixels) return 0;
    int x = draw->x, y = draw->y, w = draw->iWidth, h = draw->iHeight;
    if (x + w > IMG_W) w = IMG_W - x;
    if (y + h > IMG_H) h = IMG_H - y;
    if (w <= 0 || h <= 0) return 1;
    for (int row = 0; row < h; ++row) {
        memcpy(sv_pixels + (size_t)(y + row) * IMG_W + x,
               draw->pPixels + (size_t)row * draw->iWidth, w * 2);
    }
    return 1;
}
}  // namespace

bool bgimg_ready() { return decoded && pixels && dsc; }

// ---- 屏保相册：按 LittleFS 文件名解码到独立缓冲（不占用页面背景的 pixels） ----
bool album_decode_path(const char* path) {
    if (!fs_ready) return false;
    File f = LittleFS.open(path, "r");
    if (!f) return false;
    size_t size = f.size();
    if (size == 0 || size > MAX_IMG) { f.close(); return false; }
    uint8_t* jpg = (uint8_t*)malloc(size);
    if (!jpg) { f.close(); return false; }
    f.read(jpg, size);
    f.close();
    if (!sv_pixels) {
        sv_pixels = (uint16_t*)heap_caps_calloc(320 * 480, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (!sv_pixels) { free(jpg); return false; }
    static JPEGDEC jsv;
    if (!jsv.openFLASH(jpg, size, draw_mcu_sv)) { free(jpg); return false; }
    bool ok = (jsv.decode(0, 0, 0) == 1);
    jsv.close();
    free(jpg);
    if (!ok) return false;
    if (!sv_dsc) sv_dsc = new lv_img_dsc_t();
    sv_dsc->header.always_zero = 0;
    sv_dsc->header.w = 320;
    sv_dsc->header.h = 480;
    sv_dsc->header.cf = LV_IMG_CF_TRUE_COLOR;
    sv_dsc->data = (const uint8_t*)sv_pixels;
    sv_dsc->data_size = 320 * 480 * 2;
    return true;
}

const lv_img_dsc_t* album_source() { return sv_dsc; }

uint8_t album_count() {
    if (!fs_ready) return 0;
    uint8_t n = 0;
    char path[16];
    for (uint8_t i = 1; i <= 8; ++i) {
        snprintf(path, sizeof(path), "/sv%d.jpg", i);
        if (LittleFS.exists(path)) ++n;
    }
    return n;
}

bool album_decode_index(uint8_t idx) {
    if (!idx || idx > 8) return false;
    char path[16];
    snprintf(path, sizeof(path), "/sv%d.jpg", idx);
    return album_decode_path(path);
}

void album_delete(const char* name) {
    if (!fs_ready) return;
    char path[16];
    snprintf(path, sizeof(path), "/%s", name);
    LittleFS.remove(path);
}

const void* bgimg_source() { return dsc; }

bool bgimg_init() {
    fs_ready = LittleFS.begin();   // profile_init 已挂载；这里只确认状态
    fs_ready = fs_ready || LittleFS.begin(true);
    if (fs_ready && LittleFS.exists("/bg.jpg")) return bgimg_decode();
    return false;
}

bool bgimg_decode() {
    decoded = false;
    if (!fs_ready || !LittleFS.exists("/bg.jpg")) return false;
    File f = LittleFS.open("/bg.jpg", "r");
    if (!f) return false;
    size_t size = f.size();
    if (size == 0 || size > MAX_IMG) { f.close(); return false; }
    uint8_t* jpg = (uint8_t*)malloc(size);
    if (!jpg) { f.close(); return false; }
    f.read(jpg, size);
    f.close();

    if (!pixels) {
        pixels = (uint16_t*)heap_caps_calloc(IMG_W * IMG_H, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (!pixels) { free(jpg); return false; }

    static JPEGDEC jpeg;
    if (!jpeg.openFLASH(jpg, size, draw_mcu)) { free(jpg); return false; }
    bool ok = (jpeg.decode(0, 0, 0) == 1);
    jpeg.close();
    free(jpg);
    if (!ok) return false;

    if (!dsc) dsc = new lv_img_dsc_t();
    dsc->header.always_zero = 0;
    dsc->header.w = IMG_W;
    dsc->header.h = IMG_H;
    dsc->header.cf = LV_IMG_CF_TRUE_COLOR;
    dsc->data = (const uint8_t*)pixels;
    dsc->data_size = (uint32_t)IMG_W * IMG_H * 2;
    decoded = true;
    Serial.println("ACK=IMG,APPLIED");
    return true;
}

bool bgimg_delete() {
    decoded = false;
    if (fs_ready && LittleFS.exists("/bg.jpg")) LittleFS.remove("/bg.jpg");
    Serial.println("ACK=IMG,DELETED");
    return true;
}

bool bgimg_receive_cfg(const char* value) {
    if (!strncmp(value, "BEGIN,", 6)) {
        unsigned long length = 0, crc = 0;
        if (sscanf(value, "BEGIN,%lu,%lx", &length, &crc) != 2 ||
            !length || length > MAX_IMG) {
            reject("SIZE"); return false;
        }
        stage_len = length; stage_crc = crc;
        stage_seq = 0; staging = ""; staging.reserve(stage_len);
        stage_active = true; return false;
    }
    if (!strncmp(value, "DATA,", 5)) {
        if (!stage_active) return false;
        const char* first = strchr(value + 5, ',');
        if (!first || strtoul(value + 5, nullptr, 10) != stage_seq) {
            reject("SEQ"); return false;
        }
        uint8_t bytes[96]; size_t length = 0;
        const unsigned char* encoded = (const unsigned char*)first + 1;
        if (mbedtls_base64_decode(bytes, sizeof(bytes), &length, encoded, strlen((const char*)encoded)) != 0 ||
            staging.length() + length > stage_len) {
            reject("DATA"); return false;
        }
        staging.concat((const char*)bytes, length);
        ++stage_seq; return false;
    }
    if (!strncmp(value, "END,", 4)) {
        if (!stage_active || staging.length() != stage_len ||
            crc32((const uint8_t*)staging.c_str(), staging.length()) != stage_crc) {
            reject("CRC_MISMATCH"); return false;
        }
        if (!fs_ready) { reject("STORAGE"); return false; }
        File file = LittleFS.open("/bg.tmp", "w");
        if (!file || file.write((const uint8_t*)staging.c_str(), staging.length()) != staging.length()) {
            if (file) file.close();
            reject("STORAGE"); return false;
        }
        file.close();
        LittleFS.remove("/bg.jpg");
        LittleFS.rename("/bg.tmp", "/bg.jpg");
        staging = ""; stage_active = false;
        if (!bgimg_decode()) { reject("DECODE"); return false; }
        return true;   // 新图已应用，调用方触发 ui_rebuild
    }
    return false;
}
