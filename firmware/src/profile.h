#pragma once
#include <Arduino.h>

enum WidgetKind : uint8_t {
    W_AI_LIST, W_HW_CARDS, W_CLOCK, W_ENVIRONMENT,
    W_REMINDERS, W_CALENDAR, W_ALARM, W_INVALID
};

struct PageConfig {
    char id[17] = "";
    char title[25] = "";
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
    uint8_t reminder_count = 0, calendar_count = 0, alarm_count = 0, app_count = 0;
    ScheduleItem reminders[20], calendar[50], alarms[16];
    AppEntry apps[12];
    uint32_t bg = 0x0B1220, grad = 0x0E1B33, card = 0x274064, accent = 0x22D3EE;
    uint32_t text = 0xF3F7FF, sub = 0xA8BDD9;
    // 卡片描边色：填充色差不足以在深色主题下勾出边界时，靠它保证可见。
    uint32_t card_edge = 0x3E506E;
    uint8_t bright = 90;
};

extern DeviceProfile g_profile;
void profile_init();
bool profile_receive_cfg(const char* value);  // true when a complete profile was applied
const char* profile_widget_name(WidgetKind kind);
