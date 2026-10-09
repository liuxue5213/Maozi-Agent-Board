#include "profile.h"
#include <ArduinoJson.h>
#include <LittleFS.h>
#include <mbedtls/base64.h>

DeviceProfile g_profile;

namespace {
bool fs_ready = false;
String staging;
uint32_t stage_version = 0, stage_length = 0, stage_crc = 0;
uint16_t stage_seq = 0;
bool stage_active = false;

// DeviceProfile 约 8.5KB，绝不能在栈上构造临时对象（loopTask 栈仅 8KB）。
// 统一用一个静态空白模板做复位，赋值为成员逐项拷贝，不产生栈临时。
static DeviceProfile blank_profile;

static void reset_profile(DeviceProfile& p) {
    p = blank_profile;
}

void copy_text(char* dst, size_t cap, const char* src) {
    if (!src) src = "";
    snprintf(dst, cap, "%s", src);
}

WidgetKind widget_kind(const char* name) {
    if (!strcmp(name, "ai_list")) return W_AI_LIST;
    if (!strcmp(name, "hw_cards")) return W_HW_CARDS;
    if (!strcmp(name, "clock")) return W_CLOCK;
    if (!strcmp(name, "environment")) return W_ENVIRONMENT;
    if (!strcmp(name, "reminder_list")) return W_REMINDERS;
    if (!strcmp(name, "today_calendar")) return W_CALENDAR;
    if (!strcmp(name, "alarm")) return W_ALARM;
    if (!strcmp(name, "weather")) return W_WEATHER;
    if (!strcmp(name, "countdown")) return W_COUNTDOWN;
    if (!strcmp(name, "ai_cost")) return W_AI_COST;
    return W_INVALID;
}

uint32_t crc32(const uint8_t* data, size_t length) {
    uint32_t crc = 0xFFFFFFFF;
    for (size_t i = 0; i < length; ++i) {
        crc ^= data[i];
        for (int j = 0; j < 8; ++j)
            crc = (crc >> 1) ^ ((crc & 1) ? 0xEDB88320 : 0);
    }
    return crc ^ 0xFFFFFFFF;
}

bool schedule_array(JsonVariantConst source, ScheduleItem* target,
                    uint8_t max_count, uint8_t& count, bool calendar) {
    if (!source.is<JsonArrayConst>() || source.size() > max_count) return false;
    count = source.size();
    for (uint8_t i = 0; i < count; ++i) {
        JsonObjectConst item = source[i].as<JsonObjectConst>();
        if (item.isNull()) return false;
        const char* id = item["id"] | "";
        const char* date = item["date"] | "";
        const char* tm = item["time"] | "";
        const char* txt = item[calendar ? "title" : "text"] | "";
        if (!calendar && item["label"].is<const char*>()) txt = item["label"];
        if (!*id || strlen(id) > 16 || strlen(date) > 10 || strlen(tm) != 5 ||
            !*txt || strlen(txt) > 48) return false;
        copy_text(target[i].id, sizeof(target[i].id), id);
        copy_text(target[i].date, sizeof(target[i].date), date);
        copy_text(target[i].time, sizeof(target[i].time), tm);
        copy_text(target[i].text, sizeof(target[i].text), txt);
        target[i].enabled = item["enabled"] | true;
        target[i].weekdays = 0;
        if (!calendar) {
            JsonArrayConst weekdays = item["weekdays"].as<JsonArrayConst>();
            if (weekdays.isNull() || !weekdays.size()) return false;
            for (JsonVariantConst day : weekdays) {
                int d = day.as<int>();
                if (d < 1 || d > 7) return false;
                target[i].weekdays |= 1 << (d - 1);
            }
        }
    }
    return true;
}

bool decode_profile(JsonDocument& doc, DeviceProfile& out) {
    if ((doc["schema"] | 0) != 1) return false;
    uint32_t version = doc["version"] | 0;
    JsonArrayConst pages = doc["pages"].as<JsonArrayConst>();
    if (!version || pages.isNull() || pages.size() < 1 || pages.size() > 6) return false;
    reset_profile(out);
    out.version = version;
    out.page_count = pages.size();
    for (uint8_t i = 0; i < out.page_count; ++i) {
        JsonObjectConst page = pages[i].as<JsonObjectConst>();
        if (page.isNull()) return false;
        const char* id = page["id"] | "";
        const char* title = page["title"] | "";
        const char* layout = page["layout"] | "";
        JsonArrayConst widgets = page["widgets"].as<JsonArrayConst>();
        if (!*id || strlen(id) > 16 || !*title || strlen(title) > 24 ||
            (strcmp(layout, "list") && strcmp(layout, "grid_2x2")) ||
            widgets.isNull() || widgets.size() < 1 || widgets.size() > 4) return false;
        PageConfig& dst = out.pages[i];
        copy_text(dst.id, sizeof(dst.id), id);
        copy_text(dst.title, sizeof(dst.title), title);
        dst.list = !strcmp(layout, "list");
        dst.count = widgets.size();
        uint8_t units = 0;
        for (uint8_t j = 0; j < dst.count; ++j) {
            const char* name = widgets[j] | "";
            WidgetKind kind = widget_kind(name);
            if (kind == W_INVALID) return false;
            for (uint8_t k = 0; k < j; ++k) if (dst.widgets[k] == kind) return false;
            dst.widgets[j] = kind;
            uint8_t needed = (kind == W_AI_LIST || kind == W_HW_CARDS) ? 4 :
                (kind == W_REMINDERS || kind == W_CALENDAR ? 2 : 1);
            units += needed;
            if (needed == 4 && dst.count != 1) return false;
        }
        if (units > 4) return false;
        if (!dst.list) {
            uint8_t slot = 0;
            for (uint8_t j = 0; j < dst.count; ++j) {
                WidgetKind kind = dst.widgets[j];
                uint8_t needed = (kind == W_AI_LIST || kind == W_HW_CARDS) ? 4 :
                    (kind == W_REMINDERS || kind == W_CALENDAR ? 2 : 1);
                if (needed == 2 && (slot % 2)) ++slot;
                slot += needed;
            }
            if (slot > 4) return false;
        }
    }
    if (!schedule_array(doc["reminders"], out.reminders, 20, out.reminder_count, false) ||
        !schedule_array(doc["calendar"], out.calendar, 50, out.calendar_count, true) ||
        !schedule_array(doc["alarms"], out.alarms, 16, out.alarm_count, false)) return false;
    JsonArrayConst cds = doc["countdowns"].as<JsonArrayConst>();
    if (!cds.isNull()) {
        if (!schedule_array(doc["countdowns"], out.countdowns, 8, out.countdown_count, true))
            return false;
    } else {
        out.countdown_count = 0;
    }
    JsonArrayConst apps = doc["apps"].as<JsonArrayConst>();
    if (!apps.isNull()) {
        if (apps.size() > 12) return false;
        out.app_count = apps.size();
        for (uint8_t i = 0; i < out.app_count; ++i) {
            JsonObjectConst app = apps[i].as<JsonObjectConst>();
            if (app.isNull()) return false;
            const char* id = app["id"] | "";
            const char* name = app["name"] | "";
            if (!*id || strlen(id) > 12 || strlen(name) > 16) return false;
            for (const char* p = id; *p; ++p)
                if (!isalnum((int)(unsigned char)*p) && *p != '-' && *p != '_') return false;
            copy_text(out.apps[i].id, sizeof(out.apps[i].id), id);
            copy_text(out.apps[i].name, sizeof(out.apps[i].name), name);
        }
    }
    JsonObjectConst theme = doc["theme"].as<JsonObjectConst>();
    if (!theme.isNull()) {
        auto color = [&](const char* key, uint32_t fallback) {
            const char* value = theme[key] | "";
            return strlen(value) == 6 ? (uint32_t)strtoul(value, nullptr, 16) : fallback;
        };
        out.bg = color("bg", out.bg); out.grad = color("grad", out.grad);
        out.card = color("card", out.card);
        out.accent = color("accent", out.accent); out.text = color("text", out.text);
        out.sub = color("sub", out.sub);
        out.card_edge = color("card_edge", out.card_edge);
        // card_opa 客户端下发的是百分比(30-100)，LVGL 的 bg_opa 需要 0-255。
        out.card_opa = (uint8_t)(constrain((int)(theme["card_opa"] | 100), 30, 100) * 255 / 100);
        out.bright = constrain((int)(theme["bright"] | 90), 5, 100);
    }
    return true;
}

void set_defaults() {
    reset_profile(g_profile);
    g_profile.page_count = 3;
    copy_text(g_profile.pages[0].id, 17, "ai");
    copy_text(g_profile.pages[0].title, 25, "AI 工作台");
    g_profile.pages[0].list = true;
    g_profile.pages[0].widgets[0] = W_AI_LIST; g_profile.pages[0].count = 1;
    copy_text(g_profile.pages[1].id, 17, "computer");
    copy_text(g_profile.pages[1].title, 25, "电脑");
    g_profile.pages[1].widgets[0] = W_HW_CARDS; g_profile.pages[1].count = 1;
    copy_text(g_profile.pages[2].id, 17, "environment");
    copy_text(g_profile.pages[2].title, 25, "环境");
    g_profile.pages[2].widgets[0] = W_ENVIRONMENT;
    g_profile.pages[2].widgets[1] = W_CLOCK; g_profile.pages[2].count = 2;
}

bool load_slot(const char* filename, DeviceProfile& target) {
    File file = LittleFS.open(filename, "r");
    if (!file || file.size() > 8192) return false;
    DynamicJsonDocument doc(16384);
    auto err = deserializeJson(doc, file);
    file.close();
    return !err && decode_profile(doc, target);
}

void reject(const char* reason) {
    Serial.printf("NAK=CFG,%lu,%s\n", (unsigned long)stage_version, reason);
    staging = ""; stage_active = false;
}
} // namespace

const char* profile_widget_name(WidgetKind kind) {
    switch (kind) {
    case W_AI_LIST: return "ai_list"; case W_HW_CARDS: return "hw_cards";
    case W_CLOCK: return "clock"; case W_ENVIRONMENT: return "environment";
    case W_REMINDERS: return "reminder_list"; case W_CALENDAR: return "today_calendar";
    case W_ALARM: return "alarm"; default: return "invalid";
    }
}

void profile_init() {
    set_defaults();
    fs_ready = LittleFS.begin(true);
    if (!fs_ready) return;
    DeviceProfile* candidate = new DeviceProfile();
    if (!candidate) return;
    if (load_slot("/profile_a.json", *candidate)) g_profile = *candidate;
    if (load_slot("/profile_b.json", *candidate) &&
        candidate->version >= g_profile.version) g_profile = *candidate;
    delete candidate;
}

bool profile_receive_cfg(const char* value) {
    if (!strncmp(value, "BEGIN,", 6)) {
        unsigned long v = 0, length = 0, crc = 0;
        stage_version = 0;
        if (sscanf(value, "BEGIN,%lu,%lu,%lx", &v, &length, &crc) != 3 ||
            !v || !length || length > 8192) {
            reject("SIZE"); return false;
        }
        stage_version = v; stage_length = length; stage_crc = crc;
        stage_seq = 0; staging = ""; staging.reserve(stage_length + 1);
        stage_active = true; return false;
    }
    if (!strncmp(value, "DATA,", 5)) {
        if (!stage_active) return false;
        const char* first = strchr(value + 5, ',');
        const char* second = first ? strchr(first + 1, ',') : nullptr;
        if (!first || !second || strtoul(value + 5, nullptr, 10) != stage_version ||
            strtoul(first + 1, nullptr, 10) != stage_seq) {
            reject("SEQ"); return false;
        }
        uint8_t bytes[90]; size_t length = 0;
        const unsigned char* encoded = (const unsigned char*)second + 1;
        if (mbedtls_base64_decode(bytes, sizeof(bytes), &length, encoded, strlen((const char*)encoded)) != 0 ||
            staging.length() + length > stage_length) {
            reject("DATA"); return false;
        }
        staging.concat((const char*)bytes, length);
        ++stage_seq; return false;
    }
    if (!strncmp(value, "END,", 4)) {
        if (!stage_active || strtoul(value + 4, nullptr, 10) != stage_version ||
            staging.length() != stage_length) {
            reject("LENGTH"); return false;
        }
        if (crc32((const uint8_t*)staging.c_str(), staging.length()) != stage_crc) {
            reject("CRC_MISMATCH"); return false;
        }
        DynamicJsonDocument doc(16384);
        DeviceProfile* candidate = new DeviceProfile();
        if (!candidate) { reject("MEMORY"); return false; }
        if (deserializeJson(doc, staging) || !decode_profile(doc, *candidate) ||
            candidate->version != stage_version) {
            delete candidate;
            reject("INVALID_CONFIG"); return false;
        }
        if (!fs_ready) { delete candidate; reject("STORAGE"); return false; }
        const char* dest = (g_profile.version % 2) ? "/profile_b.json" : "/profile_a.json";
        const char* tmp = "/profile.tmp";
        File file = LittleFS.open(tmp, "w");
        if (!file || file.write((const uint8_t*)staging.c_str(), staging.length()) != staging.length()) {
            if (file) file.close(); delete candidate; reject("STORAGE"); return false;
        }
        file.close();
        LittleFS.remove(dest);
        if (!LittleFS.rename(tmp, dest)) { delete candidate; reject("STORAGE"); return false; }
        g_profile = *candidate;
        delete candidate;
        Serial.printf("ACK=CFG,%lu,APPLIED\n", (unsigned long)stage_version);
        staging = ""; stage_active = false;
        return true;
    }
    return false;
}
