#pragma once
#include <Arduino.h>

/** 并行 AI 任务行（AI0~AI3） */
struct AITask {
    bool used = false;
    char tool[12];
    char state[12];
    char proj[32];
    char id[9] = "";
    int age = 0;
    int prog = -1;
    int elapse = 0;
};

/** 与 docs/PROTOCOL.md 对应的下行遥测 */
struct Telemetry {
    uint32_t clock_hms = 0;   // HHMMSS
    char date[11] = "";       // YYYY-MM-DD
    AITask tasks[4];
    char remind[48] = "";
    // 硬件
    int cpu = 0, cpufreq = 0;
    float cputemp = NAN;
    int mem = 0;
    float memused = NAN, memtotal = NAN;
    int gpu = 0, gpumem = 0;
    float gputemp = NAN;
    int disk = 0;
    float diskfree = NAN;
    float disktotal = NAN;
    int bat = -1;
    uint32_t uptime = 0;
    // AI
    char aistate[16] = "IDLE";
    char aitool[16] = "";
    char aitask[16] = "";
    int aiprog = -1;
    int aitime = 0;
    char aierr[64] = "";
    // 配置
    int bright = -1;
    char lang[8] = "en";
    char theme[16] = "dark";
    // 电脑控制状态（控制中心显示；-1 = 未知/不可用）
    int vol = -1;
    int mut = -1;
    // 天气（客户端 30 分钟刷新）
    float wx_temp = NAN, wx_hi = NAN, wx_lo = NAN;
    int wx_code = -1;
    bool wx_valid = false;
    // 主题色推送（CFG=bg:xxxxxx,card:...,accent:...,text:...,sub:...）
    uint32_t theme_bg = 0, theme_grad = 0, theme_card = 0, theme_accent = 0, theme_text = 0, theme_sub = 0;
    bool theme_changed = false;

    bool cpu_temp_valid = false;
    bool gpu_valid = false;
    bool gpu_temp_valid = false;
};

/** 非阻塞行解析器：loop 里逐字符 feed */
class Proto {
public:
    Telemetry t;
    volatile bool dirty = false;
    bool profile_changed = false;
    bool bg_changed = false;
    void feed(char c);

private:
    char _line[192];
    uint8_t _len = 0;
    void handle(const char* line);
    void handle_cfg(const char* value);
    void handle_ai(int idx, const char* value);
};

/** 上行帧构造 */
String uplink_env(float temp, float hum);
String uplink_hb(uint32_t secs);
String uplink_touch(const char* page);
String uplink_bright(int v);
String uplink_key(const char* action);    // VOL+ VOL- MUTE PLAY NEXT PREV
String uplink_launch(const char* app_id);
