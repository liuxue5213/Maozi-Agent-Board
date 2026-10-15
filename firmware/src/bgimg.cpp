#include "bgimg.h"
#include <LittleFS.h>
#include <JPEGDEC.h>
#include <mbedtls/base64.h>
#include <esp_heap_caps.h>
#include <lvgl.h>

namespace {
bool fs_ready = false;
bool decoded = false;
char pet_loaded[28] = "";              // 当前缓冲对应的帧文件名（跨函数共享）
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

bool album_delete(const char* name) {
    if (!fs_ready || !name) return false;
    const bool album = strlen(name) == 7 && name[0] == 's' && name[1] == 'v' &&
                       name[2] >= '1' && name[2] <= '8' && !strcmp(name + 3, ".jpg");
    const bool pet = !strncmp(name, "pet_", 4) && strlen(name) < 28 &&
                     strlen(name) >= 9 && !strcmp(name + strlen(name) - 4, ".jpg");
    if (!album && !pet) return false;
    if (pet) for (const char* p = name + 4; p < name + strlen(name) - 4; ++p)
        if (!((*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '_'))
            return false;
    char path[36];
    snprintf(path, sizeof(path), "/%s", name);
    // 不存在也算删除成功；只有仍存在且 remove 失败才应报错。
    if (LittleFS.exists(path) && !LittleFS.remove(path)) return false;
    if (pet) {
        pet_loaded[0] = 0;
        workanim_invalidate();
    }
    return true;
}

const void* bgimg_source() { return dsc; }

bool bgimg_init() {
    fs_ready = LittleFS.begin();   // profile_init 已挂载；这里只确认状态
    fs_ready = fs_ready || LittleFS.begin(true);
    if (fs_ready) {                // 临时调试：列举根目录与容量
        File root = LittleFS.open("/");
        int n = 0;
        for (File f = root.openNextFile(); f && n < 12; f = root.openNextFile()) {
            Serial.printf("[FS] %s %u\n", f.name(), (unsigned)f.size());
            ++n;
        }
        Serial.printf("[FS] total=%u used=%u\n",
                      (unsigned)LittleFS.totalBytes(), (unsigned)LittleFS.usedBytes());
    }
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
        // 目标名白名单：bg.jpg（页面背景，立即解码应用）/
        // sv1..8.jpg（屏保相册）/ pet_<小写串>.jpg（角色图，显示时按需解码）。
        // 旧版忽略 END 名字写死 /bg.jpg——相册上传会覆盖页面背景（真 bug）。
        char target[28] = "bg.jpg";
        const char* nm = value + 4;
        if (nm[0]) {
            size_t nlen = strlen(nm);
            bool ok = nlen >= 6 && nlen < sizeof(target) &&   // bg.jpg 只有 6 字符
                      !strcmp(nm + nlen - 4, ".jpg");
            if (ok && !strncmp(nm, "bg", 2)) ok = (nlen == 6);
            else if (ok && !strncmp(nm, "sv", 2))
                ok = nlen == 7 && nm[2] >= '1' && nm[2] <= '8';
            else if (ok && !strncmp(nm, "pet_", 4)) {
                if (nlen < 9) ok = false;
                for (size_t i = 4; ok && i < nlen - 4; ++i) {
                    char c = nm[i];
                    if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') ||
                          c == '_')) ok = false;
                }
            }
            if (!ok) { reject("NAME"); return false; }
            snprintf(target, sizeof(target), "/%s", nm);   // 必须带前导斜杠：无斜杠是相对路径，rename/直写全部失败（.tmp 残留实锤）
        }
        // 直接写目标文件：tmp+rename 在此工程会失败（vfs 对目标创建被拒，
        // 且 remove 先删旧文件会让失败演变成「图片永久丢失」）。
        // 写入中断的最坏情况 = 坏图，显示前解码校验会拒绝并回落像素宠物。
        File file = LittleFS.open(target, "w");
        if (!file) {
            Serial.printf("NAK=IMG,OPEN %s\n", target);
            reject("STORAGE"); return false;
        }
        size_t written = file.write((const uint8_t*)staging.c_str(), staging.length());
        file.close();
        if (written != staging.length()) {
            LittleFS.remove(target);
            reject("STORAGE"); return false;
        }
        const size_t applied_size = staging.length();
        staging = ""; stage_active = false;
        if (!strcmp(target, "/bg.jpg") || !strcmp(target, "bg.jpg")) {
            if (!bgimg_decode()) { reject("DECODE"); return false; }
            return true;   // 新背景已应用，调用方触发 ui_rebuild
        }
        if (!strncmp(target, "/pet_", 5)) {
            pet_loaded[0] = 0;   // 同名重传也要换新图
            // 双缓冲供各状态复用；任意角色帧改写后都要作废缓存。
            workanim_invalidate();
        }
        Serial.printf("ACK=IMG,%s,%u\n", target, (unsigned)applied_size);
        return true;       // 相册/角色图落盘即可，显示时按需解码
    }
    return false;
}

// ---- 角色图（pet_<状态>.jpg）：单缓冲按需解码，状态切换换图 ----
static uint16_t* pet_pixels = nullptr;
static lv_img_dsc_t* pet_dsc = nullptr;


int draw_mcu_pet(JPEGDRAW* draw) {
    if (!pet_pixels) return 0;
    int x = draw->x, y = draw->y, w = draw->iWidth, h = draw->iHeight;
    if (x + w > IMG_W) w = IMG_W - x;
    if (y + h > IMG_H) h = IMG_H - y;
    if (w <= 0 || h <= 0) return 1;
    for (int row = 0; row < h; ++row) {
        memcpy(pet_pixels + (size_t)(y + row) * IMG_W + x,
               draw->pPixels + (size_t)row * draw->iWidth, w * 2);
    }
    return 1;
}

bool petimg_exists(const char* name) {
    char path[36];
    snprintf(path, sizeof(path), "/%s.jpg", name);   // 调用方传帧名，文件带后缀
    bool ex = fs_ready && LittleFS.exists(path);
    return ex;
}

bool petimg_decode_name(const char* name) {
    if (!fs_ready || !name || !name[0]) return false;
    if (pet_dsc && !strcmp(pet_loaded, name)) return true;   // 已在缓冲
    char path[36];
    snprintf(path, sizeof(path), "/%s.jpg", name);
    Serial.printf("[PETD] decode %s exists=%d\n", path, (int)LittleFS.exists(path));
    if (!LittleFS.exists(path)) return false;
    File f = LittleFS.open(path, "r");
    if (!f) { Serial.printf("[PETD] open failed %s\n", path); return false; }
    size_t size = f.size();
    if (size == 0 || size > MAX_IMG) {
        Serial.printf("[PETD] invalid size %s %u\n", path, (unsigned)size);
        f.close(); return false;
    }
    uint8_t* jpg = (uint8_t*)malloc(size);
    if (!jpg) { f.close(); return false; }
    f.read(jpg, size);
    f.close();
    if (!pet_pixels) {
        pet_pixels = (uint16_t*)heap_caps_calloc(320 * 480, 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    }
    if (!pet_pixels) {
        Serial.printf("[PETD] no PSRAM %s free=%u\n", path,
                      (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        free(jpg); return false;
    }
    static JPEGDEC jpet;
    if (!jpet.openFLASH(jpg, size, draw_mcu_pet)) {
        Serial.printf("[PETD] jpeg open failed %s %u\n", path, (unsigned)size);
        free(jpg); return false;
    }
    bool ok = (jpet.decode(0, 0, 0) == 1);
    jpet.close();
    free(jpg);
    if (!ok) {
        Serial.printf("[PETD] jpeg decode failed %s %u\n", path, (unsigned)size);
        return false;
    }
    Serial.printf("[PETD] ready %s %u\n", path, (unsigned)size);
    if (!pet_dsc) pet_dsc = new lv_img_dsc_t();
    pet_dsc->header.always_zero = 0;
    pet_dsc->header.w = 320;
    pet_dsc->header.h = 480;
    pet_dsc->header.cf = LV_IMG_CF_TRUE_COLOR;
    pet_dsc->data = (const uint8_t*)pet_pixels;
    pet_dsc->data_size = 320 * 480 * 2;
    snprintf(pet_loaded, sizeof(pet_loaded), "%s", name);
    return true;
}

const lv_img_dsc_t* petimg_source() { return pet_dsc; }
const char* petimg_loaded() { return pet_loaded; }


// ---- 状态动画：A/B 帧及局部混合输出 ----
static uint16_t *wa_px[2] = {};
static lv_img_dsc_t *wa_dsc[2] = {};
static char wa_name[2][28] = {};
// 最多 12 张已解码帧约占 3.6MB PSRAM；复访状态时免去 JPEG 解码。
// 画面缓冲 wa_px 与缓存分离，LRU 淘汰不会使 LVGL 引用悬空。
struct AnimCacheEntry {
    char name[28] = {};
    uint16_t* pixels = nullptr;
    uint32_t used = 0;
};
static AnimCacheEntry anim_cache[12];
static uint32_t anim_cache_clock = 0;
static uint8_t interaction_prefetch_cursor = 0;
static uint16_t* motion_px = nullptr;
static lv_img_dsc_t motion_dsc = {};

static uint16_t* wa_target = nullptr;
int draw_mcu_wa(JPEGDRAW* draw) {
    uint16_t* px = wa_target;
    if (!px) return 0;
    int x = draw->x, y = draw->y, w = draw->iWidth, h = draw->iHeight;
    if (x + w > 320) w = 320 - x;
    if (y + h > 480) h = 480 - y;
    if (w <= 0 || h <= 0) return 1;
    for (int row = 0; row < h; ++row) {
        memcpy(px + (size_t)(y + row) * 320 + x,
               draw->pPixels + (size_t)row * draw->iWidth, w * 2);
    }
    return 1;
}

static bool decode_wa_jpeg(const char* name, uint16_t* target) {
    if (!fs_ready || !target) return false;
    char path[36];
    snprintf(path, sizeof(path), "/%s", name);
    if (!LittleFS.exists(path)) return false;
    File f = LittleFS.open(path, "r");
    if (!f) return false;
    size_t size = f.size();
    if (size == 0 || size > MAX_IMG) { f.close(); return false; }
    uint8_t* jpg = (uint8_t*)malloc(size);
    if (!jpg) { f.close(); return false; }
    f.read(jpg, size);
    f.close();
    // JPEGDEC 回调无法传参，解码只在主循环进行。
    wa_target = target;
    static JPEGDEC jwa;
    if (!jwa.openFLASH(jpg, size, draw_mcu_wa)) {
        Serial.printf("[PETA] jpeg open failed %s %u\n", name, (unsigned)size);
        wa_target = nullptr;
        free(jpg); return false;
    }
    bool ok = (jwa.decode(0, 0, 0) == 1);
    jwa.close();
    wa_target = nullptr;
    free(jpg);
    if (!ok) {
        Serial.printf("[PETA] jpeg decode failed %s %u\n", name, (unsigned)size);
        return false;
    }
    return true;
}

static AnimCacheEntry* cache_find(const char* name, bool touch = true) {
    for (auto& entry : anim_cache)
        if (entry.pixels && !strcmp(entry.name, name)) {
            if (touch) entry.used = ++anim_cache_clock;
            return &entry;
        }
    return nullptr;
}

static AnimCacheEntry* cache_reserve(const char* name) {
    AnimCacheEntry* entry = nullptr;
    for (auto& candidate : anim_cache) {
        if (!candidate.pixels) { entry = &candidate; break; }
        if (!entry || candidate.used < entry->used) entry = &candidate;
    }
    if (!entry) return nullptr;
    // 先申请新缓冲；内存不足时保留旧缓存并回退到直接 JPEG 解码。
    uint16_t* next = (uint16_t*)heap_caps_malloc(320 * 480 * 2,
                                                  MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!next) return nullptr;
    if (entry->pixels) heap_caps_free(entry->pixels);
    entry->pixels = next;
    snprintf(entry->name, sizeof(entry->name), "%s", name);
    entry->used = ++anim_cache_clock;
    return entry;
}

void workanim_prefetch_step() {
    // 每次主循环最多预解码一帧，避免一次性解码 6 帧把屏幕停住近 1 秒。
    static const char* names[] = {
        "pet_playing.jpg", "pet_playing_b.jpg",
        "pet_feeding.jpg", "pet_feeding_b.jpg",
        "pet_drinking.jpg", "pet_drinking_b.jpg",
    };
    if (!fs_ready || interaction_prefetch_cursor >= sizeof(names) / sizeof(names[0]))
        return;
    const char* name = names[interaction_prefetch_cursor++];
    if (cache_find(name, false)) return;
    char path[36];
    snprintf(path, sizeof(path), "/%s", name);
    if (!LittleFS.exists(path)) return;
    AnimCacheEntry* entry = cache_reserve(name);
    if (!entry) return;
    if (!decode_wa_jpeg(name, entry->pixels)) {
        heap_caps_free(entry->pixels);
        *entry = AnimCacheEntry();
        return;
    }
    Serial.printf("[PETA] prefetched %s free=%u\n", name,
                  (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

bool workanim_decode(int slot, const char* name) {
    if (!fs_ready || slot < 0 || slot > 1) return false;
    constexpr size_t bytes = 320 * 480 * 2;
    if (!wa_px[slot]) wa_px[slot] = (uint16_t*)heap_caps_malloc(
        bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!wa_px[slot]) {
        Serial.printf("[PETA] no PSRAM %s free=%u\n", name,
                      (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        return false;
    }
    AnimCacheEntry* hit = cache_find(name);
    if (hit) {
        memcpy(wa_px[slot], hit->pixels, bytes);
    } else {
        if (!decode_wa_jpeg(name, wa_px[slot])) return false;
        AnimCacheEntry* entry = cache_reserve(name);
        if (entry) memcpy(entry->pixels, wa_px[slot], bytes);
    }
    if (!wa_dsc[slot]) wa_dsc[slot] = new lv_img_dsc_t();
    wa_dsc[slot]->header.always_zero = 0;
    wa_dsc[slot]->header.w = 320;
    wa_dsc[slot]->header.h = 480;
    wa_dsc[slot]->header.cf = LV_IMG_CF_TRUE_COLOR;
    wa_dsc[slot]->data = (const uint8_t*)wa_px[slot];
    wa_dsc[slot]->data_size = 320 * 480 * 2;
    snprintf(wa_name[slot], sizeof(wa_name[slot]), "%s", name);
    return true;
}

const lv_img_dsc_t* workanim_source(int slot) { return wa_dsc[slot]; }
bool workanim_ready(int slot) { return slot >= 0 && slot <= 1 &&
                                     wa_dsc[slot] != nullptr && wa_name[slot][0]; }

const lv_img_dsc_t* workanim_blend_roi(uint8_t alpha) {
    if (!wa_dsc[0] || !wa_dsc[1] || !wa_px[0] || !wa_px[1]) return nullptr;
    if (!motion_px) motion_px = (uint16_t*)heap_caps_malloc(
        PET_MOTION_W * PET_MOTION_H * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!motion_px) return nullptr;
    // 边缘羽化：场景始终取 A，只有宠物主体逐级过渡，防止整屏跳帧。
    for (int y = 0; y < PET_MOTION_H; ++y) {
        int ey = y < 14 ? y : PET_MOTION_H - 1 - y;
        if (ey > 14) ey = 14;
        for (int x = 0; x < PET_MOTION_W; ++x) {
            int ex = x < 14 ? x : PET_MOTION_W - 1 - x;
            if (ex > 14) ex = 14;
            int edge = ex < ey ? ex : ey;
            int a = ((int)alpha * edge) / 14;
            size_t src = (size_t)(PET_MOTION_Y + y) * IMG_W + PET_MOTION_X + x;
            uint16_t c0 = wa_px[0][src], c1 = wa_px[1][src];
            if (!a) { motion_px[y * PET_MOTION_W + x] = c0; continue; }
            int r = (((c0 >> 11) & 31) * (255 - a) + ((c1 >> 11) & 31) * a + 127) / 255;
            int g = (((c0 >> 5) & 63) * (255 - a) + ((c1 >> 5) & 63) * a + 127) / 255;
            int b = ((c0 & 31) * (255 - a) + (c1 & 31) * a + 127) / 255;
            motion_px[y * PET_MOTION_W + x] = (r << 11) | (g << 5) | b;
        }
    }
    motion_dsc.header.always_zero = 0;
    motion_dsc.header.w = PET_MOTION_W;
    motion_dsc.header.h = PET_MOTION_H;
    motion_dsc.header.cf = LV_IMG_CF_TRUE_COLOR;
    motion_dsc.data = (const uint8_t*)motion_px;
    motion_dsc.data_size = PET_MOTION_W * PET_MOTION_H * 2;
    return &motion_dsc;
}

void workanim_invalidate() {
    // 双缓冲现供任意状态复用。文件被改写后清缓存，UI 下次渲染时按
    // 当前状态解码；不能在这里擅自换回工作帧。
    for (int s = 0; s < 2; ++s) {
        wa_name[s][0] = 0;
        // 保留描述符对象；重复上传角色包时不泄漏堆内存。
    }
    for (auto& entry : anim_cache) {
        if (entry.pixels) heap_caps_free(entry.pixels);
        entry = AnimCacheEntry();
    }
    anim_cache_clock = 0;
    interaction_prefetch_cursor = 0;
}
