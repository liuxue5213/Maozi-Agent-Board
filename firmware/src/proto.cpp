#include "proto.h"
#include "profile.h"

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
    if (!strcmp(key, "CAPS")) return;   // 能力位图：固件侧暂不消费
    if (!strcmp(key, "HB"))   return;   // 心跳由 main 的时间戳逻辑处理
    if (!strcmp(key, "TIME"))  { t.clock_hms = (uint32_t)atol(v); dirty = true; return; }
    if (!strcmp(key, "DATE"))  { snprintf(t.date, sizeof(t.date), "%s", v); dirty = true; return; }
    if (!strcmp(key, "REMIND")) { strncpy(t.remind, v, sizeof(t.remind) - 1); dirty = true; return; }
    if (!strncmp(key, "AI", 2) && key[2] >= '0' && key[2] <= '3') {
        handle_ai(key[2] - '0', v);
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
