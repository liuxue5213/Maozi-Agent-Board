#pragma once
#include <Arduino.h>

enum WidgetKind : uint8_t {
    W_AI_LIST, W_HW_CARDS, W_CLOCK, W_ENVIRONMENT,
    W_REMINDERS, W_CALENDAR, W_ALARM, W_WEATHER, W_COUNTDOWN, W_AI_COST,
    W_PET, W_INVALID
};

struct PageConfig {
    char id[17] = "";
    char title[25] = "";
    // 本页只显示哪个工具的会话（空 = 全部，工具页由此而来）
    char filter[13] = "";
    // 天气页绑定的城市名（须在 weather.cities 里；空 = 非/默认天气页）
    char city[13] = "";
    bool list = false;
    uint8_t count = 0;
    WidgetKind widgets[4] = {};
};

struct ScheduleItem {
    char id[17] = "";
    char date[11] = "";
    char time[6] = "";
    char text[49] = "";
    uint8_t weekdays = 0;
    bool enabled = true;
};

/** 快捷启动应用（顶部下拉面板，客户端配置） */
struct AppEntry {
    char id[13] = "";
    char name[17] = "";
};

struct DeviceProfile {
    uint32_t version = 1;
    uint8_t page_count = 0;
    PageConfig pages[6];
    uint8_t reminder_count = 0, calendar_count = 0, alarm_count = 0, app_count = 0, countdown_count = 0;
    ScheduleItem reminders[20], calendar[50], alarms[16], countdowns[8];
    AppEntry apps[12];
    // 天气城市名（与客户端 profile.weather.cities 同序，W0..W2/WF0..WF2 对号）
    char city_names[3][13] = {};
    // 桌面宠物：类型（robot/cat/chick）+ 名字
    char pet_type[8] = "robot";
    char pet_name[13] = "帽子";
    // 诗句库（屏保「诗词短句」轮换；空 = 用内置四句）
    uint8_t poem_count = 0;
    char poems[10][61] = {};
    // 屏保增强：相册轮播间隔秒（样式4）、切换效果（0 立即/1 淡入/2 滑入）、
    // 诗句轮换间隔分钟（样式3）
    uint16_t svint = 60;
    uint8_t svfx = 1;
    uint16_t pint = 5;
    uint32_t bg = 0x060A12, grad = 0x0E1726, card = 0x28384E, accent = 0x4CC9F0;
    uint32_t text = 0xF1F5F9, sub = 0xA6B6C9;
    // 卡片描边色：填充色差不足以在深色主题下勾出边界时，靠它保证可见。
    uint32_t card_edge = 0x3C5070;
    // 卡片不透明度（0-255）。工作台滑杆与主题画廊的 card_opa 都经CFG 下发；
    // 此前 profile 未解析该字段，导致同步后不透明度丢失（仅实时 KV 生效）。
    uint8_t card_opa = 255;
    uint8_t bright = 90;
    uint16_t idle_dim = 60;
    uint8_t saver = 0;
    char saver_msg[64] = "代码写完了吗？起来喝口水";
    bool top_clock = true;
    bool top_weather = true;
    bool bright_auto = false;
};

extern DeviceProfile g_profile;
void profile_init();
bool profile_receive_cfg(const char* value);  // true when a complete profile was applied
const char* profile_widget_name(WidgetKind kind);
