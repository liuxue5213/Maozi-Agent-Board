#include "proto.h"
#include <Update.h>
#include <mbedtls/base64.h>
#include "profile.h"
#include "bgimg.h"

extern void send_fw_handshake();   // main.cpp 提供：收到 CAPS（客户端问候）时回握手

void Proto::feed(char c) {
    if (c == '\r') return;
    if (c == '\n') {
        if (_len > 0) {
            _line[_len] = 0;
            handle(_line);
            _len = 0;
        }
        return;
    }
    if (_len < sizeof(_line) - 1) {
        _line[_len++] = c;
    } else {  // 行超长，丢弃重来
        _len = 0;
    }
}

static uint32_t hex6(const char* v) {
    return (uint32_t)strtoul(v, nullptr, 16) & 0xFFFFFF;
}

void Proto::handle_cfg(const char* value) {
    if (!strncmp(value, "BEGIN,", 6) || !strncmp(value, "DATA,", 5) ||
        !strncmp(value, "END,", 4)) {
        if (profile_receive_cfg(value)) profile_changed = true;
        return;
    }
    // CFG=theme:dark,bg:0B1220,card:141E32,accent:22D3EE,text:F3F7FF,sub:8CA3C7,bright:90
    char buf[160];
    strncpy(buf, value, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    char* save = nullptr;
    for (char* pair = strtok_r(buf, ",", &save); pair;
         pair = strtok_r(nullptr, ",", &save)) {
        char* eq = strchr(pair, ':');
        if (!eq) continue;
        *eq = 0;
        const char* k = pair;
        const char* v = eq + 1;
        if (!strcmp(k, "bright")) {
            t.bright = constrain(atoi(v), 0, 100);
        } else if (!strcmp(k, "lang")) {
            strncpy(t.lang, v, sizeof(t.lang) - 1);
        } else if (!strcmp(k, "theme")) {
            strncpy(t.theme, v, sizeof(t.theme) - 1);
        } else if (!strcmp(k, "bg"))         { t.theme_bg = hex6(v); t.theme_changed = true; }
        else if (!strcmp(k, "grad"))         { t.theme_grad = hex6(v); t.theme_changed = true; }
        else if (!strcmp(k, "card"))         { t.theme_card = hex6(v); t.theme_changed = true; }
        else if (!strcmp(k, "accent"))       { t.theme_accent = hex6(v); t.theme_changed = true; }
        else if (!strcmp(k, "text"))         { t.theme_text = hex6(v); t.theme_changed = true; }
        else if (!strcmp(k, "sub"))          { t.theme_sub = hex6(v); t.theme_changed = true; }
    }
    dirty = true;
}

void Proto::handle_ai(int idx, const char* value) {
    if (idx < 0 || idx > 3) return;
    AITask& a = t.tasks[idx];
    char buf[96];
    strncpy(buf, value, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = 0;
    // strtok_r 会跳过空字段；项目名为空时必须保留其位置，才能正确读会话 ID。
    char* fields[7] = {};
    char* cursor = buf;
    for (int i = 0; i < 7; ++i) {
        fields[i] = cursor;
        if (!cursor) break;
        char* comma = strchr(cursor, ',');
        if (comma) { *comma = 0; cursor = comma + 1; }
        else cursor = nullptr;
    }
    if (!fields[0] || !fields[1] || !fields[0][0] || !fields[1][0]) {
        a.used = false;
        dirty = true;
        return;
    }
    a.used = true;
    memset(a.tool, 0, sizeof(a.tool));
    strncpy(a.tool, fields[0], sizeof(a.tool) - 1);
    memset(a.state, 0, sizeof(a.state));
    strncpy(a.state, fields[1], sizeof(a.state) - 1);
    a.prog = fields[2] ? atoi(fields[2]) : -1;
    a.elapse = fields[3] ? atoi(fields[3]) : 0;
    memset(a.proj, 0, sizeof(a.proj));
    if (fields[4]) strncpy(a.proj, fields[4], sizeof(a.proj) - 1);
    memset(a.id, 0, sizeof(a.id));
    if (fields[5]) strncpy(a.id, fields[5], sizeof(a.id) - 1);
    a.age = fields[6] ? atoi(fields[6]) : 0;
    dirty = true;
}

void Proto::handle(const char* line) {
    const char* eq = strchr(line, '=');
    if (!eq) return;
    char key[16];
    size_t klen = eq - line;
    if (klen >= sizeof(key)) return;
    memcpy(key, line, klen);
    key[klen] = 0;
    const char* v = eq + 1;

    if (!strcmp(key, "CFG"))  { handle_cfg(v); return; }
    if (!strcmp(key, "CAPS")) { send_fw_handshake(); return; }   // 客户端问候 → 回能力握手
    if (!strcmp(key, "IMG")) {
        if (!strcmp(v, "DELETE")) {
            bgimg_delete();
            bg_changed = true;
            return;
        }
        if (bgimg_receive_cfg(v)) bg_changed = true;
        return;
    }
    if (!strcmp(key, "HB"))   return;   // 心跳由 main 的时间戳逻辑处理
    if (!strcmp(key, "TIME"))  { t.clock_hms = (uint32_t)atol(v); dirty = true; return; }
    if (!strcmp(key, "DATE"))  { snprintf(t.date, sizeof(t.date), "%s", v); dirty = true; return; }
    if (!strcmp(key, "REMIND")) { strncpy(t.remind, v, sizeof(t.remind) - 1); dirty = true; return; }
    if (!strncmp(key, "AI", 2) && key[2] >= '0' && key[2] <= '3') {
        handle_ai(key[2] - '0', v);
        return;
    }
    if (!strcmp(key, "AIU")) {
        // AIU=<tool>,<model>,<elapsed> → 提取模型名展示
        char buf[64];
        strncpy(buf, v, sizeof(buf) - 1);
        buf[sizeof(buf) - 1] = 0;
        char* c1 = strchr(buf, ',');
        if (c1) {
            char* c2 = strchr(c1 + 1, ',');
            if (c2) *c2 = 0;
            strncpy(t.ai_model, c1 + 1, sizeof(t.ai_model) - 1);
            t.ai_model[sizeof(t.ai_model) - 1] = 0;
        }
        dirty = true;
        return;
    }
    if (!strcmp(key, "OTA")) {
        static uint32_t ota_size = 0, ota_seq = 0;
        static bool ota_active = false;
        static uint8_t bin_buf[96]; size_t bin_len = 0;
        if (!strncmp(v, "BEGIN,", 6)) {
            ota_size = strtoul(strchr(v, ',') + 1, nullptr, 10);
            if (!ota_size || ota_size > 3 * 1024 * 1024 || !Update.begin(ota_size)) {
                Serial.println("NAK=OTA,BEGIN"); return;
            }
            ota_active = true; ota_seq = 0;
            Serial.println("ACK=OTA,BEGIN");
            return;
        }
        if (!strncmp(v, "DATA,", 5) && ota_active) {
            const char* b64_start = v + 5;
            while (*b64_start == ',') ++b64_start;
            if (strtoul(b64_start, (char**)&b64_start, 10) != ota_seq) {
                Update.abort(); ota_active = false;
                Serial.println("NAK=OTA,SEQ"); return;
            }
            while (*b64_start == ',') ++b64_start;
            uint8_t bin[96]; size_t bin_len = 0;
            if (mbedtls_base64_decode(bin, sizeof(bin), &bin_len,
                (const unsigned char*)b64_start, strlen(b64_start)) != 0) {
                Update.abort(); ota_active = false;
                Serial.println("NAK=OTA,DECODE"); return;
            }
            Update.write(bin, bin_len);
            ++ota_seq;
            return;
        }
        if (!strncmp(v, "END", 3) && ota_active) {
            if (!Update.end(true)) {
                ota_active = false;
                Serial.println("NAK=OTA,VERIFY"); return;
            }
            ota_active = false;
            Serial.println("ACK=OTA,REBOOTING");
            Serial.flush();
            delay(500);
            ESP.restart();
            return;
        }
        return;
    }
    if (!strcmp(key, "AIT")) {
        t.day_tok_total = atol(v) * 1000;   // 客户端单位 K → tokens
        dirty = true;
        return;
    }

    if      (!strcmp(key, "CPU"))      t.cpu = atoi(v);
    else if (!strcmp(key, "CPUFREQ"))  t.cpufreq = atoi(v);
    else if (!strcmp(key, "CPUTEMP"))  { t.cputemp = atof(v); t.cpu_temp_valid = !isnan(t.cputemp); }
    else if (!strcmp(key, "MEM"))      t.mem = atoi(v);
    else if (!strcmp(key, "MEMUSED"))  t.memused = atof(v);
    else if (!strcmp(key, "MEMTOTAL")) t.memtotal = atof(v);
    else if (!strcmp(key, "GPU"))      { t.gpu = atoi(v); t.gpu_valid = true; }
    else if (!strcmp(key, "GPUMEM"))   t.gpumem = atoi(v);
    else if (!strcmp(key, "GPUTEMP"))  { t.gputemp = atof(v); t.gpu_temp_valid = !isnan(t.gputemp); }
    else if (!strcmp(key, "DISK"))     t.disk = atoi(v);
    else if (!strcmp(key, "DISKFREE")) t.diskfree = atof(v);
    else if (!strcmp(key, "DISKTOTAL")) t.disktotal = atof(v);
    else if (!strcmp(key, "BAT"))      t.bat = atoi(v);
    else if (!strcmp(key, "UPTIME"))   t.uptime = (uint32_t)atol(v);
    else if (!strcmp(key, "VOL"))      t.vol = atoi(v);
    else if (!strcmp(key, "MUT"))      t.mut = atoi(v);
    else if (!strcmp(key, "WX")) {
        // WX=<temp>,<code>,<tmax>,<tmin>
        char buf[48];
        strncpy(buf, v, sizeof(buf) - 1);
        buf[sizeof(buf) - 1] = 0;
        char* save = nullptr;
        char* f1 = strtok_r(buf, ",", &save);
        char* f2 = f1 ? strtok_r(nullptr, ",", &save) : nullptr;
        char* f3 = f2 ? strtok_r(nullptr, ",", &save) : nullptr;
        char* f4 = f3 ? strtok_r(nullptr, ",", &save) : nullptr;
        t.wx_temp = f1 ? atof(f1) : NAN;
        t.wx_code = f2 ? atoi(f2) : -1;
        t.wx_hi = f3 ? atof(f3) : NAN;
        t.wx_lo = f4 ? atof(f4) : NAN;
        t.wx_valid = !isnan(t.wx_temp);
    }
    else if (!strcmp(key, "AISTATE"))  strncpy(t.aistate, v, sizeof(t.aistate) - 1);
    else if (!strcmp(key, "AITOOL"))   strncpy(t.aitool, v, sizeof(t.aitool) - 1);
    else if (!strcmp(key, "AITASK"))   strncpy(t.aitask, v, sizeof(t.aitask) - 1);
    else if (!strcmp(key, "AIPROG"))   t.aiprog = atoi(v);
    else if (!strcmp(key, "AITIME"))   t.aitime = atoi(v);
    else if (!strcmp(key, "AIERR"))    strncpy(t.aierr, v, sizeof(t.aierr) - 1);
    dirty = true;
}

String uplink_env(float temp, float hum) {
    String s = "";
    s += isnan(temp) ? "TEMP=NA\n" : "TEMP=" + String(temp, 1) + "\n";
    s += isnan(hum) ? "HUM=NA\n" : "HUM=" + String((int)hum) + "\n";
    return s;
}

String uplink_hb(uint32_t secs) {
    return "HB=" + String(secs) + "\n";
}

String uplink_touch(const char* page) {
    return "TOUCH=" + String(page) + "\n";
}

String uplink_bright(int v) {
    return "BRIGHT=" + String(v) + "\n";
}

String uplink_key(const char* action) {
    return "KEY=" + String(action) + "\n";
}

String uplink_launch(const char* app_id) {
    return "LAUNCH=" + String(app_id) + "\n";
}
