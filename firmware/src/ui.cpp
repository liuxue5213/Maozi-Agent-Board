#include "ui.h"
#include "config.h"
#include "display.h"
#include "fonts.h"
#include "profile.h"
#include "bgimg.h"
#include <lvgl.h>
#include <math.h>
#include <esp_heap_caps.h>
#include <mbedtls/base64.h>
// LVGL 8.3 的 lvgl.h 不自动引入 snapshot 模块头，显式包含
// （内容由 lv_conf.h 的 LV_USE_SNAPSHOT=1 门控）
#include "extra/others/snapshot/lv_snapshot.h"

extern float g_temp, g_hum;
void send_touch(const char* page);

// 竖屏 320×480 几何常量
namespace {
constexpr int SCR_W = 320, SCR_H = 480;
constexpr int HEAD_Y = 8;        // 标题行
constexpr int BODY_Y = 60;       // 内容区顶（上方留提示行）
constexpr int BODY_H = 380;      // 内容区高（60..440）
constexpr int FOOT_Y = 446;      // 页码行（底边 468，留 12px 安全边距，满足触控余量）
constexpr int EDGE_TOP = 90;     // 顶部下拉判定区
constexpr int EDGE_BOTTOM = SCR_H - 90;

uint32_t card_edge = COL_CARD_EDGE;

struct WidgetView {
    WidgetKind kind = W_INVALID;
    lv_obj_t *root = nullptr, *main = nullptr, *sub = nullptr;
    lv_obj_t* extra[8] = {};
    lv_obj_t* rows[5] = {};
    uint8_t row_slot[5] = {};
    // 工具页（filter 非空）的行文本：5 行 × (工具名 + 项目·用时)。
    // 全部页仍走 extra[] 的焦点布局，两套互不干扰。
    lv_obj_t* row_tool[5] = {};
    lv_obj_t* row_info[5] = {};
    lv_obj_t* done_band = nullptr;      // 工具页顶部「最近完成」条
    lv_obj_t* band_lbl = nullptr;
    char filter[13] = "";               // 本页工具过滤（空 = 全部）
    // 天气城市页（PageConfig.city 非空的 W_WEATHER）
    char city[13] = "";
    // 宠物页（W_PET）：三条状态条的填充与数值
    lv_obj_t *pm_fill[3] = {}, *pm_val[3] = {};
    lv_obj_t *wcur_tmp = nullptr, *wcur_cond = nullptr, *wcur_hilo = nullptr;
    lv_obj_t *wday_lbl[5] = {}, *wcond_lbl[5] = {}, *wtmp_lbl[5] = {};
    uint16_t card_w = 0;              // 卡片实际宽度：窄卡需换用短文案（见 render_generic）
    // 状态聚焦布局构件（对齐 docs/screen-ui-sample 效果图）：
    lv_obj_t* ai_count = nullptr;     // 顶部右侧「进行中 N · 共 M」
    lv_obj_t* ai_bar = nullptr;       // 焦点任务进度条
    lv_obj_t* ai_bar_fill = nullptr;
    lv_obj_t* ai_dot = nullptr;       // 焦点状态点
    lv_obj_t* ai_overview = nullptr;  // 单会话时利用空位显示电脑摘要
    lv_obj_t* ai_metrics[2] = {};
    lv_obj_t* row_dot[5] = {};        // 行状态点（工具页 5 行 / 全部页前 3 行）
    lv_obj_t* row_bar[5] = {};        // 行进度条
    lv_obj_t* row_fill[5] = {};
    lv_obj_t* hw_bar[4] = {};         // 硬件卡迷你条
    lv_obj_t* hw_temp[3] = {};        // 温度风扇三列卡数值（CPU/GPU/风扇）
    lv_obj_t* hw_fill[4] = {};
};
struct PageView {
    lv_obj_t *screen = nullptr, *conn = nullptr, *alert = nullptr;
    lv_obj_t *topclock = nullptr, *topwx = nullptr, *topdot = nullptr;
    lv_obj_t *offbar = nullptr;   // 离线状态条（顶部 4px 红条）
    lv_obj_t *inbox_lbl = nullptr;  // 收件箱角标（页脚右下「N件」，琥珀色）
    WidgetView widgets[4];
};

PageView views[6];
uint8_t view_count = 0, current_page = 0;
uint32_t bg = COL_BG, grad = COL_BG, card = COL_CARD, accent = COL_ACCENT;
uint8_t card_opa = 255;              // 卡片不透明度（0-255），背景图模式下 70% 左右最有层次
uint32_t text_color = COL_TEXT, sub_color = COL_SUB;
bool online = false, have_data = false, alarm_active = false;
static int inbox_pending = -1;   // 服务端收件箱待处理数（build_page 与角标共用）
bool top_clock = true, top_wx = true;
Telemetry latest;
char pinned_id[9] = "";
char fired_dates[16][11] = {};
uint32_t last_clock_ms = 0, last_render_ms = 0, alarm_started_ms = 0;
int idle_dim_secs = 60;              // 0=关闭；无触摸超时后进入息屏时钟
bool saver_active = false;
lv_obj_t* scr_saver = nullptr;
lv_obj_t* saver_time_lbl = nullptr, *saver_date_lbl = nullptr;
uint8_t saver_style = 0;
// 像素宠物（AI 卡右侧小只 + 宠物页大只），形象/表情/动作联动 AI 状态
struct PetParts {
    lv_obj_t *root=nullptr, *grp=nullptr, *head=nullptr, *body=nullptr;
    lv_obj_t *eye_l=nullptr, *eye_r=nullptr, *glow_l=nullptr, *glow_r=nullptr;
    lv_obj_t *mouth=nullptr, *beak=nullptr, *chest=nullptr;
    lv_obj_t *arml=nullptr, *armr=nullptr, *feetl=nullptr, *feetr=nullptr;
    lv_obj_t *earl=nullptr, *earr=nullptr, *tail=nullptr, *ant=nullptr, *badge=nullptr;
    int last_hash = -1;
    int base_x = 0, base_y = 0;
    int eye_y = 0;
    int sz = 1;
};
static PetParts pet_ai, pet_page;
// 相册轮播：当前文件序号 / 上次轮换时刻
static uint8_t sv_idx = 1, sv_count = 0;
static uint32_t sv_last_rot = 0;
static lv_obj_t* saver_img = nullptr;       // 样式4 全屏图
static lv_obj_t* saver_quote = nullptr;     // 样式3 诗句标签（轮换更新）
// 诗句轮换（样式3）：当前句序号 / 上次轮换时刻
static uint8_t poem_idx = 0;
static uint32_t poem_last_switch = 0;             // 0 极简 1 仪表 2 像素夜色 3 诗词短句
char saver_msg[64] = "代码写完了吗？起来喝口水";
uint32_t seen_hms = 0;

// ---- 覆盖面板：0 无 1 启动器 2 控制中心 ----
uint8_t overlay = 0;
lv_obj_t *scr_settings = nullptr;
lv_obj_t *launch_cells[12] = {};
char launch_ids[12][13];
uint8_t launch_count = 0;
lv_obj_t *ctrl_vol_lbl = nullptr, *ctrl_mute_lbl = nullptr, *ctrl_bright_lbl = nullptr;
// 设置页「正在播放」（NP 遥测驱动：歌名 / 歌手·进度 / 进度条）
lv_obj_t *np_title_lbl = nullptr, *np_sub_lbl = nullptr, *np_fill = nullptr;

lv_obj_t* label(lv_obj_t* parent, const char* value, int size, uint32_t color,
                int x, int y, int width, int height = -1) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_label_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, width);
    // 排版系统核心：标签给定固定高度后，LONG_DOT 会在水平方向截断（…），
    // 文字再长也不会换行压到下一行——重叠问题从机制上消灭。
    if (height > 0) lv_obj_set_height(obj, height);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    // 字号→字库映射（最小渲染字号=20px，杜绝小字糊化）：
    //   >=40 → cn_48(48px)  |  >=28 → cn_32(32px)
    //   >=20 → cn_24(24px)  |  <20  → cn_20(20px)
    // 所有 <=20px 的请求统一落到 cn_20，cn_16 已移除，无 16px 渲染路径。
    const lv_font_t* font = size >= 40 ? &cn_48 : size >= 28 ? &cn_32 :
                            size >= 20 ? &cn_24 : &cn_20;
    lv_obj_set_style_text_font(obj, font, 0);
    lv_label_set_text(obj, value);
    return obj;
}

void set_text(lv_obj_t* obj, const char* value) {
    if (!obj) return;
    const char* old = lv_label_get_text(obj);
    if (!old || strcmp(old, value)) lv_label_set_text(obj, value);
}

/** 字号请求 → 字库的渲染行高。固定高度必须按渲染字号给，否则 LVGL 会裁字。 */
int render_line_h(int size) {
    const lv_font_t* f = size >= 40 ? &cn_48 : size >= 28 ? &cn_32 : size >= 20 ? &cn_24 : &cn_20;
    return (int)f->line_height;
}

/** 单行标签：显式给一行行高，LONG_DOT 才会横向截断，杜绝折行压字。 */
lv_obj_t* label1(lv_obj_t* parent, const char* value, int size, uint32_t color,
                 int x, int y, int width) {
    return label(parent, value, size, color, x, y, width, render_line_h(size));
}

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_obj_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(card), 0);
    // 卡片不透明度：工作台「卡片不透明度」滑杆与主题画廊的 card_opa 都靠它生效。
    // 半透明卡片让背景图/渐变透出来，是主题体系里除色相外的第二个维度。
    lv_obj_set_style_bg_opa(obj, card_opa, 0);
    lv_obj_set_style_border_width(obj, 1, 0);
    // 卡片与背景亮度差过小时（深色主题尤甚），纯填充色差不足以让用户
    // 看出"哪里是内容、哪里是空白"。描边提供独立于填充色的边界线索。
    lv_obj_set_style_border_color(obj, lv_color_hex(card_edge), 0);
    lv_obj_set_style_radius(obj, 10, 0);
    lv_obj_set_style_pad_all(obj, 0, 0);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(obj, LV_OBJ_FLAG_GESTURE_BUBBLE);
    return obj;
}

// 按下态：加深底色提供即时反馈。无按下态变化时触摸"像没反应"。
static uint32_t shade(uint32_t color, int percent) {
    int r = (color >> 16) & 0xFF, g = (color >> 8) & 0xFF, b = color & 0xFF;
    r = constrain(r * (100 + percent) / 100, 0, 255);
    g = constrain(g * (100 + percent) / 100, 0, 255);
    b = constrain(b * (100 + percent) / 100, 0, 255);
    return (uint32_t(r) << 16) | (uint32_t(g) << 8) | uint32_t(b);
}

// ---------- 进度条 / 状态点（效果图构件） ----------
// 进度条轨道：低对比凹槽。填充条按百分比改宽度，prog<0（未知）时为 0 宽，
// 绝不拿假进度填充。
lv_obj_t* bar_track(lv_obj_t* parent, int x, int y, int w) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_obj_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, 4);
    lv_obj_set_style_bg_color(obj, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_opa(obj, 170, 0);
    lv_obj_set_style_radius(obj, 2, 0);
    lv_obj_set_style_border_width(obj, 0, 0);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    return obj;
}

lv_obj_t* bar_fill(lv_obj_t* track) {
    if (!track) return nullptr;
    lv_obj_t* obj = lv_obj_create(track);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, 0, 0);
    lv_obj_set_size(obj, 0, 4);
    lv_obj_set_style_radius(obj, 2, 0);
    lv_obj_set_style_border_width(obj, 0, 0);
    lv_obj_set_style_bg_opa(obj, LV_OPA_COVER, 0);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    return obj;
}

void bar_set(lv_obj_t* fill, lv_obj_t* track, int pct, uint32_t color) {
    if (!fill || !track) return;
    int w = (int)lv_obj_get_width(track);
    int fw = (pct <= 0 || w <= 0) ? 0 : w * constrain(pct, 0, 100) / 100;
    lv_obj_set_style_bg_color(fill, lv_color_hex(color), 0);
    lv_obj_set_style_bg_opa(fill, fw ? LV_OPA_COVER : LV_OPA_TRANSP, 0);
    lv_obj_set_size(fill, fw, 4);
}

lv_obj_t* dot_obj(lv_obj_t* parent, int x, int y, int d) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_obj_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, d, d);
    lv_obj_set_style_radius(obj, d / 2, 0);
    lv_obj_set_style_border_width(obj, 0, 0);
    lv_obj_set_style_bg_color(obj, lv_color_hex(sub_color), 0);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    return obj;
}

lv_obj_t* button(lv_obj_t* parent, const char* text, int x, int y, int w, int h,
                 lv_event_cb_t cb, void* user_data, uint32_t color = 0) {
    uint32_t base = color ? color : card;
    lv_obj_t* obj = box(parent, x, y, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(base), 0);
    lv_obj_add_flag(obj, LV_OBJ_FLAG_CLICKABLE);
    if (cb) lv_obj_add_event_cb(obj, cb, LV_EVENT_CLICKED, user_data);
    lv_obj_set_style_bg_color(obj, lv_color_hex(shade(base, -18)), LV_STATE_PRESSED);
    lv_obj_set_style_border_color(obj, lv_color_hex(text_color), LV_STATE_PRESSED);
    // 标签保持自适应高度，再用 LV_ALIGN_CENTER 整体居中——直接设 height 会让
    // LVGL 把文字画在标签顶部（LVGL 8 的 label 无垂直对齐样式），反而偏心。
    lv_obj_t* txt = label(obj, text, 20, color ? bg : text_color, 0, 0, w);
    lv_obj_set_style_text_align(txt, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(txt, LV_ALIGN_CENTER, 0, 0);
    return obj;
}

const char* weather_text(int code) {
    if (code == 0) return "晴";
    if (code <= 2) return "多云";
    if (code == 3) return "阴";
    if (code == 45 || code == 48) return "雾";
    if (code >= 51 && code <= 57) return "毛毛雨";
    if (code >= 61 && code <= 67) return "雨";
    if (code >= 71 && code <= 77) return "雪";
    if (code >= 80 && code <= 82) return "阵雨";
    if (code >= 85 && code <= 86) return "阵雪";
    if (code >= 95) return "雷暴";
    return "未知";
}

int task_count() {
    int n = 0;
    for (const AITask& task : latest.tasks) if (task.used) ++n;
    return n;
}

void render_all();

void switch_page(uint8_t target, bool animated) {
    if (target >= view_count || target == current_page) return;
    current_page = target;
    if (animated)
        lv_scr_load_anim(views[target].screen, LV_SCR_LOAD_ANIM_FADE_ON, 150, 0, false);
    else lv_scr_load(views[target].screen);
    char event[16];
    snprintf(event, sizeof(event), "PAGE%u", (unsigned)(target + 1));
    send_touch(event);
}

void page_click(lv_event_t* event) {
    uint8_t target = (uint8_t)(uintptr_t)lv_event_get_user_data(event);
    switch_page(target, false);
}

void open_overlay(uint8_t which);
void close_overlay();
void open_settings_cb(lv_event_t*) { open_overlay(1); }
void close_overlay_cb(lv_event_t*) { close_overlay(); }

// ---------- 覆盖面板开合（设置页 = 原启动器 + 控制中心 合并） ----------
void close_overlay() {
    if (!overlay) return;
    overlay = 0;
    lv_scr_load(views[current_page].screen);
}

void open_overlay(uint8_t which) {
    if (overlay == which || !scr_settings) return;
    overlay = which;
    lv_scr_load_anim(scr_settings, LV_SCR_LOAD_ANIM_FADE_ON, 150, 0, false);
    send_touch("SETTINGS_OPEN");
}

void settings_close_gesture(lv_event_t*) {
    lv_indev_t* input = lv_indev_get_act();
    if (!input) return;
    lv_dir_t direction = lv_indev_get_gesture_dir(input);
    if (direction == LV_DIR_TOP || direction == LV_DIR_BOTTOM) close_overlay();
}

// 页面手势：横滑切页；面板打开时竖滑收起。
// 原竖滑开启动器/控制中心的手势移除——入口统一为左下角 ⚙（避免误触发）。
void gesture(lv_event_t* event) {
    lv_indev_t* input = lv_indev_get_act();
    if (!input) return;
    lv_dir_t direction = lv_indev_get_gesture_dir(input);
    if (overlay) {   // 面板打开时：竖滑收起
        if (direction == LV_DIR_TOP || direction == LV_DIR_BOTTOM)
            close_overlay();
        return;
    }
    if (direction == LV_DIR_LEFT && current_page + 1 < view_count)
        switch_page(current_page + 1, true);
    else if (direction == LV_DIR_RIGHT && current_page > 0)
        switch_page(current_page - 1, true);
}

// ---------- 上行动作 ----------
void launch_app(lv_event_t* event) {
    uint8_t index = (uint8_t)(uintptr_t)lv_event_get_user_data(event);
    if (index < launch_count) {
        Serial.print(uplink_launch(launch_ids[index]));
        close_overlay();
    }
}

void send_key_cb(lv_event_t* event) {
    const char* action = (const char*)lv_event_get_user_data(event);
    Serial.print(uplink_key(action));
}

void bright_step(int delta) {
    int value = constrain(display_brightness_get() + delta, 5, 100);
    display_brightness(value);
    Serial.print(uplink_bright(value));
    if (ctrl_bright_lbl) {
        char text[16];
        snprintf(text, sizeof(text), "%d%%", value);
        set_text(ctrl_bright_lbl, text);
    }
}

void bright_down(lv_event_t*) { bright_step(-10); }
void bright_up(lv_event_t*) { bright_step(+10); }

void stop_alarm(lv_event_t*) {
    alarm_active = false;
    for (uint8_t p = 0; p < view_count; ++p)
        lv_obj_add_flag(views[p].alert, LV_OBJ_FLAG_HIDDEN);
    display_brightness(g_profile.bright);
}

void choose_task(lv_event_t* event) {
    WidgetView* view = static_cast<WidgetView*>(lv_event_get_user_data(event));
    lv_obj_t* target = lv_event_get_target(event);
    for (int i = 0; i < 5; ++i) {
        if (view->rows[i] == target) {
            uint8_t slot = view->row_slot[i];
            if (slot < 8 && latest.tasks[slot].used) {
                snprintf(pinned_id, sizeof(pinned_id), "%s", latest.tasks[slot].id);
                render_all();
            }
            break;
        }
    }
}

// ---------- 页面构建（竖屏） ----------
/* 工具页：顶部「最近完成」带 + 5 行等高会话（render 阶段按页面 filter 过滤）。
   行几何：高 66、间隔 4，从 y=94 到 440 正好铺满内容区；完成带 30px 在 60..90。
   全部页（filter 空）保留焦点卡 + 机器人，见 make_ai 主分支。 */
void make_weather_city(WidgetView& view, lv_obj_t* screen, const char* city);

void make_ai_tool_page(WidgetView& view, lv_obj_t* screen) {
    view.done_band = box(screen, 8, BODY_Y, 304, 30);
    view.root = view.done_band;      // ui_set_online 的离线描边以此为准
    view.band_lbl = label1(view.done_band, "", 20, sub_color, 10, 0, 284);
    for (int i = 0; i < 5; ++i) {
        lv_obj_t* row = box(screen, 8, 94 + 70 * i, 304, 66);
        lv_obj_set_style_bg_color(row, lv_color_hex(bg), 0);
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(row, choose_task, LV_EVENT_CLICKED, &view);
        view.rows[i] = row;
        view.row_dot[i] = dot_obj(row, 8, 10, 8);
        view.row_tool[i] = label(row, "", 20, text_color, 22, 4, 104, 26);
        view.row_info[i] = label(row, "", 20, sub_color, 126, 4, 170, 26);
        lv_obj_set_style_text_align(view.row_info[i], LV_TEXT_ALIGN_RIGHT, 0);
        view.row_bar[i] = bar_track(row, 8, 46, 268);
        view.row_fill[i] = bar_fill(view.row_bar[i]);
    }
}

void make_pet_compact(WidgetView& view, lv_obj_t* screen);

void pet_action_cb(lv_event_t* event);

void make_ai(WidgetView& view, lv_obj_t* screen, const char* filter) {
    strncpy(view.filter, filter ? filter : "", sizeof(view.filter) - 1);
    view.filter[sizeof(view.filter) - 1] = 0;
    if (view.filter[0]) { make_ai_tool_page(view, screen); return; }
    view.root = box(screen, 8, BODY_Y, 304, BODY_H);
    // 对齐 docs/screen-ui-sample「状态聚焦」效果图：
    //   头行  工具·会话号（左） + 进行中 N · 共 M（右）
    //   焦点  项目名 32px → 状态点 + 状态 20px → 运行信息 20px → 进度条
    //   其他  3 行会话（状态点 + 工具 + 项目·用时 + 进度条）
    view.extra[0] = label(view.root, "", 20, sub_color,  14,  10, 178, 26);
    view.ai_count = label(view.root, "", 20, sub_color, 192,  10,  98, 26);
    lv_obj_set_style_text_align(view.ai_count, LV_TEXT_ALIGN_RIGHT, 0);
    view.main     = label(view.root, "暂无会话", 32, text_color, 14,  42, 208, 42);
    view.ai_dot   = dot_obj(view.root, 14, 102, 10);
    view.sub      = label(view.root, "等待 AI 编辑器活动", 20, accent, 30,  96, 196, 28);
    view.extra[7] = label(view.root, "", 20, sub_color,  14, 134, 276, 26);
    view.ai_bar = bar_track(view.root, 14, 166, 276);
    view.ai_bar_fill = bar_fill(view.ai_bar);
    for (int i = 0; i < 3; ++i) {
        lv_obj_t* row = box(view.root, 10, 174 + 68 * i, 284, 62);
        lv_obj_set_style_bg_color(row, lv_color_hex(bg), 0);
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(row, choose_task, LV_EVENT_CLICKED, &view);
        view.rows[i] = row;
        view.row_dot[i] = dot_obj(row, 8, 10, 8);
        view.extra[i + 1] = label(row, "", 20, text_color, 22,  4, 104, 26);  // 行1 工具
        view.extra[i + 4] = label(row, "", 20, sub_color, 126,  4, 150, 26);  // 行1 右：项目·用时
        lv_obj_set_style_text_align(view.extra[i + 4], LV_TEXT_ALIGN_RIGHT, 0);
        view.row_bar[i] = bar_track(row, 8, 40, 268);
        view.row_fill[i] = bar_fill(view.row_bar[i]);
    }
    view.ai_overview = box(view.root, 10, 212, 284, 154);
    lv_obj_set_style_bg_color(view.ai_overview, lv_color_hex(bg), 0);
    label1(view.ai_overview, "电脑概览", 20, sub_color, 12, 10, 258);
    view.ai_metrics[0] = label1(view.ai_overview, "", 20, text_color, 12, 52, 258);
    view.ai_metrics[1] = label1(view.ai_overview, "", 20, text_color, 12, 98, 258);
    // 像素宠物（AI 卡右侧 64×80）：形象随 profile.pet.type 切换，
    // 眨眼/摇摆常驻动画，出错抖动、完成跳跃、等你弹「!」（见 render_ai）。
    make_pet_compact(view, screen);
}

// ---------- 像素宠物：绘制 / 表情 / 页面 ----------
struct PetScrims { int a, b, c; };

static lv_obj_t* pet_rect(lv_obj_t* parent, int x, int y, int w, int h,
                          int radius, uint32_t color, int border = 0,
                          uint32_t border_color = 0) {
    lv_obj_t* o = lv_obj_create(parent);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_radius(o, radius, 0);
    lv_obj_set_style_bg_color(o, lv_color_hex(color), 0);
    lv_obj_set_style_border_width(o, border, 0);
    if (border) lv_obj_set_style_border_color(o, lv_color_hex(border_color), 0);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(o, LV_OBJ_FLAG_CLICKABLE);
    return o;
}

/* 画一只宠物：type = robot / cat / chick；sz=1 为 AI 卡小只（64×80），
   sz=2 为宠物页大只（128×160）。所有部件挂进 p.grp，摇摆动画作用其上，
   抖动/跳跃动画作用在 p.root——两组动画互不打架。 */
/* 十六进制混色：wa = a 的权重（0-255）。宠物身体色 = 主题强调色×背景，
   让猫/小鸡跟随主题色系（机器人保留钢蓝本体，描边三者都用强调色）。 */
static uint32_t mix_hex(uint32_t a, uint32_t b, uint8_t wa) {
    uint32_t wb = 255 - wa;
    return (((a >> 16 & 0xFF) * wa + (b >> 16 & 0xFF) * wb) / 255 << 16)
         | (((a >> 8 & 0xFF) * wa + (b >> 8 & 0xFF) * wb) / 255 << 8)
         | (((a & 0xFF) * wa + (b & 0xFF) * wb) / 255);
}

static void draw_pet(PetParts& p, lv_obj_t* parent, const char* type,
                     int x, int y, int sz) {
    if (p.root) lv_obj_del(p.root);   // 重建安全（旧动画随对象删除）
    p = PetParts();
    p.base_x = x; p.base_y = y; p.sz = sz;
    bool is_cat = !strcmp(type, "cat");
    bool is_chick = !strcmp(type, "chick");
    bool is_robot = !is_cat && !is_chick;
    // 猫=强调色浓（62%）体积感强，小鸡=淡（38%）更娇嫩；描边统一主题强调色
    uint32_t body_c = is_cat ? mix_hex(accent, bg, 158)
                    : is_chick ? mix_hex(accent, bg, 97)
                    : 0x46689C;
    uint32_t edge_c = accent;
    uint32_t limb_c = body_c;

    p.root = lv_obj_create(parent);
    lv_obj_set_pos(p.root, x, y);
    // lv_obj_create() 会继承主题的默认内边距。这里是纯绘图坐标容器，若不清零，
    // root + grp 两层 padding 会把整只宠物向右下推两次，并在容器边缘裁掉身体。
    // 右侧额外 4px 给“嗨/饿/渴/累”等徽标留安全区；宠物本体仍是 64×80。
    lv_obj_set_size(p.root, 68 * sz, 80 * sz);
    lv_obj_set_style_bg_opa(p.root, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(p.root, 0, 0);
    lv_obj_set_style_pad_all(p.root, 0, 0);
    lv_obj_clear_flag(p.root, LV_OBJ_FLAG_SCROLLABLE);
    p.grp = lv_obj_create(p.root);
    lv_obj_set_pos(p.grp, 0, 0);
    lv_obj_set_size(p.grp, 68 * sz, 80 * sz);
    lv_obj_set_style_bg_opa(p.grp, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(p.grp, 0, 0);
    lv_obj_set_style_pad_all(p.grp, 0, 0);
    lv_obj_clear_flag(p.grp, LV_OBJ_FLAG_SCROLLABLE);

    if (is_robot) p.ant = pet_rect(p.grp, 29*sz, 0, 6*sz, 6*sz, 3*sz, COL_OK);
    if (is_cat) {
        p.earl = pet_rect(p.grp, 12*sz, 0, 10*sz, 9*sz, 3*sz, body_c, sz, edge_c);
        p.earr = pet_rect(p.grp, 42*sz, 0, 10*sz, 9*sz, 3*sz, body_c, sz, edge_c);
    }
    int head_y = is_cat ? 7*sz : 5*sz;
    p.head = pet_rect(p.grp, 10*sz, head_y, 44*sz, 28*sz, 8*sz, body_c, sz, edge_c);
    p.glow_l = pet_rect(p.grp, 16*sz, (head_y+5)*sz, 14*sz, 14*sz, 7*sz, accent);
    p.glow_r = pet_rect(p.grp, 34*sz, (head_y+5)*sz, 14*sz, 14*sz, 7*sz, accent);
    lv_obj_set_style_bg_opa(p.glow_l, LV_OPA_50, 0);
    lv_obj_set_style_bg_opa(p.glow_r, LV_OPA_50, 0);
    p.eye_l = pet_rect(p.grp, 19*sz, (head_y+8)*sz, 8*sz, 8*sz, 4*sz, text_color);
    p.eye_r = pet_rect(p.grp, 37*sz, (head_y+8)*sz, 8*sz, 8*sz, 4*sz, text_color);
    p.eye_y = (head_y + 8) * sz;
    if (is_chick) p.beak = pet_rect(p.grp, 26*sz, (head_y+17)*sz, 12*sz, 6*sz, 2*sz, 0xFFB84D);
    else p.mouth = pet_rect(p.grp, 24*sz, (head_y+19)*sz, 16*sz, 3*sz, 1*sz, accent);
    int body_y = head_y + 30*sz;
    p.body = pet_rect(p.grp, 8*sz, body_y, 48*sz, 26*sz, 8*sz, body_c, sz, edge_c);
    p.chest = pet_rect(p.grp, 28*sz, body_y + 8*sz, 8*sz, 5*sz, 2*sz,
                       is_robot ? accent : edge_c);
    p.arml = pet_rect(p.grp, 0, body_y + 3*sz, 7*sz, 16*sz, 3*sz, limb_c);
    p.armr = pet_rect(p.grp, 57*sz, body_y + 3*sz, 7*sz, 16*sz, 3*sz, limb_c);
    if (is_cat) p.tail = pet_rect(p.grp, 58*sz, body_y - 12*sz, 6*sz, 22*sz, 3*sz, limb_c);
    p.feetl = pet_rect(p.grp, 16*sz, body_y + 28*sz, 16*sz, 7*sz, 3*sz, limb_c);
    p.feetr = pet_rect(p.grp, 34*sz, body_y + 28*sz, 16*sz, 7*sz, 3*sz, limb_c);
    p.badge = lv_label_create(p.grp);
    lv_obj_set_pos(p.badge, 44*sz, 0);
    lv_obj_set_style_text_color(p.badge, lv_color_hex(COL_WARN), 0);
    lv_obj_set_style_text_font(p.badge, &cn_24, 0);
    set_text(p.badge, "");

    // 常驻动画：摇摆（grp）+ 眨眼（双眼）+ 天线呼吸（机器人）
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, p.grp);
    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_y);
    lv_anim_set_values(&a, 0, 3 * sz);
    lv_anim_set_time(&a, 700);
    lv_anim_set_playback_time(&a, 700);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);
    for (lv_obj_t* eye : {p.eye_l, p.eye_r}) {
        lv_anim_init(&a);
        lv_anim_set_var(&a, eye);
        lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_height);
        lv_anim_set_values(&a, 8 * sz, 2 * sz);
        lv_anim_set_time(&a, 130);
        lv_anim_set_playback_time(&a, 130);
        lv_anim_set_repeat_delay(&a, 2400);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }
    if (is_robot && p.ant) {
        lv_anim_init(&a);
        lv_anim_set_var(&a, p.ant);
        lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_height);
        lv_anim_set_values(&a, 6 * sz, 2 * sz);
        lv_anim_set_time(&a, 800);
        lv_anim_set_playback_time(&a, 800);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }
}

/* —— 三层宠物状态机（docs/AI_PIXEL_PET_DESIGN.md §3/§6）——
   活动层：WORKING/THINKING/IDLE（AI 会话状态驱动）
   时间层：SLEEPY 22:00-01:00 软覆盖（AI 在干活则「陪你熬夜」不犯困，须持续 10s）；
          SLEEPING 01:00-06:00 硬覆盖
   场景层：ALERT > CELEBRATE > PLAYING > GREETING 瞬时覆盖，到期回基态 */
enum PetLayer {
    PL_IDLE = 0, PL_THINKING, PL_WORKING, PL_SLEEPY, PL_SLEEPING,
    PL_GREETING, PL_CELEBRATE, PL_PLAYING, PL_ALERT,
};
static PetLayer pet_scene = PL_IDLE;      // 场景层当前值（pet_scene_until>0 时有效）
static uint32_t pet_scene_until = 0;      // 场景层到期时刻（millis）
static uint32_t pet_calm_since = 0;       // 活动层离开 WORKING 的时刻（SLEEPY 迟滞）
static bool pet_greeted = false;          // 开机问候只来一次

static void pet_trigger_scene(PetLayer l, uint32_t ms) {
    pet_scene = l;
    pet_scene_until = millis() + ms;
}


static int pet_hour() {
    if (!latest.clock_hms) return -1;         // 无时钟不进时间层（保持活动层）
    return (int)(latest.clock_hms / 10000) % 24;   // HHMMSS -> HH
}

static PetLayer pet_arbitrate(const char* st, uint32_t now) {
    if (pet_scene_until && (int32_t)(now - pet_scene_until) >= 0) pet_scene_until = 0;
    // 事件驱动场景：出错/完成沿触发瞬时状态（而非全程跟随，6s/4s 后回基态）
    static char prev_st[12] = "";
    if (strncmp(prev_st, st, 11) != 0) {
        strncpy(prev_st, st, 11);
        prev_st[11] = 0;
        if (!strcmp(st, "ERROR")) pet_trigger_scene(PL_ALERT, 6000);
        else if (!strcmp(st, "DONE")) pet_trigger_scene(PL_CELEBRATE, 4000);
    }
    // 开机问候（凌晨 1-6 点不问候，保持安静）
    if (!pet_greeted && have_data) {
        pet_greeted = true;
        int gh = pet_hour();
        if (!(gh >= 1 && gh < 6)) pet_trigger_scene(PL_GREETING, 3000);
    }
    // 整点/半点问候（§6.3，可关）：每个半小时槽只触发一次
    static int pet_last_greet_slot = -1;
    if (g_profile.pet_greet && !pet_scene_until && latest.clock_hms) {
        int gh = pet_hour();
        if (!(gh >= 1 && gh < 6)) {
            int mm = (int)((latest.clock_hms / 100) % 100);
            int slot = gh * 2 + mm / 30;
            if (slot != pet_last_greet_slot) {
                pet_last_greet_slot = slot;
                pet_trigger_scene(PL_GREETING, 3000);
            }
        }
    }
    if (pet_scene_until) return pet_scene;

    int h = pet_hour();
    bool working = !strcmp(st, "GENERATING") || !strcmp(st, "TOOL") ||
                   !strcmp(st, "BUILD");
    // 凌晨硬覆盖可在设置关闭（pet_sleep=0 时凌晨并入犯困软规则，只提示不强睡）
    if (h >= 1 && h < 6 && g_profile.pet_sleep) return PL_SLEEPING;
    if ((h >= 22 || h < 1) || (h >= 1 && h < 6)) {       // 软覆盖：陪你熬夜
        if (!working) {
            if (!pet_calm_since) pet_calm_since = now;
            if (now - pet_calm_since >= 10000) return PL_SLEEPY;
        } else pet_calm_since = 0;
    } else pet_calm_since = 0;

    // 活动层（场景过期后的 ERROR/DONE 仍以活动层形式兜底显示）
    if (!strcmp(st, "ERROR")) return PL_ALERT;
    if (!strcmp(st, "DONE")) return PL_CELEBRATE;
    if (!strcmp(st, "WAIT_INPUT") || !strcmp(st, "ASK")) return PL_THINKING;
    if (working) return PL_WORKING;
    return PL_IDLE;
}

/* 眼睛统一入口：眨眼动画独占高度（先删后建，避免双动画打架），dy=视线偏移 */
static void pet_set_eyes(PetParts& p, int h8, bool blink, int dy) {
    for (lv_obj_t* eye : {p.eye_l, p.eye_r}) {
        if (!eye) continue;
        lv_anim_del(eye, (lv_anim_exec_xcb_t)lv_obj_set_height);
        lv_obj_set_height(eye, p.sz * h8);
        lv_obj_set_y(eye, p.eye_y + p.sz * dy);
        if (blink) {
            lv_anim_t a;
            lv_anim_init(&a);
            lv_anim_set_var(&a, eye);
            lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_height);
            lv_anim_set_values(&a, p.sz * h8, p.sz * 2);
            lv_anim_set_time(&a, 130);
            lv_anim_set_playback_time(&a, 130);
            lv_anim_set_repeat_delay(&a, 2400);
            lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
            lv_anim_start(&a);
        }
    }
}

/* 身体摇摆：按状态换节奏（睡觉呼吸最慢最浅） */
static void pet_set_bob(PetParts& p, int period, int amp) {
    lv_anim_del(p.grp, (lv_anim_exec_xcb_t)lv_obj_set_y);
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, p.grp);
    lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_y);
    lv_anim_set_values(&a, 0, p.sz * amp);
    lv_anim_set_time(&a, period);
    lv_anim_set_playback_time(&a, period);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);
}

/* 表情与动作：三层仲裁出的唯一状态 → 一套视觉参数（眼睛/嘴/徽标/节奏/动作） */
static void pet_apply_expression(PetParts& p, const char* st,
                                 int hunger, int thirst, int energy) {
    if (!p.body) return;
    PetLayer L = pet_arbitrate(st, millis());

    int eh = 8, dy = 0, bob_ms = 700, bob_amp = 3, mouth_w = 12;
    bool blink = true;
    const char* badge = "";
    uint32_t badge_col = COL_WARN, edge_col = accent, eye_col = text_color,
             mouth_col = accent;
    switch (L) {
        case PL_WORKING:                                   break;
        case PL_THINKING:  eh = 6; dy = -2; badge = "?";   break;
        case PL_IDLE:      eh = 5; bob_ms = 1100;          break;
        case PL_SLEEPY:    eh = 3; blink = false; bob_ms = 1600; bob_amp = 2;
                           badge = "Z";                    break;
        case PL_SLEEPING:  eh = 2; blink = false; bob_ms = 1800; bob_amp = 2;
                           badge = "z";                    break;
        case PL_GREETING:  badge = "嗨"; mouth_w = 16;      break;
        case PL_CELEBRATE: eh = 3; mouth_w = 18; mouth_col = COL_OK; break;
        case PL_PLAYING:   eh = 4; mouth_w = 14;           break;
        case PL_ALERT:     badge = "×"; badge_col = COL_ERR; edge_col = COL_ERR;
                           eye_col = COL_ERR; mouth_w = 20;
                           mouth_col = COL_ERR;            break;
    }
    // 饿渴累徽标：只在活动层显示（睡觉/场景进行时不多嘴）
    if (!badge[0] && (L == PL_IDLE || L == PL_THINKING || L == PL_WORKING)) {
        if (hunger < 20) badge = "饿";
        else if (thirst < 20) badge = "渴";
        else if (energy < 20) badge = "累";
    }

    int hash = (int)L * 31 + hunger + thirst + energy;
    if (hash == p.last_hash) return;
    p.last_hash = hash;

    pet_set_eyes(p, eh, blink, dy);
    pet_set_bob(p, bob_ms, bob_amp);
    lv_obj_set_style_border_color(p.body, lv_color_hex(edge_col), 0);
    lv_obj_set_style_bg_color(p.eye_l, lv_color_hex(eye_col), 0);
    lv_obj_set_style_bg_color(p.eye_r, lv_color_hex(eye_col), 0);
    if (p.badge) {
        set_text(p.badge, badge);
        lv_obj_set_style_text_color(p.badge, lv_color_hex(badge_col), 0);
    }
    if (p.mouth) {
        lv_obj_set_width(p.mouth, p.sz * mouth_w);
        lv_obj_set_style_bg_color(p.mouth, lv_color_hex(mouth_col), 0);
    }

    // 一次性动作：出错抖动 / 庆祝与问候起跳（摇摆动画在 grp 上，不冲突）
    if (L == PL_ALERT || L == PL_CELEBRATE || L == PL_GREETING) {
        lv_anim_del(p.root, (lv_anim_exec_xcb_t)lv_obj_set_x);
        lv_anim_del(p.root, (lv_anim_exec_xcb_t)lv_obj_set_y);
        lv_obj_set_pos(p.root, p.base_x, p.base_y);
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, p.root);
        if (L == PL_ALERT) {
            lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_x);
            lv_anim_set_values(&a, p.base_x, p.base_x + 3);
            lv_anim_set_time(&a, 60);
            lv_anim_set_playback_time(&a, 60);
            lv_anim_set_repeat_count(&a, 5);
        } else {
            lv_anim_set_exec_cb(&a, (lv_anim_exec_xcb_t)lv_obj_set_y);
            lv_anim_set_values(&a, p.base_y, p.base_y - 10);
            lv_anim_set_time(&a, 200);
            lv_anim_set_playback_time(&a, 160);
            lv_anim_set_repeat_count(&a, L == PL_GREETING ? 1 : 2);
        }
        lv_anim_start(&a);
    }
}

void make_pet_compact(WidgetView& view, lv_obj_t* screen) {
    (void)screen;
    draw_pet(pet_ai, view.root, g_profile.pet_type, 229, 42, 1);
}

void make_pet_page(WidgetView& view, lv_obj_t* screen) {
    (void)view;
    draw_pet(pet_page, screen, g_profile.pet_type, 96, 66, 2);
    // 三条状态条（饱食/水分/活力），PS 遥测实时驱动
    static const char* names[3] = {"饱食", "水分", "活力"};
    for (int i = 0; i < 3; ++i) {
        label1(screen, names[i], 20, sub_color, 24, 240 + i * 30, 60);
        lv_obj_t* track = pet_rect(screen, 84, 244 + i * 30, 170, 10, 5, 0x0E1B26);
        view.pm_fill[i] = pet_rect(track, 0, 0, 170, 10, 5, accent);
        view.pm_val[i] = label(screen, "--", 20, text_color, 262, 240 + i * 30, 44);
    }
    button(screen, "喂食", 12, 344, 92, 44, pet_action_cb, (void*)"feed");
    button(screen, "喝水", 114, 344, 92, 44, pet_action_cb, (void*)"water");
    button(screen, "运动", 216, 344, 92, 44, pet_action_cb, (void*)"play");
    label1(screen, "状态保存在电脑，互动立即生效", 20, sub_color, 10, 396, 300);
}

void render_pet_page(WidgetView& view) {
    if (!view.pm_fill[0]) return;
    int vals[3] = {latest.pet_h, latest.pet_t, latest.pet_e};
    for (int i = 0; i < 3; ++i) {
        if (view.pm_fill[i])
            lv_obj_set_width(view.pm_fill[i], 170 * constrain(vals[i], 0, 100) / 100);
        if (view.pm_val[i]) {
            char line[8];
            snprintf(line, sizeof(line), "%d", vals[i]);
            set_text(view.pm_val[i], line);
        }
    }
    const char* st = latest.tasks[0].used ? latest.tasks[0].state : "IDLE";
    pet_apply_expression(pet_page, st, latest.pet_h, latest.pet_t, latest.pet_e);
}

void pet_action_cb(lv_event_t* event) {
    Serial.print(uplink_pet((const char*)lv_event_get_user_data(event)));
}

void make_hardware(WidgetView& view, lv_obj_t* screen) {
    // 上两行四卡（处理器/内存/磁盘/网络）压到 140 高，底部腾出整宽温度风扇卡：
    // 电脑页数据面持续变多（CPU/GPU 温度、风扇转速），三列并排一眼可读。
    // 无数据源的列显示 "--"（诚实显示：绝不渲染 0 假值）。
    const char* titles[4] = {"处理器", "内存", "磁盘", "网络"};
    for (int i = 0; i < 4; ++i) {
        lv_obj_t* panel = box(screen, 8 + (i % 2) * 156, BODY_Y + (i / 2) * 148, 148, 140);
        label(panel, titles[i], 20, sub_color, 12, 18, 124);
        view.extra[i * 2] = label(panel, "--", 30, text_color, 12, 46, 124, 40);
        view.hw_bar[i] = bar_track(panel, 12, 98, 124);
        view.hw_fill[i] = bar_fill(view.hw_bar[i]);
        // 卡片说明始终限制为单行；窄卡上再长的实时数值也不能挤到下一行。
        view.extra[i * 2 + 1] = label1(panel, "", 20, sub_color, 12, 114, 124);
    }
    // 温度与风扇：三列并排（CPU 温度 / GPU 温度 / 风扇转速）。
    // 风扇列加宽到 104 且单位上移进标题——"5305rpm" 7 字符在 88px@28px
    // 下被 LONG_DOT 截成 "529..."（真机实拍确认）；纯数字最大 5 位 85px 稳放。
    lv_obj_t* panel = box(screen, 8, BODY_Y + 296, 304, 84);
    const char* caps2[3] = {"CPU", "GPU", "风扇 rpm"};
    const int col_x[3] = {12, 104, 192};
    const int col_w[3] = {80, 80, 104};
    for (int i = 0; i < 3; ++i) {
        label(panel, caps2[i], 20, sub_color, col_x[i], 12, col_w[i]);
        view.hw_temp[i] = label(panel, "--", 28, text_color, col_x[i], 38, col_w[i], 32);
    }
}

void make_generic(WidgetView& view, lv_obj_t* screen, int x, int y, int w, int h) {
    view.root = box(screen, x, y, w, h);
    view.card_w = (uint16_t)w;
    const char* title = "";
    switch (view.kind) {
    case W_CLOCK: title = "本地时间"; break;
    case W_ENVIRONMENT: title = "桌面环境"; break;
    case W_REMINDERS: title = "提醒列表"; break;
    case W_CALENDAR: title = "今日日历"; break;
    case W_ALARM: title = "闹钟"; break;
    case W_WEATHER: title = "天气"; break;
    case W_COUNTDOWN: title = "倒计时"; break;
    case W_AI_COST: title = "AI 用量"; break;
    default: break;
    }
    // 每行都给「恰好一行」的高度：LVGL 的 LONG_DOT 只在高度固定时横向截断，
    // 高度自适应则会把长文本折行压到下一行（实机配色核验 §3.2 记录的
    // 「16:12:27 被折成两行、2026-10- 后被截断」即由此而来）。
    label1(view.root, title, 20, sub_color, 12, 12, w - 24);
    // compact 阈值随字号上调：20px 正文需要约 76px 才不挤，90 太贴近。
    bool compact = h < 110;
    if (view.kind == W_CLOCK && !compact && w >= 280) {
        // 320px 竖屏中，八位时分秒与右侧日期并排时使用 32px，避免截断。
        view.main = label1(view.root, "--:--:--", 32, text_color, 12, 50, w - 112);
        view.extra[6] = label(view.root, "", 24, sub_color, w - 96, 50, 84);
        lv_obj_set_style_text_align(view.extra[6], LV_TEXT_ALIGN_RIGHT, 0);
        view.extra[7] = label(view.root, "", 20, accent, w - 96, 86, 84);
        lv_obj_set_style_text_align(view.extra[7], LV_TEXT_ALIGN_RIGHT, 0);
        return;
    }
    view.main = label1(view.root, "--", compact ? 20 : 28, text_color,
                       12, compact ? 44 : 68, w - 24);
    if (!compact) view.sub = label1(view.root, "", 20, sub_color, 12, h - 40, w - 24);
}

// ---------- 覆盖面板构建 ----------
// ---------- 设置页（原启动器 + 控制中心 合并；内容超高可滚动） ----------
void build_settings() {
    scr_settings = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(scr_settings, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_grad_color(scr_settings, lv_color_hex(grad), 0);
    lv_obj_set_style_bg_grad_dir(scr_settings, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_width(scr_settings, 0, 0);
    lv_obj_clear_flag(scr_settings, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(scr_settings, settings_close_gesture, LV_EVENT_GESTURE, nullptr);

    // 固定头：标题 + 收起按钮
    label(scr_settings, "设置", 20, text_color, 10, HEAD_Y, 120);
    lv_obj_t* close_btn = button(scr_settings, "收起", 260, HEAD_Y - 4, 52, 26,
                                 close_overlay_cb, nullptr);
    (void)close_btn;

    // 可滚动内容区（快捷应用 + 音量/媒体 + 亮度）
    lv_obj_t* page = lv_obj_create(scr_settings);
    lv_obj_set_pos(page, 0, 36);
    lv_obj_set_size(page, 320, SCR_H - 36);
    lv_obj_set_style_bg_opa(page, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(page, 0, 0);
    lv_obj_set_style_pad_all(page, 0, 0);
    // lv_obj 默认可滚动：子对象超出 444px 高度即可上下滑

    // ---- 快捷应用（原启动器）----
    label(page, "快捷应用", 20, sub_color, 10, 8, 160);
    launch_count = 0;
    for (uint8_t i = 0; i < g_profile.app_count && launch_count < 12; ++i) {
        const AppEntry& app = g_profile.apps[i];
        uint8_t row = launch_count / 3, col = launch_count % 3;
        int x = 8 + col * 102, y = 34 + row * 68;
        lv_obj_t* cell = button(page, app.name, x, y, 96, 60,
                                launch_app, (void*)(uintptr_t)launch_count);
        (void)cell;
        launch_cells[launch_count] = cell;
        snprintf(launch_ids[launch_count], 13, "%s", app.id);
        ++launch_count;
    }
    if (!launch_count) {
        // 空态一行带过（整页还有音量/媒体要放，不再用大卡片占位）
        // ⚠️ 文案用字必须在字库内（cn_20/24 全 GB2312 覆盖，安全）
        label1(page, "暂无快捷应用", 20, sub_color, 10, 40, 300);
    }
    int y = 34 + ((launch_count + 2) / 3) * 68 + 6;
    if (!launch_count) y = 34 + 36 + 6;

    // ---- 音量 / 媒体（原控制中心，紧凑三行）----
    label(page, "电脑音量", 20, sub_color, 10, y, 160);
    button(page, "-", 10, y + 26, 64, 40, send_key_cb, (void*)"VOL-");
    ctrl_vol_lbl = label(page, "--", 24, text_color, 84, y + 34, 92);
    lv_obj_set_style_text_align(ctrl_vol_lbl, LV_TEXT_ALIGN_CENTER, 0);
    button(page, "+", 178, y + 26, 64, 40, send_key_cb, (void*)"VOL+");
    ctrl_mute_lbl = button(page, "静音", 250, y + 26, 62, 40, send_key_cb, (void*)"MUTE");
    y += 78;

    button(page, "上一首", 10, y, 88, 40, send_key_cb, (void*)"PREV");
    button(page, "播放暂停", 104, y, 96, 40, send_key_cb, (void*)"PLAY", accent);
    button(page, "下一首", 206, y, 88, 40, send_key_cb, (void*)"NEXT");
    y += 52;

    label(page, "屏幕亮度", 20, sub_color, 10, y, 160);
    button(page, "-", 10, y + 26, 64, 40, bright_down, nullptr);
    ctrl_bright_lbl = label(page, "90%", 24, text_color, 84, y + 34, 92);
    lv_obj_set_style_text_align(ctrl_bright_lbl, LV_TEXT_ALIGN_CENTER, 0);
    button(page, "+", 178, y + 26, 64, 40, bright_up, nullptr);
    y += 78;

    label1(page, "音量与媒体由电脑执行", 20, sub_color, 10, y, 300);
}

void build_page(uint8_t index) {
    const PageConfig& config = g_profile.pages[index];
    PageView& page = views[index];
    page.screen = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(page.screen, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_grad_color(page.screen, lv_color_hex(grad), 0);
    lv_obj_set_style_bg_grad_dir(page.screen, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_width(page.screen, 0, 0);
    lv_obj_clear_flag(page.screen, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(page.screen, gesture, LV_EVENT_GESTURE, nullptr);
    Serial.printf("[BG] build_page ready=%d\n", (int)bgimg_ready());
    if (bgimg_ready()) {                    // 背景图层（最底层）
        lv_obj_t* bgimg = lv_img_create(page.screen);
        lv_img_set_src(bgimg, bgimg_source());
        lv_obj_set_pos(bgimg, 0, 0);
        lv_obj_move_background(bgimg);
        // 顶栏遮罩：主题文字按深色底校验，用户换浅色背景图后顶栏的白/浅色
        // 文字会糊进背景（实测墨韵背景）。垫一条上深下浅的黑色渐隐带
        //（LVGL 8 没有逐端透明度，用三段递减 opa 模拟），只在有背景图时绘制；
        // 纯色/渐变主题保持原貌。
        static const int SCRIM_OPA[3] = {150, 95, 45};
        for (int i = 0; i < 3; ++i) {
            lv_obj_t* scrim = lv_obj_create(page.screen);
            lv_obj_set_pos(scrim, 0, i * 15);
            lv_obj_set_size(scrim, SCR_W, 15);
            lv_obj_set_style_bg_color(scrim, lv_color_hex(0x000000), 0);
            lv_obj_set_style_bg_opa(scrim, SCRIM_OPA[i], 0);
            lv_obj_set_style_border_width(scrim, 0, 0);
            lv_obj_set_style_radius(scrim, 0, 0);
            lv_obj_clear_flag(scrim, LV_OBJ_FLAG_SCROLLABLE);
            lv_obj_clear_flag(scrim, LV_OBJ_FLAG_CLICKABLE);
            lv_obj_add_flag(scrim, LV_OBJ_FLAG_GESTURE_BUBBLE);
        }
    }
    // 离线状态条：顶部 4px 红色条。电脑掉线时整屏给出明确"离线"信号
    // （与顶栏状态点、标题栏红色"电脑离线"文字形成三重冗余提示）。
    page.offbar = lv_obj_create(page.screen);
    lv_obj_set_pos(page.offbar, 0, 0);
    lv_obj_set_size(page.offbar, SCR_W, 4);
    lv_obj_set_style_bg_color(page.offbar, lv_color_hex(COL_ERR), 0);
    lv_obj_set_style_border_width(page.offbar, 0, 0);
    lv_obj_clear_flag(page.offbar, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_clear_flag(page.offbar, LV_OBJ_FLAG_CLICKABLE);   // 4px 装饰条不吞点击
    lv_obj_add_flag(page.offbar, LV_OBJ_FLAG_GESTURE_BUBBLE);
    if (online) lv_obj_add_flag(page.offbar, LV_OBJ_FLAG_HIDDEN);
    else lv_obj_clear_flag(page.offbar, LV_OBJ_FLAG_HIDDEN);
    // 状态栏（对齐效果图）：左=页面冠名 · 中=短时钟 · 右=天气 + 电池% + 状态点。
    // 电池缺 BAT 遥测时隐藏百分比，绝不显示占位假值。
    // 标题宽度要能让出时钟的位置。时钟改成 HH:MM:SS 后需要约 96px
    // （cn_24 下 "00:00:00" 实测 96.4px），所以标题从 112 收到 100——
    // 4 个汉字的标题（如「帽子看板」= 96px）仍然完整显示，更长的照旧截断。
    label1(page.screen, config.title, 20, text_color, 8, HEAD_Y,
           top_clock ? 100 : (top_wx ? 174 : 190));
    if (top_clock) {
        // 中置时钟：110..210 居中于屏幕中线，96.4px 的时间串在 100px 盒子里
        // 有 3.6px 余量（盒宽不足会让 LVGL 把时间截成 "12:34:…"）。
        page.topclock = label1(page.screen, "", 20, accent, 112, HEAD_Y, 100);
        lv_obj_set_style_text_align(page.topclock, LV_TEXT_ALIGN_CENTER, 0);
    }
    if (top_wx) {
        // 天气右对齐收窄到 76px 并左移，给时钟腾位；「毛毛雨」3 字 72px 仍放得下。
        page.topwx = label1(page.screen, "", 20, sub_color, 216, HEAD_Y, 76);
        lv_obj_set_style_text_align(page.topwx, LV_TEXT_ALIGN_RIGHT, 0);
        set_text(page.topwx, latest.wx_valid ? weather_text(latest.wx_code) : "");
    }
    if (top_clock || top_wx) {
        page.topdot = lv_obj_create(page.screen);
        lv_obj_set_pos(page.topdot, 300, HEAD_Y + 6);
        lv_obj_set_size(page.topdot, 12, 12);
        lv_obj_set_style_radius(page.topdot, 6, 0);
        lv_obj_set_style_border_width(page.topdot, 0, 0);
        lv_obj_set_style_bg_color(page.topdot,
            lv_color_hex(online ? COL_OK : COL_ERR), 0);
        page.conn = nullptr;
    } else {
        page.topdot = nullptr;
        page.conn = label(page.screen, online ? "在线" : "电脑离线", 20,
                          online ? 0x34D399 : COL_ERR, 210, HEAD_Y + 2, 100);
    }

    // 页码按钮（底部居中）。
    // 原实现两处不一致：总宽按 6px 间距算、绘制按 40px 步进，导致圆点组
    // 实际偏左且页数越多偏移越大。同时 34x24 的热区远低于 44x44 可触下限。
    // 现改为：视觉 32x22，但热区扩到 44x44（居中放置），间距统一 10px。
    uint8_t n = view_count;
    const int DOT_W = 32, DOT_H = 22, HIT = 44, GAP = 10;
    int total = n * DOT_W + (n - 1) * GAP;
    int x0 = (SCR_W - total) / 2;
    // 左下角单 ⚙：打开设置页（快捷应用 + 音量/媒体 + 亮度 合并）。
    // 原左 ☰ 右 ⚙ 双按钮按需求合并；右侧留白给页码呼吸。
    button(page.screen, "设", 4, FOOT_Y - 2, 40, 28, open_settings_cb, nullptr);
    // 收件箱角标：页脚右下（6 页时页码点最多到 x≈281，角标 x284 宽 32 不相撞）。
    // 文本由 ui_set_inbox 按服务端 INBX= 维护；-1/0 隐藏。
    page.inbox_lbl = label(page.screen, "", 20, COL_WARN, 284, FOOT_Y - 2, 32, 26);
    if (inbox_pending > 0) {
        char t[12];
        snprintf(t, sizeof(t), "%d件", inbox_pending);
        set_text(page.inbox_lbl, t);
    } else {
        lv_obj_add_flag(page.inbox_lbl, LV_OBJ_FLAG_HIDDEN);
    }
    for (uint8_t k = 0; k < n; ++k) {
        int cx = x0 + k * (DOT_W + GAP);
        // 透明外扩热区：保证每颗圆点实际可触面积 >= 44x44。
        lv_obj_t* hit = lv_obj_create(page.screen);
        lv_obj_set_size(hit, HIT, HIT);
        lv_obj_set_pos(hit, cx + (DOT_W - HIT) / 2, FOOT_Y - (HIT - DOT_H) / 2);
        lv_obj_set_style_bg_opa(hit, LV_OPA_TRANSP, 0);
        lv_obj_set_style_border_width(hit, 0, 0);
        lv_obj_clear_flag(hit, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(hit, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_flag(hit, LV_OBJ_FLAG_GESTURE_BUBBLE);
        lv_obj_add_event_cb(hit, page_click, LV_EVENT_CLICKED, (void*)(uintptr_t)k);

        lv_obj_t* dot = box(page.screen, cx, FOOT_Y, DOT_W, DOT_H);
        lv_obj_set_style_bg_color(dot, lv_color_hex(k == index ? accent : card), 0);
        lv_obj_set_style_radius(dot, 6, 0);
        lv_obj_clear_flag(dot, LV_OBJ_FLAG_CLICKABLE);
        char number[3]; snprintf(number, sizeof(number), "%u", (unsigned)(k + 1));
        lv_obj_t* num = label(dot, number, 20, k == index ? bg : text_color, 0, 0, DOT_W);
        lv_obj_set_style_text_align(num, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_align(num, LV_ALIGN_CENTER, 0, 0);
    }
    int cursor = 0;
    // 栅格按本页实际组件数分配高度，消除"2 个组件只占上 halves、下半屏全空"
    // 的死区（原实现恒定190px 高）。行数 = ceil(占用槽位/2)。
    int slots = 0;
    for (uint8_t i = 0; i < config.count; ++i) {
        if (config.widgets[i] == W_REMINDERS || config.widgets[i] == W_CALENDAR) slots += 2;
        else slots += 1;
    }
    int rows = (slots + 1) / 2;
    if (rows < 1) rows = 1;
    if (rows > 2) rows = 2;
    const int GRID_GAP = 8;
    int card_h = (BODY_H - (rows - 1) * GRID_GAP) / rows;
    for (uint8_t i = 0; i < config.count; ++i) {
        WidgetView& widget = page.widgets[i];
        widget.kind = config.widgets[i];
        if (widget.kind == W_AI_LIST) { make_ai(widget, page.screen, config.filter); continue; }
        if (widget.kind == W_WEATHER && config.city[0]) {
            make_weather_city(widget, page.screen, config.city); continue;
        }
        if (widget.kind == W_PET) { make_pet_page(widget, page.screen); continue; }
        if (widget.kind == W_HW_CARDS) { make_hardware(widget, page.screen); continue; }
        if (config.list) {
            int h = BODY_H / config.count - 6;
            make_generic(widget, page.screen, 8, BODY_Y + i * (h + 6), 304, h);
        } else {
            bool wide = widget.kind == W_REMINDERS || widget.kind == W_CALENDAR;
            if (wide && (cursor % 2)) ++cursor;
            int x = 8 + (cursor % 2) * 156;
            int y = BODY_Y + (cursor / 2) * (card_h + GRID_GAP);
            make_generic(widget, page.screen, x, y, wide ? 304 : 148, card_h);
            cursor += wide ? 2 : 1;
        }
    }
    page.alert = box(page.screen, 16, 150, 288, 160);
    lv_obj_set_style_bg_color(page.alert, lv_color_hex(COL_WARN), 0);
    label(page.alert, "闹钟提醒", 32, bg, 20, 20, 248);
    label(page.alert, "点击任意处停止", 24, bg, 20, 84, 248);
    lv_obj_add_flag(page.alert, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(page.alert, stop_alarm, LV_EVENT_CLICKED, nullptr);
    lv_obj_add_flag(page.alert, LV_OBJ_FLAG_HIDDEN);
}

int weekday(const char* date) {
    int y, m, d;
    if (sscanf(date, "%d-%d-%d", &y, &m, &d) != 3 || m < 1 || m > 12 || d < 1 || d > 31)
        return 0;
    static const int month[] = {0,3,2,5,0,3,5,1,4,6,2,4};
    y -= m < 3;
    int sunday_zero = (y + y/4 - y/100 + y/400 + month[m-1] + d) % 7;
    return sunday_zero == 0 ? 7 : sunday_zero;
}

const char* weekday_name(int day) {
    static const char* names[] = {"一", "二", "三", "四", "五", "六", "日"};
    return day >= 1 && day <= 7 ? names[day - 1] : "?";
}

int clock_seconds() {
    int base = (latest.clock_hms / 10000) * 3600 +
        ((latest.clock_hms / 100) % 100) * 60 + latest.clock_hms % 100;
    return (base + (millis() - last_clock_ms) / 1000) % 86400;
}

const char* state_short(const char* s) {
    if (!strcmp(s, "GENERATING")) return "生成中";
    if (!strcmp(s, "ASK")) return "待确认";
    if (!strcmp(s, "ERROR")) return "出错";
    if (!strcmp(s, "WAIT_INPUT")) return "等回复";
    if (!strcmp(s, "DONE")) return "已完成";
    if (!strcmp(s, "TOOL")) return "执行中";
    if (!strcmp(s, "THINKING")) return "思考中";
    return "处理中";
}

uint32_t state_color(const char* s) {
    if (!strcmp(s, "GENERATING") || !strcmp(s, "TOOL")) return accent;
    if (!strcmp(s, "WAIT_INPUT")) return COL_OK;
    if (!strcmp(s, "ASK")) return COL_WARN;
    if (!strcmp(s, "THINKING")) return COL_WARN;
    if (!strcmp(s, "ERROR")) return COL_ERR;
    return sub_color;
}

bool state_running(const char* s) {
    return !strcmp(s, "THINKING") || !strcmp(s, "GENERATING") ||
           !strcmp(s, "TOOL") || !strcmp(s, "BUILD");
}

// 任务活跃 = 任一会话处于运行/等待用户状态（非 DONE/IDLE）。
// 用于屏保门控：盯着长任务时屏幕不能自己睡掉。
bool tasks_active() {
    for (const AITask& t : latest.tasks) {
        if (!t.used) continue;
        if (strcmp(t.state, "DONE") && strcmp(t.state, "IDLE")) return true;
    }
    return false;
}

/* 「新完成」高亮：DN0 内容变化 → 完成带高亮 10 秒；用户任何触摸（不活动
   计时复位）视为已读，立即取消。设备端自己维护，不需要客户端下发已读状态。 */
static uint32_t done_fresh_until = 0;
static char done_last_key[48] = "";

void render_ai_filtered(WidgetView& view) {
    char line[96];
    const DoneTask& d0 = latest.done[0];
    if (view.band_lbl) {
        if (d0.used) {
            // 年龄本地推进：客户端停发该条（或整条清位）之后，屏上的
            // 「X分前」也要继续走，不能冻结在收到那一刻。
            int age = d0.age + (millis() - latest.done_at_ms[0]) / 1000;
            char ago[12];
            if (age < 60) snprintf(ago, sizeof(ago), "刚刚");
            else if (age < 3600) snprintf(ago, sizeof(ago), "%d分前", age / 60);
            else snprintf(ago, sizeof(ago), "%d时前", age / 3600);
            snprintf(line, sizeof(line), "完成 %s · %s", d0.proj, ago);
        } else {
            snprintf(line, sizeof(line), "暂无完成的会话");
        }
        set_text(view.band_lbl, line);
        bool fresh = d0.used && millis() < done_fresh_until;
        lv_obj_set_style_border_color(view.done_band,
            lv_color_hex(fresh ? accent : card_edge), 0);
        lv_obj_set_style_text_color(view.band_lbl,
            lv_color_hex(fresh ? accent : sub_color), 0);
    }
    int row = 0;
    bool blink_on = (millis() / 500) % 2 == 0;   // 活动指示：运行中的点每 0.5s 呼吸
    for (int i = 0; i < 8 && row < 5; ++i) {
        const AITask& task = latest.tasks[i];
        if (!task.used) continue;
        if (view.filter[0] && strcmp(task.tool, view.filter) != 0) continue;
        bool is_err = !strcmp(task.state, "ERROR");
        set_text(view.row_tool[row], task.tool);
        lv_obj_set_style_text_color(view.row_tool[row],
            lv_color_hex(is_err ? COL_ERR : text_color), 0);
        snprintf(line, sizeof(line), "%s · %d:%02d",
                 task.proj[0] ? task.proj : "未命名", task.elapse / 60, task.elapse % 60);
        set_text(view.row_info[row], line);
        if (view.row_dot[row]) {
            uint32_t dc = state_color(task.state);
            // 运行中的会话：状态点呼吸闪烁——一眼看出「活的」
            if (state_running(task.state) && !blink_on) dc = bg;
            lv_obj_set_style_bg_color(view.row_dot[row], lv_color_hex(dc), 0);
        }
        bar_set(view.row_fill[row], view.row_bar[row], task.prog, accent);
        view.row_slot[row] = i;
        lv_obj_clear_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
        ++row;
    }
    for (; row < 5; ++row) {
        lv_obj_add_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
        set_text(view.row_tool[row], "");
        set_text(view.row_info[row], "");
        if (view.row_dot[row])
            lv_obj_set_style_bg_color(view.row_dot[row], lv_color_hex(sub_color), 0);
        bar_set(view.row_fill[row], view.row_bar[row], 0, accent);
    }
}

void render_ai(WidgetView& view) {
    if (view.filter[0]) { render_ai_filtered(view); return; }
    if (!view.root) return;
    bool blink_on = (millis() / 500) % 2 == 0;   // 活动指示：运行中呼吸
    int focus = -1;
    for (int i = 0; i < 4; ++i)
        if (latest.tasks[i].used && pinned_id[0] && !strcmp(latest.tasks[i].id, pinned_id))
            focus = i;
    if (focus < 0)
        for (int i = 0; i < 4 && focus < 0; ++i) if (latest.tasks[i].used) focus = i;
    // 头行右侧：进行中 N · 共 M（效果图头行）
    int total = 0, running = 0;
    for (const AITask& task : latest.tasks) {
        if (!task.used) continue;
        ++total;
        if (state_running(task.state)) ++running;
    }
    char line[96];
    if (!total) strcpy(line, "活跃 0/0");
    else snprintf(line, sizeof(line), "活跃 %d/%d", running, total);
    set_text(view.ai_count, line);
    if (focus < 0) {
        set_text(view.extra[0], "AI 会话");
        set_text(view.main, "暂无活跃会话");
        set_text(view.sub, "等待编辑器活动");
        set_text(view.extra[7], "多编辑器启动后自动加入");
        if (view.ai_dot)
            lv_obj_set_style_bg_color(view.ai_dot, lv_color_hex(sub_color), 0);
        bar_set(view.ai_bar_fill, view.ai_bar, 0, accent);
    } else {
        const AITask& task = latest.tasks[focus];
        snprintf(line, sizeof(line), "%s · %.4s", task.tool, task.id);
        set_text(view.extra[0], line);
        set_text(view.main, task.proj[0] ? task.proj : "未命名项目");
        set_text(view.sub, state_short(task.state));
        lv_obj_set_style_text_color(view.sub, lv_color_hex(state_color(task.state)), 0);
        if (view.ai_dot)
            {
                uint32_t adc = state_color(task.state);
                if (state_running(task.state) && !blink_on) adc = bg;
                lv_obj_set_style_bg_color(view.ai_dot, lv_color_hex(adc), 0);
            }
        snprintf(line, sizeof(line), "运行 %d:%02d · %d 秒前更新",
                 task.elapse / 60, task.elapse % 60, task.age);
        set_text(view.extra[7], line);
        bar_set(view.ai_bar_fill, view.ai_bar, task.prog, accent);
    }
    int row = 0;
    for (int i = 0; i < 4 && row < 3; ++i) {
        if (!latest.tasks[i].used || i == focus) continue;
        const AITask& task = latest.tasks[i];
        bool is_err = !strcmp(task.state, "ERROR");
        set_text(view.extra[row + 1], task.tool);
        // 出错行工具名转错误色，与状态点同义（行内没有放状态文字的空间）
        lv_obj_set_style_text_color(view.extra[row + 1],
            lv_color_hex(is_err ? COL_ERR : text_color), 0);
        snprintf(line, sizeof(line), "%s · %d:%02d",
                 task.proj[0] ? task.proj : "未命名", task.elapse / 60, task.elapse % 60);
        set_text(view.extra[row + 4], line);
        if (view.row_dot[row])
            lv_obj_set_style_bg_color(view.row_dot[row],
                lv_color_hex(state_color(task.state)), 0);
        bar_set(view.row_fill[row], view.row_bar[row], task.prog, accent);
        view.row_slot[row] = i;
        lv_obj_clear_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
        ++row;
    }
    for (; row < 3; ++row) {
        lv_obj_add_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
        set_text(view.extra[row + 1], "");
        set_text(view.extra[row + 4], "");
        if (view.row_dot[row])
            lv_obj_set_style_bg_color(view.row_dot[row], lv_color_hex(sub_color), 0);
        bar_set(view.row_fill[row], view.row_bar[row], 0, accent);
    }
    if (view.ai_overview) {
        if (total <= 1) {
            lv_obj_clear_flag(view.ai_overview, LV_OBJ_FLAG_HIDDEN);
            if (have_data && online) {
                snprintf(line, sizeof(line), "CPU %d%%   内存 %d%%", latest.cpu, latest.mem);
                set_text(view.ai_metrics[0], line);
                if (!isnan(latest.netrx) && !isnan(latest.nettx))
                    snprintf(line, sizeof(line), "磁盘 %d%%   网络 %.1f Mb",
                             latest.disk, latest.netrx + latest.nettx);
                else snprintf(line, sizeof(line), "磁盘 %d%%   网络 --", latest.disk);
                set_text(view.ai_metrics[1], line);
            } else {
                set_text(view.ai_metrics[0], "等待电脑数据");
                set_text(view.ai_metrics[1], "");
            }
        } else lv_obj_add_flag(view.ai_overview, LV_OBJ_FLAG_HIDDEN);
    }
    // 宠物表情联动 AI 状态（AI 卡小宠物与宠物页大宠物共用一套反应）
    const char* st = focus >= 0 ? latest.tasks[focus].state : "IDLE";
    pet_apply_expression(pet_ai, st, latest.pet_h, latest.pet_t, latest.pet_e);
}

void render_hardware(WidgetView& view) {
    if (!view.hw_bar[0]) return;
    char line[48], tag[48];
    if (!have_data || !online) {
        for (int i = 0; i < 4; ++i) {
            set_text(view.extra[i*2], "--"); set_text(view.extra[i*2+1], "数据不可用");
            bar_set(view.hw_fill[i], view.hw_bar[i], 0, accent);
        }
        for (int i = 0; i < 3; ++i) set_text(view.hw_temp[i], "--");
        return;
    }
    snprintf(line, sizeof(line), "%d%%", latest.cpu);
    set_text(view.extra[0], line);
    // 温度已升格为底部三列卡，说明行改显 CPU 频率（信息不重复）
    if (latest.cpufreq > 0) snprintf(tag, sizeof(tag), "基频 %.1fG", latest.cpufreq / 1000.0f);
    else strcpy(tag, "即时负载");
    set_text(view.extra[1], tag);
    bar_set(view.hw_fill[0], view.hw_bar[0], latest.cpu, accent);

    snprintf(line, sizeof(line), "%d%%", latest.mem);
    set_text(view.extra[2], line);
    if (!isnan(latest.memtotal))
        snprintf(tag, sizeof(tag), "%.1f/%.0f GB", latest.memused, latest.memtotal);
    else strcpy(tag, "即时负载");
    set_text(view.extra[3], tag);
    bar_set(view.hw_fill[1], view.hw_bar[1], latest.mem, accent);

    snprintf(line, sizeof(line), "%d%%", latest.disk);
    set_text(view.extra[4], line);
    if (!isnan(latest.diskfree)) snprintf(tag, sizeof(tag), "余 %.1f G", latest.diskfree);
    else strcpy(tag, "使用率");
    set_text(view.extra[5], tag);
    // 磁盘 ≥90% 迷你条转警告色（效果图规则），一眼看出空间告急
    bar_set(view.hw_fill[2], view.hw_bar[2], latest.disk,
            latest.disk >= 90 ? COL_WARN : accent);

    // 网络：大数值=上下行合计（Mbps），迷你条按 50 Mbps 满刻度，明细放说明行。
    // 条形只是相对快慢的视觉线索，数值本身始终是真实遥测。
    bool net_ok = !isnan(latest.netrx) && !isnan(latest.nettx);
    if (net_ok) {
        snprintf(line, sizeof(line), "%.0f Mb", latest.netrx + latest.nettx);
        // 明细保留一位小数：0.3Mbps 这类小流量取整后全是 0，等于没显示
        snprintf(tag, sizeof(tag), "下%.1f 上%.1f", latest.netrx, latest.nettx);
        int pct = (int)((latest.netrx + latest.nettx) * 100.0f / 50.0f);
        bar_set(view.hw_fill[3], view.hw_bar[3], pct, accent);
    } else {
        strcpy(line, "--"); strcpy(tag, "网速未提供");
        bar_set(view.hw_fill[3], view.hw_bar[3], 0, accent);
    }
    set_text(view.extra[6], line);
    set_text(view.extra[7], tag);

    // 温度与风扇三列卡：无效列显示 "--"（诚实显示，绝不渲染 0 假值）
    if (latest.cpu_temp_valid) snprintf(line, sizeof(line), "%d℃", (int)(latest.cputemp + 0.5f));
    else strcpy(line, "--");
    set_text(view.hw_temp[0], line);
    if (latest.gpu_temp_valid) snprintf(line, sizeof(line), "%d℃", (int)(latest.gputemp + 0.5f));
    else strcpy(line, "--");
    set_text(view.hw_temp[1], line);
    // 风扇不带单位：单位已在标题（"风扇 rpm"），列宽按纯数字校过
    if (latest.fan_valid) snprintf(line, sizeof(line), "%d", (int)(latest.fan + 0.5f));
    else strcpy(line, "--");
    set_text(view.hw_temp[2], line);
}

// 目标日期时间（date="YYYY-MM-DD" time="HH:MM"）距现在的剩余秒数（负=已过）
long countdown_seconds(const ScheduleItem* item) {
    int ty = 0, tm = 0, td = 0, th = 0, tmn = 0;
    if (sscanf(item->date, "%d-%d-%d", &ty, &tm, &td) != 3) return -1;
    if (sscanf(item->time, "%d:%d", &th, &tmn) != 2) return -1;
    // 天数差（Howard Hinnant days_from_civil）
    auto days_civil = [](int y, int m, int d) -> long {
        y -= m <= 2;
        long era = (y >= 0 ? y : y - 399) / 400;
        unsigned yoe = (unsigned)(y - era * 400);
        unsigned doy = (153u * (unsigned)(m + (m > 2 ? -3 : 9)) + 2u) / 5u + (unsigned)d - 1u;
        unsigned doe = yoe * 365u + yoe / 4u - yoe / 100u + doy;
        return era * 146097L + (long)doe - 719468L;
    };
    if (!latest.date[0]) return -1;
    int ny = 0, nm = 0, nd = 0;
    if (sscanf(latest.date, "%d-%d-%d", &ny, &nm, &nd) != 3) return -1;
    long day_diff = (days_civil(ty, tm, td) - days_civil(ny, nm, nd)) * 86400L;
    int now_secs = clock_seconds();
    long target_secs = (long)th * 3600 + tmn * 60;
    return day_diff + target_secs - now_secs;
}

/* 天气城市页：当前实况卡（116px）+ 5 天预报行（46px×5，今天起）。
   数据取 latest.cities[]，下标 = 页面 city 名在 g_profile.city_names 里的序号。 */
void make_weather_city(WidgetView& view, lv_obj_t* screen, const char* city) {
    strncpy(view.city, city ? city : "", sizeof(view.city) - 1);
    view.city[sizeof(view.city) - 1] = 0;
    view.root = box(screen, 8, BODY_Y, 304, 116);
    view.main = label(view.root, "--", 48, text_color, 14, 4, 150, 62);
    view.sub = label(view.root, "", 24, accent, 14, 70, 140, 30);
    view.extra[0] = label(view.root, "", 20, sub_color, 150, 78, 140, 26);
    for (int i = 0; i < 5; ++i) {
        lv_obj_t* row = box(screen, 8, 184 + 50 * i, 304, 46);
        lv_obj_set_style_bg_color(row, lv_color_hex(bg), 0);
        view.rows[i] = row;
        view.wday_lbl[i] = label(row, "", 20, sub_color, 12, 4, 64, 26);
        view.wcond_lbl[i] = label(row, "", 20, text_color, 80, 4, 110, 26);
        view.wtmp_lbl[i] = label(row, "", 20, text_color, 194, 4, 98, 26);
        lv_obj_set_style_text_align(view.wtmp_lbl[i], LV_TEXT_ALIGN_RIGHT, 0);
    }
}

void render_weather_city(WidgetView& view) {
    if (!view.root) return;
    int idx = 0;
    for (int i = 0; i < 3; ++i)
        if (!strncmp(g_profile.city_names[i], view.city, sizeof(view.city))) { idx = i; break; }
    const CityWx& c = latest.cities[idx];
    char line[64];
    if (view.main) {
        if (c.used) snprintf(line, sizeof(line), "%.1f℃", c.temp);
        else strcpy(line, "--℃");
        set_text(view.main, line);
    }
    if (view.sub) set_text(view.sub, c.used ? weather_text(c.code) : "天气未配置");
    if (view.extra[0]) {
        if (c.used) snprintf(line, sizeof(line), "高 %.0f° 低 %.0f°", (float)c.hi, (float)c.lo);
        else snprintf(line, sizeof(line), "等客户端推送");
        set_text(view.extra[0], line);
    }
    int base_wd = weekday(latest.date);      // 1..7，无效时 0
    for (int i = 0; i < 5; ++i) {
        const CityDay& d = c.days[i];
        if (view.wday_lbl[i]) {
            const char* name;
            if (!base_wd) name = "";
            else if (i == 0) name = "今天";
            else if (i == 1) name = "明天";
            else if (i == 2) name = "后天";
            else {
                int wd = ((base_wd - 1 + i) % 7) + 1;
                static const char* wd_names[] = {"一","二","三","四","五","六","日"};
                snprintf(line, sizeof(line), "周%s", wd_names[wd - 1]);
                name = line;
            }
            set_text(view.wday_lbl[i], name);
        }
        if (view.wcond_lbl[i]) set_text(view.wcond_lbl[i],
            d.code < 0 ? "--" : weather_text(d.code));
        if (view.wtmp_lbl[i]) {
            if (d.code < 0) strcpy(line, "");
            else snprintf(line, sizeof(line), "%d° %d°", d.lo, d.hi);
            set_text(view.wtmp_lbl[i], line);
        }
    }
}

void render_generic(WidgetView& view) {
    if (view.kind == W_WEATHER && view.city[0]) { render_weather_city(view); return; }
    if (view.kind == W_PET) { render_pet_page(view); return; }
    if (!view.root) return;
    char line[96], sub[96];
    line[0] = 0; sub[0] = 0;
    switch (view.kind) {
    case W_CLOCK: {
        // 主值必须写进 line，由函数尾部 set_text(view.main, line) 统一上屏；
        // 直接 set_text(main) 会被尾部空串 line 覆盖，时钟卡会整块变白。
        int secs = clock_seconds();
        if (!latest.date[0]) strcpy(line, "--:--:--");
        else snprintf(line, sizeof(line), "%02d:%02d:%02d",
                      secs / 3600, (secs / 60) % 60, secs % 60);
        int wd = weekday(latest.date);
        if (view.extra[6]) {
            // 大时钟卡：右侧=星期 + 2026/10/9（效果图右栏）。主标签只承载
            // 数字冒号（48px 字库不含中文），未同步提示走 24px 全量字库块。
            if (!latest.date[0] || !wd) {
                set_text(view.extra[6], latest.date[0] ? "" : "时间未同步");
                set_text(view.extra[7], "");
                break;
            }
            char wd_buf[8], date_buf[16];
            snprintf(wd_buf, sizeof(wd_buf), "周%s", weekday_name(wd));
            set_text(view.extra[6], wd_buf);
            int y2, m2, d2;
            if (sscanf(latest.date, "%d-%d-%d", &y2, &m2, &d2) == 3)
                snprintf(date_buf, sizeof(date_buf), "%d/%d/%d", y2, m2, d2);
            else
                snprintf(date_buf, sizeof(date_buf), "%s", latest.date);
            set_text(view.extra[7], date_buf);
            break;
        }
        // 半宽卡（148px）放不下「2026-10-09 星期五 · 离线估算」——20px 正文字宽
        // 约需 300px，硬给宽度只会截成「2026-…」。故窄卡改用短格式，
        // 信息量不减反增：年月日的主值时间已经承载了「今天」这一事实。
        if (view.card_w && view.card_w < 300) {
            int y2, m2, d2;
            if (sscanf(latest.date, "%d-%d-%d", &y2, &m2, &d2) == 3)
                snprintf(sub, sizeof(sub), "%02d-%02d 周%s", m2, d2, weekday_name(wd));
            else
                snprintf(sub, sizeof(sub), "星期%s", weekday_name(wd));
        } else {
            snprintf(sub, sizeof(sub), "%s 星期%s%s", latest.date,
                     weekday_name(wd), online ? "" : " · 离线估算");
        }
        break;
    }
    case W_WEATHER:
        if (!latest.wx_valid) strcpy(line, "天气未配置");
        else snprintf(line, sizeof(line), "%.1f℃ %s", latest.wx_temp, weather_text(latest.wx_code));
        if (isnan(latest.wx_hi)) strcpy(sub, "等待客户端推送");
        else snprintf(sub, sizeof(sub), "高 %.0f°  低 %.0f°", latest.wx_hi, latest.wx_lo);
        break;
    case W_AI_COST: {
        if (latest.day_tok_total < 0) {
            strcpy(line, "今日暂无用量");
            strcpy(sub, "AI 会话活动后显示");
            break;
        }
        long k = latest.day_tok_total / 1000;
        if (k >= 1000) snprintf(line, sizeof(line), "今日 %.1fM", k / 1000.0);
        else snprintf(line, sizeof(line), "今日 %ldk", k);
        if (latest.ai_model[0] && latest.ai_tok_out > 0)
            snprintf(sub, sizeof(sub), "%s · 本轮 %.1fk", latest.ai_model,
                     latest.ai_tok_out / 1000.0);
        else if (latest.ai_model[0])
            snprintf(sub, sizeof(sub), "%s", latest.ai_model);
        else
            sub[0] = 0;
        break;
    }
    case W_COUNTDOWN: {
        const ScheduleItem* found = nullptr;
        for (uint8_t i = 0; i < g_profile.countdown_count; ++i) {
            const ScheduleItem& item = g_profile.countdowns[i];
            if (item.enabled && !found) found = &item;   // 取第一条
        }
        if (!found) { strcpy(line, "未设置"); strcpy(sub, "在工作台添加倒计时"); break; }
        long remain = countdown_seconds(found);
        if (remain < 0) { snprintf(line, sizeof(line), "%s", found->text);
                          strcpy(sub, "已结束"); break; }
        snprintf(line, sizeof(line), "%ld天 %02ld:%02ld:%02ld", remain / 86400,
                 (remain / 3600) % 24, (remain / 60) % 60, remain % 60);
        snprintf(sub, sizeof(sub), "%s %s", found->text, found->time);
        break;
    }
    case W_ENVIRONMENT:
        if (isnan(g_temp)) strcpy(line, "温度不可用");
        else snprintf(line, sizeof(line), "%.1f ℃", g_temp);
        if (isnan(g_hum)) strcpy(sub, "传感器读取失败");
        else snprintf(sub, sizeof(sub), "湿度 %d%%", (int)g_hum);
        break;
    case W_REMINDERS: {
        const ScheduleItem* found = nullptr;
        int day = weekday(latest.date);
        for (uint8_t i = 0; i < g_profile.reminder_count; ++i) {
            const ScheduleItem& item = g_profile.reminders[i];
            if (item.enabled && day && (item.weekdays & (1 << (day - 1))) &&
                (!found || strcmp(item.time, found->time) < 0)) found = &item;
        }
        if (found) {snprintf(line, sizeof(line), "%s %s", found->time, found->text);
                    strcpy(sub, "今天的下一条提醒");}
        else strcpy(line, "今天无提醒");
        break;
    }
    case W_CALENDAR: {
        const ScheduleItem* found = nullptr;
        for (uint8_t i = 0; i < g_profile.calendar_count; ++i) {
            const ScheduleItem& item = g_profile.calendar[i];
            if (item.enabled && !strcmp(item.date, latest.date) &&
                (!found || strcmp(item.time, found->time) < 0)) found = &item;
        }
        if (found) {snprintf(line, sizeof(line), "%s %s", found->time, found->text);
                    strcpy(sub, "今日日程");}
        else strcpy(line, "今天无日程");
        break;
    }
    case W_ALARM: {
        const ScheduleItem* found = nullptr;
        int day = weekday(latest.date);
        for (uint8_t i = 0; i < g_profile.alarm_count; ++i) {
            const ScheduleItem& item = g_profile.alarms[i];
            if (item.enabled && day && (item.weekdays & (1 << (day - 1))) &&
                (!found || strcmp(item.time, found->time) < 0)) found = &item;
        }
        if (found) {snprintf(line, sizeof(line), "%s %s", found->time, found->text);
                    strcpy(sub, "到点触摸屏幕停止");}
        else strcpy(line, "未设置闹钟");
        break;
    }
    default: break;
    }
    set_text(view.main, line);
    set_text(view.sub, sub);
}

void render_control() {
    if (!scr_settings) return;
    char text[16];
    // 正在播放：歌名 / 歌手 · 进度 / 进度条（NP 遥测；无播放时如实显示）
    if (np_title_lbl) {
        static char np_line[64];
        if (latest.np.used) {
            set_text(np_title_lbl, latest.np.title);
            int pos = latest.np.pos;
            if (latest.np.dur > 0)
                snprintf(np_line, sizeof(np_line), "%s  %d:%02d / %d:%02d",
                         latest.np.artist, pos / 60, pos % 60,
                         latest.np.dur / 60, latest.np.dur % 60);
            else
                snprintf(np_line, sizeof(np_line), "%s", latest.np.artist);
            set_text(np_sub_lbl, np_line);
            if (np_fill)
                lv_obj_set_width(np_fill,
                    300 * constrain(pos, 0, latest.np.dur) / latest.np.dur);
            lv_obj_set_style_bg_opa(np_fill,
                latest.np.playing ? LV_OPA_COVER : LV_OPA_60, 0);
        } else {
            set_text(np_title_lbl, "未在播放");
            set_text(np_sub_lbl, "");
            if (np_fill) lv_obj_set_width(np_fill, 0);
        }
    }
    if (latest.vol >= 0) snprintf(text, sizeof(text), "%d%%", latest.vol);
    else snprintf(text, sizeof(text), "--");
    set_text(ctrl_vol_lbl, text);
    snprintf(text, sizeof(text), "%d%%", display_brightness_get());
    set_text(ctrl_bright_lbl, text);
    if (ctrl_mute_lbl)
        set_text(lv_obj_get_child(ctrl_mute_lbl, 0), latest.mut == 1 ? "已静音" : "静音");
}

void render_all() {
    // 状态栏时钟 HH:MM:SS + 天气（各页同源）。
    // 显示到秒：用户要求「秒的变化能体现出来」。render_all() 每 1000ms 跑一次，
    // clock_seconds() 用 millis 在两次 TIME 上报之间插值，所以秒是连续走的。
    static char tb[16];
    int secs = clock_seconds();
    snprintf(tb, sizeof(tb), "%02d:%02d:%02d",
             secs / 3600, (secs / 60) % 60, secs % 60);
    const char* wx_txt = latest.wx_valid ? weather_text(latest.wx_code) : "";
    for (uint8_t p = 0; p < view_count; ++p) {
        PageView& page = views[p];
        if (page.topclock) set_text(page.topclock, tb);
        if (page.topwx) set_text(page.topwx, wx_txt);
        if (page.topdot)
            lv_obj_set_style_bg_color(page.topdot,
                lv_color_hex(online ? COL_OK : COL_ERR), 0);
    }
    for (uint8_t p = 0; p < view_count; ++p) {
        PageView& page = views[p];
        if (!page.screen) continue;
        for (uint8_t w = 0; w < g_profile.pages[p].count; ++w) {
            WidgetView& widget = page.widgets[w];
            if (widget.kind == W_AI_LIST) render_ai(widget);
            else if (widget.kind == W_HW_CARDS) render_hardware(widget);
            else render_generic(widget);
        }
    }
    render_control();
}

void alarm_tick() {
    uint32_t now = millis();
    if (alarm_active) {
        if (now - alarm_started_ms > 12000) {stop_alarm(nullptr); return;}
        int phase = (now - alarm_started_ms) % 1600;
        int span = phase < 800 ? phase : 1600 - phase;
        int low = max(10, (int)g_profile.bright / 3);
        display_brightness(low + (g_profile.bright - low) * span / 800);
        return;
    }
    if (!latest.date[0] || now - last_clock_ms > 600000) return;
    int day = weekday(latest.date), secs = clock_seconds();
    if (!day) return;
    for (uint8_t i = 0; i < g_profile.alarm_count; ++i) {
        const ScheduleItem& item = g_profile.alarms[i];
        int hour = 0, minute = 0;
        sscanf(item.time, "%d:%d", &hour, &minute);
        if (!item.enabled || !(item.weekdays & (1 << (day - 1))) ||
            secs / 60 != hour * 60 + minute || !strcmp(fired_dates[i], latest.date)) continue;
        snprintf(fired_dates[i], sizeof(fired_dates[i]), "%s", latest.date);
        alarm_active = true; alarm_started_ms = now;
        for (uint8_t p = 0; p < view_count; ++p)
            lv_obj_clear_flag(views[p].alert, LV_OBJ_FLAG_HIDDEN);
        break;
    }
}
} // namespace

void ui_init() { ui_rebuild(); }

void ui_rebuild() {
    // 保留当前屏直到新首页构建完成，配置同步时不闪出一帧空白。
    // 先删除其他旧页，峰值内存最多额外保留一个旧活动屏。
    lv_obj_t* old_active = lv_scr_act();
    overlay = 0;
    saver_active = false;
    // 宠物部件随旧屏幕一起销毁，必须先清零静态结构：draw_pet 开头的
    // lv_obj_del(p.root) 若摸到已释放的旧指针 = Core1 LoadProhibited
    // panic（真机复现：每次配置同步后设备静默重启，表现为同步后闪断）。
    pet_ai = PetParts();
    pet_page = PetParts();
    lv_obj_t* old[6] = {};
    uint8_t old_count = view_count;
    for (uint8_t i = 0; i < old_count; ++i) old[i] = views[i].screen;
    lv_obj_t* old_settings = scr_settings;
    lv_obj_t* old_saver = scr_saver;
    memset(views, 0, sizeof(views));
    scr_settings = nullptr;
    scr_saver = nullptr;
    saver_time_lbl = nullptr;
    saver_date_lbl = nullptr;
    for (uint8_t i = 0; i < old_count; ++i)
        if (old[i] && old[i] != old_active) lv_obj_del(old[i]);
    if (old_settings && old_settings != old_active) lv_obj_del(old_settings);
    if (old_saver && old_saver != old_active) lv_obj_del(old_saver);
    view_count = g_profile.page_count;
    if (current_page >= view_count) current_page = 0;
    bg = g_profile.bg; grad = g_profile.grad; card = g_profile.card; accent = g_profile.accent;
    text_color = g_profile.text; sub_color = g_profile.sub;
    card_edge = g_profile.card_edge;
    card_opa = g_profile.card_opa;
    top_clock = g_profile.top_clock;
    top_wx = g_profile.top_weather;
    idle_dim_secs = g_profile.idle_dim;
    saver_style = g_profile.saver;
    snprintf(saver_msg, sizeof(saver_msg), "%s", g_profile.saver_msg);
    // 保留当天的触发记录，主题/页面重建不能让闹钟在同一分钟再次响起。
    alarm_active = false;
    display_brightness(g_profile.bright);
    if (!view_count) return;
    build_page(current_page);
    if (!views[current_page].screen) return;
    lv_scr_load(views[current_page].screen);
    if (old_active) lv_obj_del(old_active);
    for (uint8_t i = 0; i < view_count; ++i)
        if (i != current_page) build_page(i);
    build_settings();
    render_all();
    // 背景/主题热更新时保证整屏重绘一次（实测缺这一步时新背景不出现）
    lv_obj_invalidate(lv_scr_act());
}

void ui_update(const Telemetry& telemetry) {
    have_data = true;
    if (telemetry.clock_hms != seen_hms) {
        seen_hms = telemetry.clock_hms;
        last_clock_ms = millis();
    }
    latest = telemetry;
    render_all();
}

void ui_set_inbox(int n) {
    // 服务端收件箱待处理数变化 → 页脚角标。set_text 对已删除控件安全
    // （views 全部指向当前页）；ui_rebuild 后由 build_page 用静态值重建。
    inbox_pending = n;
    for (uint8_t i = 0; i < view_count; ++i) {
        if (!views[i].inbox_lbl) continue;
        if (n > 0) {
            char t[12];
            snprintf(t, sizeof(t), "%d件", n);
            set_text(views[i].inbox_lbl, t);
            lv_obj_clear_flag(views[i].inbox_lbl, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(views[i].inbox_lbl, LV_OBJ_FLAG_HIDDEN);
        }
    }
}

void ui_set_online(bool connected) {
    if (online == connected) return;
    online = connected;
    for (uint8_t i = 0; i < view_count; ++i) {
        if (views[i].topdot)
            lv_obj_set_style_bg_color(views[i].topdot,
                lv_color_hex(connected ? COL_OK : COL_ERR), 0);
        if (views[i].conn) {
            set_text(views[i].conn, connected ? "在线" : "电脑离线");
            lv_obj_set_style_text_color(views[i].conn,
                lv_color_hex(connected ? COL_OK : COL_ERR), 0);
        }
        // 离线：顶部红条显示 + 卡片描边转红；在线：红条隐藏 + 描边恢复主题色。
        // 仅重着色已存在的卡片根（AI 页与通用组件的根 box）；硬件卡面板因未
        // 存入 view.root 此处不动，由顶栏红条与状态点统一提示。
        if (views[i].offbar) {
            if (connected) lv_obj_add_flag(views[i].offbar, LV_OBJ_FLAG_HIDDEN);
            else lv_obj_clear_flag(views[i].offbar, LV_OBJ_FLAG_HIDDEN);
        }
        for (uint8_t w = 0; w < g_profile.pages[i].count; ++w) {
            lv_obj_t* root = views[i].widgets[w].root;
            if (root)
                lv_obj_set_style_border_color(root,
                    lv_color_hex(connected ? card_edge : COL_ERR), 0);
        }
    }
    render_all();
}

void ui_apply_theme(uint32_t new_bg, uint32_t new_card, uint32_t new_accent,
                    uint32_t new_text, uint32_t new_sub, uint32_t new_grad) {
    new_bg = new_bg ? new_bg : COL_BG;
    new_card = new_card ? new_card : COL_CARD;
    new_accent = new_accent ? new_accent : COL_ACCENT;
    new_text = new_text ? new_text : COL_TEXT;
    new_sub = new_sub ? new_sub : COL_SUB;
    if (new_bg == bg && new_card == card && new_accent == accent &&
        new_text == text_color && new_sub == sub_color &&
        (!new_grad || new_grad == grad)) return;
    g_profile.bg = new_bg; g_profile.card = new_card; g_profile.accent = new_accent;
    g_profile.text = new_text; g_profile.sub = new_sub;
    if (new_grad) g_profile.grad = new_grad;
    ui_rebuild();
}

void close_saver() {
    if (!saver_active) return;
    saver_active = false;
    lv_scr_load(views[current_page].screen);
    display_brightness(g_profile.bright);
}

// ---------- 息屏主题（4 款） ----------
extern float g_temp, g_hum;
const char* saver_weekday() {
    int wd = weekday(latest.date);
    static char out[16];
    if (wd == 0) return "";          // 日期未同步：留空而不是「星期?」
    snprintf(out, sizeof(out), "星期%s", weekday_name(wd));
    return out;
}

void build_saver() {
    if (!scr_saver) {
        scr_saver = lv_obj_create(nullptr);
        lv_obj_set_style_bg_color(scr_saver, lv_color_hex(0x000000), 0);
        lv_obj_set_style_border_width(scr_saver, 0, 0);
    }
    // 每次进入都按当前样式重建（样式/短句可能被 CFG 更新过）
    lv_obj_clean(scr_saver);
    saver_time_lbl = saver_date_lbl = nullptr;

    int secs = clock_seconds();
    char hm[8];
    snprintf(hm, sizeof(hm), "%02d:%02d", secs / 3600, (secs / 60) % 60);

    if (saver_style == 1) {
        // 方寸仪表：边框仪表盘 + 时间/日期/温湿度
        lv_obj_t* frame = lv_obj_create(scr_saver);
        lv_obj_set_pos(frame, 20, 130);
        lv_obj_set_size(frame, 280, 220);
        lv_obj_set_style_bg_color(frame, lv_color_hex(0x0A0F14), 0);
        lv_obj_set_style_border_color(frame, lv_color_hex(0x2E4370), 0);
        lv_obj_set_style_border_width(frame, 2, 0);
        lv_obj_set_style_radius(frame, 14, 0);
        saver_time_lbl = label(frame, hm, 32, 0x22D3EE, 30, 30, 220);
        saver_date_lbl = label(frame, "", 20, 0x8CA3C7, 30, 80, 220);
        char envs[40] = "传感离线";
        if (!isnan(g_temp)) snprintf(envs, sizeof(envs), "%.1f℃  %.0f%%", g_temp, g_hum);
        label(frame, envs, 20, 0x6B7A8F, 30, 120, 220);
        label(frame, saver_weekday(), 20, 0x6B7A8F, 30, 156, 220);
        label(scr_saver, "方寸仪表", 16, 0x3A4A5F, 10, 8, 120);
    } else if (saver_style == 2) {
        // 像素夜色：强调色时间带 + 大日期
        lv_obj_t* band = lv_obj_create(scr_saver);
        lv_obj_set_pos(band, 0, 190);
        lv_obj_set_size(band, 320, 96);
        lv_obj_set_style_bg_color(band, lv_color_hex(accent), 0);
        lv_obj_set_style_border_width(band, 0, 0);
        lv_obj_clear_flag(band, LV_OBJ_FLAG_SCROLLABLE);
        saver_time_lbl = label(band, hm, 48, 0x000000, 0, 12, 320);
        lv_obj_set_style_text_align(saver_time_lbl, LV_TEXT_ALIGN_CENTER, 0);
        saver_date_lbl = label(scr_saver, "", 24, 0x6B7A8F, 0, 300, 320);
        lv_obj_set_style_text_align(saver_date_lbl, LV_TEXT_ALIGN_CENTER, 0);
        label(scr_saver, "PIXEL NIGHT", 16, 0x3A4A5F, 10, 8, 200);
    } else if (saver_style == 3) {
        // 诗词短句：小时间 + 自定义短句 + 底部日期
        saver_time_lbl = label(scr_saver, hm, 24, 0x6B7A8F, 10, 8, 140);
        saver_quote = lv_label_create(scr_saver);
        lv_obj_set_pos(saver_quote, 24, 150);
        lv_obj_set_width(saver_quote, 272);
        lv_label_set_long_mode(saver_quote, LV_LABEL_LONG_WRAP);
        lv_obj_set_style_text_font(saver_quote, &cn_24, 0);
        lv_obj_set_style_text_color(saver_quote, lv_color_hex(0x8CA3C7), 0);
        lv_label_set_text(saver_quote, saver_msg[0] ? saver_msg : "代码写完了吗？\n起来喝口水吧");
        saver_date_lbl = label(scr_saver, "", 20, 0x3A4A5F, 10, 430, 300);
        label(scr_saver, "", 16, sub_color, 0, 0, 0);
    } else if (saver_style == 4) {
        // 相册轮播：全屏图片，svint 秒轮换，淡入/滑入入场（见 rotate_album）
        if (album_decode_index(1)) {
            saver_img = lv_img_create(scr_saver);
            lv_img_set_src(saver_img, album_source());
            lv_obj_set_pos(saver_img, 0, 0);
            lv_obj_set_size(saver_img, SCR_W, SCR_H);
        } else {
            // 相册为空：黑底提示 + 极简时钟兜底
            saver_time_lbl = label(scr_saver, hm, 48, 0x6B7A8F, 0, 190, 320);
            lv_obj_set_style_text_align(saver_time_lbl, LV_TEXT_ALIGN_CENTER, 0);
            saver_date_lbl = label(scr_saver, "", 24, 0x3A4A5F, 0, 262, 320);
            lv_obj_set_style_text_align(saver_date_lbl, LV_TEXT_ALIGN_CENTER, 0);
            label(scr_saver, "相册为空，去工作台上传", 20, 0x6B7A8F, 0, 330, 320);
            lv_obj_set_style_text_align(scr_saver, LV_TEXT_ALIGN_CENTER, 0);
        }
    } else {
        // 极简时钟（默认）
        saver_time_lbl = label(scr_saver, hm, 48, 0x6B7A8F, 0, 190, 320);
        lv_obj_set_style_text_align(saver_time_lbl, LV_TEXT_ALIGN_CENTER, 0);
        saver_date_lbl = label(scr_saver, "", 24, 0x3A4A5F, 0, 262, 320);
        lv_obj_set_style_text_align(saver_date_lbl, LV_TEXT_ALIGN_CENTER, 0);
    }
}

// 相册轮换：到点换下一张，按 svfx 播放入场动画
static void album_advance() {
    uint8_t count = album_count();
    if (!count) return;
    sv_idx = (sv_idx >= count) ? 1 : sv_idx + 1;
    if (album_decode_index(sv_idx) && saver_img) {
        lv_img_set_src(saver_img, album_source());
        uint8_t fx = g_profile.svfx;
        if (fx == 1) {          // 淡入
            lv_obj_set_style_opa(saver_img, LV_OPA_0, 0);
            lv_anim_t fa;
            lv_anim_init(&fa);
            lv_anim_set_var(&fa, saver_img);
            lv_anim_set_exec_cb(&fa, (lv_anim_exec_xcb_t)lv_obj_set_style_opa);
            lv_anim_set_values(&fa, LV_OPA_0, LV_OPA_COVER);
            lv_anim_set_time(&fa, 400);
            lv_anim_start(&fa);
        } else if (fx == 2) {   // 滑入
            lv_obj_set_x(saver_img, SCR_W);
            lv_anim_t sa;
            lv_anim_init(&sa);
            lv_anim_set_var(&sa, saver_img);
            lv_anim_set_exec_cb(&sa, (lv_anim_exec_xcb_t)lv_obj_set_x);
            lv_anim_set_values(&sa, SCR_W, 0);
            lv_anim_set_time(&sa, 350);
            lv_anim_set_path_cb(&sa, lv_anim_path_ease_out);
            lv_anim_start(&sa);
        }
    }
}

// 诗句轮换：切到库里下一句（配置库优先，空则不动）
static void poem_advance() {
    if (!g_profile.poem_count) return;
    poem_idx = (poem_idx + 1) % g_profile.poem_count;
    poem_last_switch = millis();
    if (saver_quote)
        set_text(saver_quote, g_profile.poems[poem_idx]);
}

void update_saver() {
    if (!saver_time_lbl) return;
    // 相册轮播（样式4）：到点换下一张
    if (saver_style == 4 && saver_img &&
        millis() - sv_last_rot > (uint32_t)g_profile.svint * 1000) {
        sv_last_rot = millis();
        album_advance();
    }
    // 诗句轮换（样式3）：到点换下一句
    if (saver_style == 3 && saver_quote && g_profile.poem_count &&
        millis() - poem_last_switch > (uint32_t)g_profile.pint * 60000) {
        poem_advance();
    }
    int secs = clock_seconds();
    static char st[16], sd[48];
    snprintf(st, sizeof(st), "%02d:%02d", secs / 3600, (secs / 60) % 60);
    lv_label_set_text(saver_time_lbl, st);
    // 日期未同步（刚上电、客户端还没连上）时悬空一个「星期?」/「 星期?」
    // 看起来就像坏了——直接不显示日期行，等 DATE 到位后下一秒自动补上。
    int wd = weekday(latest.date);
    if (wd == 0) {
        lv_label_set_text(saver_date_lbl, "");
        return;
    }
    snprintf(sd, sizeof(sd), "%s 星期%s", latest.date, weekday_name(wd));
    lv_label_set_text(saver_date_lbl, sd);
    // 诗句轮换（样式3）：文本在 poem_advance 切 idx 后由这里对齐显示
    if (saver_style == 3 && saver_quote && g_profile.poem_count) {
        set_text(saver_quote, g_profile.poems[poem_idx % g_profile.poem_count]);
    }
}

void ui_theme_tick() {
    uint32_t now = millis();
    alarm_tick();
    // 新完成检测（工具页完成带的高亮来源）
    {
        char key[48];
        snprintf(key, sizeof(key), "%s|%s",
                 latest.done[0].used ? latest.done[0].proj : "",
                 latest.done[0].used ? latest.done[0].tool : "");
        if (strcmp(key, done_last_key) != 0) {
            strncpy(done_last_key, key, sizeof(done_last_key) - 1);
            done_last_key[sizeof(done_last_key) - 1] = 0;
            if (latest.done[0].used) {
                done_fresh_until = millis() + 10000;
                // 新完成：完成带从右侧滑入（P3 动效；结束位置=布局位 x=8）
                for (uint8_t p2 = 0; p2 < view_count; ++p2)
                    for (uint8_t w2 = 0; w2 < g_profile.pages[p2].count; ++w2)
                        if (views[p2].widgets[w2].done_band) {
                            lv_anim_t sa;
                            lv_anim_init(&sa);
                            lv_anim_set_var(&sa, views[p2].widgets[w2].done_band);
                            lv_anim_set_exec_cb(&sa, (lv_anim_exec_xcb_t)lv_obj_set_x);
                            lv_anim_set_values(&sa, SCR_W, 8);
                            lv_anim_set_time(&sa, 350);
                            lv_anim_set_path_cb(&sa, lv_anim_path_ease_out);
                            lv_anim_start(&sa);
                        }
            }
        }
        // 用户碰了屏幕就算"看过了"
        if (lv_disp_get_inactive_time(nullptr) < 2000) done_fresh_until = 0;
    }
    // 息屏门控：无触摸超阈值才息屏，且任务活跃时不息屏（盯长任务是核心场景）；
    // 已息屏后任务恢复 → 自动唤醒，避免"任务跑着、屏幕睡着"。
    if (idle_dim_secs > 0 && view_count) {
        bool should = lv_disp_get_inactive_time(nullptr) > (uint32_t)idle_dim_secs * 1000
                      && !tasks_active();
        if (should && !saver_active && !overlay && !alarm_active) {
            saver_active = true;
            build_saver();
            lv_scr_load(scr_saver);
            display_brightness(20);
        } else if (saver_active && (!should || tasks_active())) {
            saver_active = false;
            lv_scr_load(views[current_page].screen);
            display_brightness(g_profile.bright);
        }
        if (saver_active) update_saver();
    }
    if (now - last_render_ms >= 1000) {
        last_render_ms = now;
        render_all();
    }
}

void ui_set_idle_dim(int seconds) {
    idle_dim_secs = constrain(seconds, 0, 3600);
    g_profile.idle_dim = idle_dim_secs;
}

void ui_set_saver(uint8_t style) {
    saver_style = style > 3 ? 3 : style;
    g_profile.saver = saver_style;
    if (saver_active) { build_saver(); lv_scr_load(scr_saver); }
}

void ui_set_saver_interval(uint16_t seconds) {
    g_profile.svint = constrain(seconds, 15, 3600);
}

void ui_set_saver_fx(uint8_t fx) {
    g_profile.svfx = constrain(fx, 0, 2);
}

void ui_set_poem_interval(uint16_t minutes) {
    g_profile.pint = constrain(minutes, 1, 120);
}

void ui_set_saver_msg(const char* msg) {
    snprintf(saver_msg, sizeof(saver_msg), "%s", msg ? msg : "");
    snprintf(g_profile.saver_msg, sizeof(g_profile.saver_msg), "%s", saver_msg);
    if (saver_active && saver_style == 3) { build_saver(); lv_scr_load(scr_saver); }
}


void ui_set_topbar(bool clock_on, bool wx_on) {
    if (top_clock == clock_on && top_wx == wx_on) return;
    g_profile.top_clock = clock_on;
    g_profile.top_weather = wx_on;
    ui_rebuild();
}


void ui_set_card_opa(uint8_t opa) {
    if (g_profile.card_opa == opa) return;
    g_profile.card_opa = opa;
    ui_rebuild();
}

void ui_set_page(uint8_t page) {
    // 上位机/托盘请求切页（PAGE=N，0 基）。同页号会被 switch_page 拦截。
    if (page < view_count) switch_page(page, true);
}

// ---------- 实拍（工作台「实拍」按钮 → SNAP=1 下行 → 本函数回传） ----------
// 屏是写-only SPI（无 MISO）读不了 GRAM，用 LVGL 离屏快照代替：
// 全分辨率渲染到 PSRAM → 2×2 均值降到 160×240（76KB）→ base64 分帧上行。
// 每行负载 512 字节：90 字节曾把整帧拖到 ~10s（行数太多，逐行 printf/flush
// 的开销远大于纯带宽）。传输约 1-2s，期间渲染停顿——按需触发可接受。
void pet_play_event() {                   // 客户端「运动」互动 → PLAYING 场景
    pet_trigger_scene(PL_PLAYING, 2500);
}

void ui_send_snapshot() {
    lv_obj_t* scr = lv_scr_act();
    if (!scr) return;
    const int OW = SCR_W / 2, OH = SCR_H / 2;
    uint16_t* full = (uint16_t *)heap_caps_malloc(
        SCR_W * SCR_H * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    uint16_t* small = (uint16_t *)heap_caps_malloc(
        OW * OH * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!full || !small) {
        Serial.print("NAK=SNAP,MEM\n");
        if (full) free(full);
        if (small) free(small);
        return;
    }
    lv_img_dsc_t dsc;
    lv_res_t ok = lv_snapshot_take_to_buf(scr, LV_IMG_CF_TRUE_COLOR, &dsc,
                                          full, SCR_W * SCR_H * 2);
    if (ok != LV_RES_OK) {
        Serial.print("NAK=SNAP,RENDER\n");
        free(full);
        free(small);
        return;
    }
    // 2×2 均值降采样（RGB565：r5/g6/b5 分通道平均后重打包）
    for (int y = 0; y < OH; ++y) {
        for (int x = 0; x < OW; ++x) {
            uint32_t r = 0, g = 0, b = 0;
            for (int dy = 0; dy < 2; ++dy) {
                for (int dx = 0; dx < 2; ++dx) {
                    uint16_t v = full[(y * 2 + dy) * SCR_W + (x * 2 + dx)];
                    r += (v >> 11) & 0x1F;
                    g += (v >> 5) & 0x3F;
                    b += v & 0x1F;
                }
            }
            small[y * OW + x] = (uint16_t)((r / 4) << 11 | (g / 4) << 5 | (b / 4));
        }
    }
    free(full);

    Serial.printf("SNAP=BEGIN,%d,%d\n", OW, OH);
    const size_t total = (size_t)OW * OH * 2;
    const size_t CHUNK = 512;              // 每行 512 字节 → base64 ~684 字符
    unsigned char encoded[4 * ((CHUNK + 2) / 3) + 1];
    size_t olen = 0;
    int seq = 0;
    for (size_t off = 0; off < total; off += CHUNK, ++seq) {
        size_t n = (total - off < CHUNK) ? (total - off) : CHUNK;
        mbedtls_base64_encode(encoded, sizeof(encoded), &olen,
                              (const unsigned char *)small + off, n);
        Serial.printf("SNAP=DATA,%d,", seq);
        Serial.write(encoded, olen);
        Serial.print("\n");
        if ((seq & 31) == 31) delay(1);    // 定期让出，避免看门狗紧张
    }
    Serial.print("SNAP=END\n");
    free(small);
}
