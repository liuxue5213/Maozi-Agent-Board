#pragma once
#include <Arduino.h>

enum WidgetKind : uint8_t {
    W_AI_LIST, W_HW_CARDS, W_CLOCK, W_ENVIRONMENT,
    W_REMINDERS, W_CALENDAR, W_ALARM, W_WEATHER, W_COUNTDOWN, W_AI_COST,
    W_PET, W_FREE, W_INVALID
};

// 自由布局页组件（竞品拖拽编辑器 v1：固定尺寸、坐标自由）
// t：0=clock 1=date 2=lunar 3=weather 4=sys 5=quote
struct FreeComp {
    uint8_t t = 0;
    uint8_t big = 0;      // 样式档：0=常规 1=大字（clock/weather 支持，竞品「时间样式」）
    uint8_t hidden = 0;   // 隐藏：配置保留但不渲染（竞品「锁定/隐藏」）
    uint8_t color = 0;    // 自定义文字颜色：0=正文 1=强调 2=次要 3=警示
    uint8_t city = 0;     // t=weather 的数据源城市下标（竞品#59 数据绑定）
    uint16_t x = 0, y = 0;
    char text[25] = "";   // t=text 时的自定义文字（≤8 汉字）
};

struct PageConfig {
    char id[17] = "";
    char title[25] = "";
    // 本页只显示哪个工具的会话（空 = 全部，工具页由此而来）
    char filter[13] = "";
    // 天气页绑定的城市名（须在 weather.cities 里；空 = 非/默认天气页）
    char city[13] = "";
    bool list = false;
    bool free = false;
    uint8_t count = 0;
    WidgetKind widgets[4] = {};
    uint8_t comp_n = 0;
    FreeComp comps[8] = {};
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
    uint8_t pet_sleep = 1;   // 凌晨自动睡觉（01-06 硬覆盖；0=仅犯困提示）
    uint8_t pet_greet = 0;   // 整点/半点问候
    uint8_t pet_show_stats = 1;    // 角色页数值行（竞品#29 显示内容勾选）
    uint8_t pet_show_buttons = 1;  // 角色页喂食/喝水/运动按钮
    // 任务视图 tv.* 参数（竞品对齐）：AI 页渲染细节 + 空闲时切页
    uint8_t tv_other_tasks = 1;  // AI 页其他会话 3 行：1 显示 / 0 隐藏
    uint8_t tv_task_value = 0;   // 其他任务行右侧：0 用时 / 1 隐藏（仅项目名）
    uint8_t tv_tl_mode = 0;      // AI 列表渲染：0 常规行（点+进度条）/ 1 时间线事件行
    uint8_t tv_tl_time = 0;      // 时间线行时间：0 更新时刻(相对) / 1 本轮用时 / 2 不显示
    uint8_t tv_idle_page = 0;    // 空闲超时切页（1 基，0=不切；须为存在的页）
    uint8_t tv_name_mode = 0;    // 左上冠名：0=页面标题 1=自动智能体名（竞品#19）
    uint8_t tv_tl_range = 0;     // 时间线范围：0=含近期完成 1=仅进行中（竞品#16）
    // AI 用量卡看板参数（竞品图4「内容组合/显示指标」）
    uint8_t ac_mode = 0;         // 0=自动轮换 1=固定显示第一勾选项
    // 勾选槽按用户勾选顺序存储（位掩码会丢顺序，固定模式取的就是「第一个」）
    uint8_t ac_slots[4] = {0, 1, 2};   // 0=今日 1=本轮 2=模型名 3=分模型消耗
    uint8_t ac_slot_n = 3;
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
