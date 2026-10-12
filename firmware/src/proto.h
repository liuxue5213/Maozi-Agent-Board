#pragma once
#include <Arduino.h>

/** 城市天气（W0~W2 实况 + WF0~WF2 五天预报，今天起） */
struct CityDay {
    int8_t code = -1;
    int8_t lo = 0, hi = 0;
};
struct CityWx {
    bool used = false;
    float temp = 0;
    int code = -1;
    int8_t hi = 0, lo = 0;
    CityDay days[5];
};

/** 最近完成的会话行（DN0~DN2，新→旧） */
struct DoneTask {
    bool used = false;
    char tool[12];
    char proj[32];
    int age = 0;
};

/** 并行 AI 任务行（AI0~AI7；固件按页面 filter 过滤后取用） */
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
    AITask tasks[8];
    DoneTask done[3];
    CityWx cities[3];
    // 宠物状态（PS=饱食,水分,活力；0-100，客户端 pet.py 计算）
    int8_t pet_h = 80, pet_t = 75, pet_e = 90;
    // 屏保增强配置（CFG svint/svfx/pint）
    int svint = -1, svfx = -1, pint = -1;
    bool svint_changed = false, svfx_changed = false, pint_changed = false;
    // 正在播放（NP=歌名,歌手,位置秒,时长秒,播放中）
    struct NowPlaying {
        bool used = false;
        char title[49] = "";
        char artist[25] = "";
        int16_t pos = 0, dur = 0;
        bool playing = false;
    } np;
    uint32_t done_at_ms[3] = {0, 0, 0};   // 收到该 DN 行的时刻，用于本地推进年龄
    char remind[48] = "";
    // 硬件
    int cpu = 0, cpufreq = 0;
    float cputemp = NAN;
    int mem = 0;
    float memused = NAN, memtotal = NAN;
    int gpu = 0, gpumem = 0;
    float gputemp = NAN;
    float fan = NAN;
    int disk = 0;
    float diskfree = NAN;
    float disktotal = NAN;
    float netrx = NAN, nettx = NAN;   // 网速 Mbps（NAN = 未提供）
    int bat = -1;
    uint32_t uptime = 0;
    // 配置
    int bright = -1;
    char lang[8] = "en";
    char theme[16] = "dark";
    // 托盘/上位机请求切页（1 基；0=无效），main 循环转给 ui_set_page
    int page_req = 0;
    bool page_req_changed = false;
    bool pet_play_changed = false;   // 收到 PET=play（客户端互动）
    // 工作台「实拍」：SNAP=1 下行后设备抓当前画面经串口回传
    bool snap_req_changed = false;
    // 电脑控制状态（控制中心显示；-1 = 未知/不可用）
    int vol = -1;
    int mut = -1;
    // 服务端收件箱待处理数（页脚琥珀角标「N件」；-1 = 尚未收到）
    int inbox = -1;
    bool inbox_changed = false;
    // 天气（客户端 30 分钟刷新）
    float wx_temp = NAN, wx_hi = NAN, wx_lo = NAN;
    int wx_code = -1;
    bool wx_valid = false;
    // AI 用量（K tokens）
    char ai_model[16] = "";
    long ai_tok_out = -1;                      // 焦点会话本轮输出
    long day_tok_total = -1;                   // 今日总 token
    // 分模型今日消耗（AIB0..2 = 模型名,token；按消耗降序）
    char ai_by_model[3][20] = {};
    long ai_by_tok[3] = {-1, -1, -1};
    // 主题色推送（CFG=bg:xxxxxx,card:...,accent:...,text:...,sub:...）
    uint32_t theme_bg = 0, theme_grad = 0, theme_card = 0, theme_accent = 0, theme_text = 0, theme_sub = 0;
    bool theme_changed = false;
    int idledim = -1;
    bool idledim_changed = false;
    int saver = -1;
    bool saver_changed = false;
    int top_clock_cfg = -1, top_wx_cfg = -1;
    bool topbar_changed = false;
    int cardopa_cfg = -1;
    bool cardopa_changed = false;
    int bright_auto_val = -1;
    bool brightauto_changed = false;
    char savermsg[64] = "";
    bool savermsg_changed = false;

    bool cpu_temp_valid = false;
    bool gpu_valid = false;
    bool gpu_temp_valid = false;
    bool fan_valid = false;
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
    // OTA 每片 512B 经 Base64 + 序号后最长约 710 字节。旧 192B 缓冲会
    // 静默截断每一个 OTA=DATA 行，客户端却只能在最后看到 SEQ NAK。
    char _line[768];
    uint16_t _len = 0;
    void handle(const char* line);
    void handle_cfg(const char* value);
    void handle_ai(int idx, const char* value);
    void handle_dn(int idx, const char* value);
    void handle_w(int idx, const char* value);
    void handle_wf(int idx, const char* value);
};

/** 上行帧构造 */
String uplink_env(float temp, float hum);
String uplink_hb(uint32_t secs);
String uplink_touch(const char* page);
String uplink_bright(int v);
String uplink_key(const char* action);
String uplink_pet(const char* action);    // feed water play
String uplink_launch(const char* app_id);
