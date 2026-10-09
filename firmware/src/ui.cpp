#include "ui.h"
#include "config.h"
#include "display.h"
#include "fonts.h"
#include "profile.h"
#include "bgimg.h"
#include <lvgl.h>
#include <math.h>

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
    lv_obj_t* rows[3] = {};
    uint8_t row_slot[3] = {};
    uint16_t card_w = 0;              // 卡片实际宽度：窄卡需换用短文案（见 render_generic）
    // 状态聚焦布局构件（对齐 docs/screen-ui-sample 效果图）：
    lv_obj_t* ai_count = nullptr;     // 顶部右侧「进行中 N · 共 M」
    lv_obj_t* ai_bar = nullptr;       // 焦点任务进度条
    lv_obj_t* ai_bar_fill = nullptr;
    lv_obj_t* ai_dot = nullptr;       // 焦点状态点
    lv_obj_t* row_dot[3] = {};        // 其他会话行状态点
    lv_obj_t* row_bar[3] = {};        // 其他会话行进度条
    lv_obj_t* row_fill[3] = {};
    lv_obj_t* hw_bar[4] = {};         // 硬件卡迷你条
    lv_obj_t* hw_fill[4] = {};
};
struct PageView {
    lv_obj_t *screen = nullptr, *conn = nullptr, *alert = nullptr;
    lv_obj_t *topclock = nullptr, *topwx = nullptr, *topdot = nullptr;
    lv_obj_t *topbat = nullptr;   // 电池 %（遥测缺 BAT 时隐藏，绝不显示假值）
    lv_obj_t *offbar = nullptr;   // 离线状态条（顶部 4px 红条）
    WidgetView widgets[4];
};

PageView views[6];
uint8_t view_count = 0, current_page = 0;
uint32_t bg = COL_BG, grad = COL_BG, card = COL_CARD, accent = COL_ACCENT;
uint8_t card_opa = 255;              // 卡片不透明度（0-255），背景图模式下 70% 左右最有层次
uint32_t text_color = COL_TEXT, sub_color = COL_SUB;
bool online = false, have_data = false, alarm_active = false;
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
// AI 页像素小机器人（LVGL 原生对象，表情联动 AI 状态）
lv_obj_t *bot_root = nullptr, *bot_body = nullptr, *bot_eye_l = nullptr, *bot_eye_r = nullptr;
lv_obj_t *bot_mouth = nullptr, *bot_antenna = nullptr, *bot_badge = nullptr;
int bot_last_state_hash = 0;             // 0 极简 1 仪表 2 像素夜色 3 诗词短句
char saver_msg[64] = "代码写完了吗？起来喝口水";
uint32_t seen_hms = 0;

// ---- 覆盖面板：0 无 1 启动器 2 控制中心 ----
uint8_t overlay = 0;
lv_obj_t *scr_launcher = nullptr, *scr_control = nullptr;
lv_obj_t *launch_cells[12] = {};
char launch_ids[12][13];
uint8_t launch_count = 0;
lv_obj_t *ctrl_vol_lbl = nullptr, *ctrl_mute_lbl = nullptr, *ctrl_bright_lbl = nullptr;

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
void open_launcher_cb(lv_event_t*) { open_overlay(1); }
void open_control_cb(lv_event_t*) { open_overlay(2); }

// ---------- 覆盖面板开合 ----------
void close_overlay() {
    if (!overlay) return;
    overlay = 0;
    lv_scr_load(views[current_page].screen);
}

void open_overlay(uint8_t which) {
    if (overlay == which) return;
    overlay = which;
    lv_scr_load_anim(which == 1 ? scr_launcher : scr_control,
                     LV_SCR_LOAD_ANIM_FADE_ON, 150, 0, false);
    send_touch(which == 1 ? "LAUNCHER_OPEN" : "CONTROL_OPEN");
}

void launcher_close_gesture(lv_event_t*) {
    lv_indev_t* input = lv_indev_get_act();
    if (!input) return;
    if (lv_indev_get_gesture_dir(input) == LV_DIR_TOP) close_overlay();
}

void control_close_gesture(lv_event_t*) {
    lv_indev_t* input = lv_indev_get_act();
    if (!input) return;
    if (lv_indev_get_gesture_dir(input) == LV_DIR_BOTTOM) close_overlay();
}

// 页面手势：竖滑开面板（下拉=启动器，上滑=控制中心），横滑切页
void gesture(lv_event_t* event) {
    lv_indev_t* input = lv_indev_get_act();
    if (!input) return;
    lv_dir_t direction = lv_indev_get_gesture_dir(input);
    if (overlay) {   // 面板打开时：反方向滑动收起
        if ((overlay == 1 && direction == LV_DIR_TOP) ||
            (overlay == 2 && direction == LV_DIR_BOTTOM))
            close_overlay();
        return;
    }
    if (direction == LV_DIR_TOP && launch_count) {
        open_overlay(1);
        return;
    }
    if (direction == LV_DIR_BOTTOM) {
        open_overlay(2);
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
    for (int i = 0; i < 3; ++i) {
        if (view->rows[i] == target) {
            uint8_t slot = view->row_slot[i];
            if (slot < 4 && latest.tasks[slot].used) {
                snprintf(pinned_id, sizeof(pinned_id), "%s", latest.tasks[slot].id);
                render_all();
            }
            break;
        }
    }
}

// ---------- 页面构建（竖屏） ----------
void make_ai(WidgetView& view, lv_obj_t* screen) {
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
    // 像素小机器人（焦点区右侧，表情联动 AI 状态）。
    // 原位置 (226,288) 压在第三行会话上，文字被机器人盖住；挪进焦点区空白。
    bot_root = lv_obj_create(view.root);
    lv_obj_set_pos(bot_root, 236, 44);
    lv_obj_set_size(bot_root, 56, 70);
    lv_obj_set_style_bg_opa(bot_root, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(bot_root, 0, 0);
    lv_obj_clear_flag(bot_root, LV_OBJ_FLAG_SCROLLABLE);
    bot_body = lv_obj_create(bot_root);
    lv_obj_set_pos(bot_body, 6, 16);
    lv_obj_set_size(bot_body, 44, 44);
    lv_obj_set_style_bg_color(bot_body, lv_color_hex(0x1A2A4A), 0);
    lv_obj_set_style_radius(bot_body, 14, 0);
    lv_obj_set_style_border_width(bot_body, 2, 0);
    lv_obj_set_style_border_color(bot_body, lv_color_hex(accent), 0);
    lv_obj_clear_flag(bot_body, LV_OBJ_FLAG_SCROLLABLE);
    // 天线
    lv_obj_t* ant = lv_line_create(bot_root);
    static lv_point_t ant_pts[] = {{28, 4}, {28, 16}};
    lv_line_set_points(ant, ant_pts, 2);
    lv_obj_set_style_line_color(ant, lv_color_hex(accent), 0);
    lv_obj_set_style_line_width(ant, 2, 0);
    bot_antenna = lv_obj_create(bot_root);
    lv_obj_set_pos(bot_antenna, 24, 0);
    lv_obj_set_size(bot_antenna, 8, 8);
    lv_obj_set_style_radius(bot_antenna, 4, 0);
    lv_obj_set_style_bg_color(bot_antenna, lv_color_hex(COL_OK), 0);
    lv_obj_set_style_border_width(bot_antenna, 0, 0);
    // 眼睛
    bot_eye_l = lv_obj_create(bot_body);
    lv_obj_set_pos(bot_eye_l, 8, 12);
    lv_obj_set_size(bot_eye_l, 10, 10);
    lv_obj_set_style_radius(bot_eye_l, 5, 0);
    lv_obj_set_style_bg_color(bot_eye_l, lv_color_hex(COL_TEXT), 0);
    lv_obj_set_style_border_width(bot_eye_l, 0, 0);
    lv_obj_clear_flag(bot_eye_l, LV_OBJ_FLAG_SCROLLABLE);
    bot_eye_r = lv_obj_create(bot_body);
    lv_obj_set_pos(bot_eye_r, 26, 12);
    lv_obj_set_size(bot_eye_r, 10, 10);
    lv_obj_set_style_radius(bot_eye_r, 5, 0);
    lv_obj_set_style_bg_color(bot_eye_r, lv_color_hex(COL_TEXT), 0);
    lv_obj_set_style_border_width(bot_eye_r, 0, 0);
    lv_obj_clear_flag(bot_eye_r, LV_OBJ_FLAG_SCROLLABLE);
    // 嘴巴
    bot_mouth = lv_obj_create(bot_body);
    lv_obj_set_pos(bot_mouth, 16, 30);
    lv_obj_set_size(bot_mouth, 12, 3);
    lv_obj_set_style_radius(bot_mouth, 2, 0);
    lv_obj_set_style_bg_color(bot_mouth, lv_color_hex(accent), 0);
    lv_obj_set_style_border_width(bot_mouth, 0, 0);
    // 状态徽标（! ? ✓ ×）
    bot_badge = label(bot_root, "", 24, COL_WARN, 0, 6, 56);
    lv_obj_set_style_text_align(bot_badge, LV_TEXT_ALIGN_CENTER, 0);
}

void make_hardware(WidgetView& view, lv_obj_t* screen) {
    // 四卡对齐效果图：处理器/内存/磁盘/网络（GPU 上位机网页可见）。
    // 卡内：标题 → 大数值 → 迷你进度条 → 说明。磁盘 ≥90% 条转警告色。
    const char* titles[4] = {"处理器", "内存", "磁盘", "网络"};
    for (int i = 0; i < 4; ++i) {
        lv_obj_t* panel = box(screen, 8 + (i % 2) * 156, BODY_Y + (i / 2) * 194, 148, 186);
        label(panel, titles[i], 20, sub_color, 12, 28, 124);
        view.extra[i * 2] = label(panel, "--", 32, text_color, 12, 64, 124, 44);
        view.hw_bar[i] = bar_track(panel, 12, 120, 124);
        view.hw_fill[i] = bar_fill(view.hw_bar[i]);
        view.extra[i * 2 + 1] = label(panel, "", 20, sub_color, 12, 136, 124);
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
        // 大时钟卡（效果图样式）：左=48px 时分秒，右=星期 + 日期。
        // 48px 字库只收界面文案字（ASCII 数字冒号必然在 0x20-0x7F 内），
        // 中文一律不进该标签；宽度按 8 字符 ≈ 184px 预留，与右侧块不重叠。
        view.main = label(view.root, "--:--:--", 48, text_color, 12, 46, w - 112, 60);
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
void build_launcher() {
    scr_launcher = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(scr_launcher, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_grad_color(scr_launcher, lv_color_hex(grad), 0);
    lv_obj_set_style_bg_grad_dir(scr_launcher, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_width(scr_launcher, 0, 0);
    lv_obj_clear_flag(scr_launcher, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(scr_launcher, launcher_close_gesture, LV_EVENT_GESTURE, nullptr);

    label(scr_launcher, "应用启动器", 20, text_color, 10, HEAD_Y, 180);
    label(scr_launcher, "上滑收起", 16, sub_color, 230, HEAD_Y + 4, 80);
    label(scr_launcher, "点击图标在电脑上打开", 16, sub_color, 10, 32, 260);

    launch_count = 0;
    for (uint8_t i = 0; i < g_profile.app_count && launch_count < 12; ++i) {
        const AppEntry& app = g_profile.apps[i];
        uint8_t row = launch_count / 3, col = launch_count % 3;
        int x = 8 + col * 102, y = 62 + row * 100;
        lv_obj_t* cell = button(scr_launcher, app.name, x, y, 96, 90,
                                launch_app, (void*)(uintptr_t)launch_count);
        (void)cell;
        launch_cells[launch_count] = cell;
        snprintf(launch_ids[launch_count], 13, "%s", app.id);
        ++launch_count;
    }
    if (!launch_count) {
        // 空态设计（实机配色核验 §3.2 / §4 P1）：原本只有一行字悬在屏幕中部，
        // 主体 70% 是空白，用户看不出"这里本该有东西"。改为居中成组的卡片。
        // ⚠️ 文案用字必须在字库内：cn_32/cn_48 只收界面文案字（见 tools/gen_fonts.py），
        //    用字库外的字会渲染成空白。改文案后必须重跑 gen_fonts.py。
        lv_obj_t* empty = box(scr_launcher, 30, 168, 260, 164);
        lv_obj_t* icon = label(empty, "▦", 40, accent, 0, 12, 260);
        lv_obj_set_style_text_align(icon, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_t* why = label(empty, "还没有配置快捷应用", 28, text_color, 0, 62, 260);
        lv_obj_set_style_text_align(why, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_t* tip = label(empty, "在电脑工作台的\n快捷应用中添加", 20, sub_color, 0, 108, 260);
        lv_obj_set_style_text_align(tip, LV_TEXT_ALIGN_CENTER, 0);
    }
}

void build_control() {
    scr_control = lv_obj_create(nullptr);
    lv_obj_set_style_bg_color(scr_control, lv_color_hex(bg), 0);
    lv_obj_set_style_bg_grad_color(scr_control, lv_color_hex(grad), 0);
    lv_obj_set_style_bg_grad_dir(scr_control, LV_GRAD_DIR_VER, 0);
    lv_obj_set_style_border_width(scr_control, 0, 0);
    lv_obj_clear_flag(scr_control, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(scr_control, control_close_gesture, LV_EVENT_GESTURE, nullptr);

    label(scr_control, "控制中心", 20, text_color, 10, HEAD_Y, 160);
    label(scr_control, "下滑收起", 16, sub_color, 230, HEAD_Y + 4, 80);

    // 音量组
    lv_obj_t* vol_box = box(scr_control, 8, 44, 304, 96);
    label(vol_box, "电脑音量", 16, sub_color, 10, 8, 120);
    button(vol_box, "-", 10, 40, 64, 44, send_key_cb, (void*)"VOL-");
    ctrl_vol_lbl = label(vol_box, "--", 24, text_color, 84, 46, 92);
    lv_obj_set_style_text_align(ctrl_vol_lbl, LV_TEXT_ALIGN_CENTER, 0);
    button(vol_box, "+", 184, 40, 64, 44, send_key_cb, (void*)"VOL+");
    ctrl_mute_lbl = button(scr_control, "静音", 254, 44, 58, 96, send_key_cb, (void*)"MUTE");

    // 媒体组
    lv_obj_t* media_box = box(scr_control, 8, 150, 304, 96);
    label(media_box, "媒体播放", 16, sub_color, 10, 8, 120);
    button(media_box, "上一首", 10, 40, 88, 44, send_key_cb, (void*)"PREV");
    button(media_box, "播放暂停", 104, 40, 96, 44, send_key_cb, (void*)"PLAY", accent);
    button(media_box, "下一首", 206, 40, 88, 44, send_key_cb, (void*)"NEXT");

    // 屏幕亮度组（本地生效）
    lv_obj_t* bl_box = box(scr_control, 8, 256, 304, 96);
    label(bl_box, "屏幕亮度", 16, sub_color, 10, 8, 120);
    button(bl_box, "-", 10, 40, 64, 44, bright_down, nullptr);
    ctrl_bright_lbl = label(bl_box, "90%", 24, text_color, 84, 46, 92);
    lv_obj_set_style_text_align(ctrl_bright_lbl, LV_TEXT_ALIGN_CENTER, 0);
    button(bl_box, "+", 184, 40, 64, 44, bright_up, nullptr);

    label(scr_control, "音量与媒体由电脑执行 · 实时生效", 16, sub_color, 10, 372, 300);
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
    if (bgimg_ready()) {                    // 背景图层（最底层）
        lv_obj_t* bgimg = lv_img_create(page.screen);
        lv_img_set_src(bgimg, bgimg_source());
        lv_obj_set_pos(bgimg, 0, 0);
        lv_obj_move_background(bgimg);
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
    label(page.screen, config.title, 20, text_color, 10, HEAD_Y,
          top_clock ? 118 : (top_wx ? 160 : 200));
    if (top_clock) {
        // 中置短时钟：HH:MM 20px 约 45px 宽，x=132..196 与左标题/右天气都不相交
        page.topclock = label(page.screen, "", 20, accent, 132, HEAD_Y, 60);
        lv_obj_set_style_text_align(page.topclock, LV_TEXT_ALIGN_CENTER, 0);
    }
    if (top_wx) {
        page.topwx = label(page.screen, "", 16, sub_color, 178, HEAD_Y + 4, 56);
        lv_obj_set_style_text_align(page.topwx, LV_TEXT_ALIGN_RIGHT, 0);
        set_text(page.topwx, latest.wx_valid ? weather_text(latest.wx_code) : "");
    }
    // 电池标签只在状态栏模式（时钟或天气至少一个开启）创建；
    // 两开关全关的回退模式里右侧是「在线/电脑离线」文字，避免重叠。
    if (top_clock || top_wx) {
        page.topbat = label(page.screen, "", 20, text_color, 240, HEAD_Y + 2, 52);
        lv_obj_set_style_text_align(page.topbat, LV_TEXT_ALIGN_RIGHT, 0);
        if (latest.bat >= 0 && latest.bat <= 100) {
            char bat_text[12];
            snprintf(bat_text, sizeof(bat_text), "%d%%", latest.bat);
            set_text(page.topbat, bat_text);
        } else {
            lv_obj_add_flag(page.topbat, LV_OBJ_FLAG_HIDDEN);
        }
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
    button(page.screen, "☰", 4, FOOT_Y - 2, 40, 28, open_launcher_cb, nullptr);
    button(page.screen, "⚙", SCR_W - 44, FOOT_Y - 2, 40, 28, open_control_cb, nullptr);
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
        if (widget.kind == W_AI_LIST) { make_ai(widget, page.screen); continue; }
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

int clock_seconds() {
    int base = (latest.clock_hms / 10000) * 3600 +
        ((latest.clock_hms / 100) % 100) * 60 + latest.clock_hms % 100;
    return (base + (millis() - last_clock_ms) / 1000) % 86400;
}

const char* state_short(const char* s) {
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

void render_ai(WidgetView& view) {
    if (!view.root) return;
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
    if (!total) strcpy(line, "空闲中");
    else snprintf(line, sizeof(line), "进行中 %d · 共 %d", running, total);
    set_text(view.ai_count, line);
    if (focus < 0) {
        set_text(view.extra[0], "Claude / Codex / ZCode");
        set_text(view.main, "暂无活跃会话");
        set_text(view.sub, "等待编辑器活动");
        set_text(view.extra[7], "有会话活动时自动显示");
        if (view.ai_dot)
            lv_obj_set_style_bg_color(view.ai_dot, lv_color_hex(sub_color), 0);
        bar_set(view.ai_bar_fill, view.ai_bar, 0, accent);
    } else {
        const AITask& task = latest.tasks[focus];
        snprintf(line, sizeof(line), "%s · #%s", task.tool, task.id);
        set_text(view.extra[0], line);
        set_text(view.main, task.proj[0] ? task.proj : "未命名项目");
        set_text(view.sub, state_short(task.state));
        lv_obj_set_style_text_color(view.sub, lv_color_hex(state_color(task.state)), 0);
        if (view.ai_dot)
            lv_obj_set_style_bg_color(view.ai_dot,
                lv_color_hex(state_color(task.state)), 0);
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
    // 角色表情
    const char* st = focus >= 0 ? latest.tasks[focus].state : "IDLE";
    int hash = (int)(st[0]) * 31 + (int)(st[1] ? st[1] : 0);
    if (hash != bot_last_state_hash) {
        bot_last_state_hash = hash;
        bool is_err = !strcmp(st, "ERROR");
        bool is_done = !strcmp(st, "DONE");
        bool is_wait = !strcmp(st, "WAIT_INPUT") || !strcmp(st, "ASK");
        bool is_idle = !strcmp(st, "IDLE");
        uint32_t eye_col = is_err ? COL_ERR : is_done ? COL_OK : COL_TEXT;
        uint32_t body_col = is_err ? 0x2A1A1A : is_done ? 0x1A2A1A : 0x1A2A4A;
        uint32_t border_col = is_err ? COL_ERR : is_done ? COL_OK : accent;
        lv_obj_set_style_bg_color(bot_body, lv_color_hex(body_col), 0);
        lv_obj_set_style_border_color(bot_body, lv_color_hex(border_col), 0);
        lv_obj_set_style_bg_color(bot_eye_l, lv_color_hex(eye_col), 0);
        lv_obj_set_style_bg_color(bot_eye_r, lv_color_hex(eye_col), 0);
        // 眼睛形状：IDLE 压扁(睡)、DONE 压扁(笑)、其他圆
        int eye_h = is_idle || is_done ? 4 : 10;
        lv_obj_set_height(bot_eye_l, eye_h);
        lv_obj_set_height(bot_eye_r, eye_h);
        lv_obj_set_y(bot_eye_l, 12 + (10 - eye_h) / 2);
        lv_obj_set_y(bot_eye_r, 12 + (10 - eye_h) / 2);
        // 嘴巴：ERROR 宽扁 / WAIT_INPUT 小圆嘴 / 其他正常
        lv_obj_set_width(bot_mouth, is_err ? 18 : 12);
        lv_obj_set_style_bg_color(bot_mouth,
            lv_color_hex(is_err ? COL_ERR : is_done ? COL_OK : accent), 0);
        set_text(bot_badge, is_wait ? "!" : is_err ? "×" : "");
    }
}

void render_hardware(WidgetView& view) {
    if (!view.root) return;
    char line[48], tag[48];
    if (!have_data || !online) {
        for (int i = 0; i < 4; ++i) {
            set_text(view.extra[i*2], "--"); set_text(view.extra[i*2+1], "数据不可用");
            bar_set(view.hw_fill[i], view.hw_bar[i], 0, accent);
        }
        return;
    }
    snprintf(line, sizeof(line), "%d%%", latest.cpu);
    set_text(view.extra[0], line);
    if (latest.cpu_temp_valid) snprintf(tag, sizeof(tag), "%.0f ℃", latest.cputemp);
    else strcpy(tag, "即时负载");
    set_text(view.extra[1], tag);
    bar_set(view.hw_fill[0], view.hw_bar[0], latest.cpu, accent);

    snprintf(line, sizeof(line), "%d%%", latest.mem);
    set_text(view.extra[2], line);
    if (!isnan(latest.memtotal))
        snprintf(tag, sizeof(tag), "%.1f/%.1f G", latest.memused, latest.memtotal);
    else strcpy(tag, "即时负载");
    set_text(view.extra[3], tag);
    bar_set(view.hw_fill[1], view.hw_bar[1], latest.mem, accent);

    snprintf(line, sizeof(line), "%d%%", latest.disk);
    set_text(view.extra[4], line);
    if (!isnan(latest.diskfree)) snprintf(tag, sizeof(tag), "余 %.0f G", latest.diskfree);
    else strcpy(tag, "使用率");
    set_text(view.extra[5], tag);
    // 磁盘 ≥90% 迷你条转警告色（效果图规则），一眼看出空间告急
    bar_set(view.hw_fill[2], view.hw_bar[2], latest.disk,
            latest.disk >= 90 ? COL_WARN : accent);

    // 网络：大数值=上下行合计（Mbps），迷你条按 50 Mbps 满刻度，明细放说明行。
    // 条形只是相对快慢的视觉线索，数值本身始终是真实遥测。
    bool net_ok = !isnan(latest.netrx) && !isnan(latest.nettx);
    if (net_ok) {
        snprintf(line, sizeof(line), "%.1f", latest.netrx + latest.nettx);
        snprintf(tag, sizeof(tag), "下 %.1f 上 %.1f Mb", latest.netrx, latest.nettx);
        int pct = (int)((latest.netrx + latest.nettx) * 100.0f / 50.0f);
        bar_set(view.hw_fill[3], view.hw_bar[3], pct, accent);
    } else {
        strcpy(line, "--"); strcpy(tag, "网速未提供");
        bar_set(view.hw_fill[3], view.hw_bar[3], 0, accent);
    }
    set_text(view.extra[6], line);
    set_text(view.extra[7], tag);
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

void render_generic(WidgetView& view) {
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
        static const char* WD = "一二三四五六日";
        if (view.extra[6]) {
            // 大时钟卡：右侧=星期 + 2026/10/9（效果图右栏）。主标签只承载
            // 数字冒号（48px 字库不含中文），未同步提示走 24px 全量字库块。
            if (!latest.date[0] || !wd) {
                set_text(view.extra[6], latest.date[0] ? "" : "时间未同步");
                set_text(view.extra[7], "");
                break;
            }
            char wd_buf[8], date_buf[16];
            snprintf(wd_buf, sizeof(wd_buf), "周%c", WD[(wd + 6) % 7]);
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
                snprintf(sub, sizeof(sub), "%02d-%02d 周%c", m2, d2, WD[(wd + 6) % 7]);
            else
                snprintf(sub, sizeof(sub), "星期%c", WD[(wd + 6) % 7]);
        } else {
            snprintf(sub, sizeof(sub), "%s 星期%c%s", latest.date,
                     WD[(wd + 6) % 7], online ? "" : " · 离线估算");
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
    if (!scr_control) return;
    char text[16];
    if (latest.vol >= 0) snprintf(text, sizeof(text), "%d%%", latest.vol);
    else snprintf(text, sizeof(text), "--");
    set_text(ctrl_vol_lbl, text);
    snprintf(text, sizeof(text), "%d%%", display_brightness_get());
    set_text(ctrl_bright_lbl, text);
    if (ctrl_mute_lbl)
        set_text(lv_obj_get_child(ctrl_mute_lbl, 0), latest.mut == 1 ? "已静音" : "静音");
}

void render_all() {
    // 状态栏：短时钟 HH:MM + 天气 + 电池%（各页同源）
    static char tb[16];
    int secs = clock_seconds();
    snprintf(tb, sizeof(tb), "%02d:%02d", secs / 3600, (secs / 60) % 60);
    const char* wx_txt = latest.wx_valid ? weather_text(latest.wx_code) : "";
    bool bat_ok = latest.bat >= 0 && latest.bat <= 100;
    for (uint8_t p = 0; p < view_count; ++p) {
        PageView& page = views[p];
        if (page.topclock) set_text(page.topclock, tb);
        if (page.topwx) set_text(page.topwx, wx_txt);
        if (page.topbat) {
            if (bat_ok) {
                char bat_text[12];
                snprintf(bat_text, sizeof(bat_text), "%d%%", latest.bat);
                set_text(page.topbat, bat_text);
                // 低电量转警告色；正常的电池文字用主题文字色
                lv_obj_set_style_text_color(page.topbat, lv_color_hex(
                    latest.bat <= 20 ? COL_WARN : text_color), 0);
                lv_obj_clear_flag(page.topbat, LV_OBJ_FLAG_HIDDEN);
            } else {
                lv_obj_add_flag(page.topbat, LV_OBJ_FLAG_HIDDEN);
            }
        }
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
    // 装载临时空白屏避免删除活动屏幕导致悬空指针；
    // 先删旧屏再建新屏，把 LVGL 堆峰值减半（防止 6 页+双面板挤爆内存出空屏）。
    lv_obj_t* blank = lv_obj_create(nullptr);
    if (!blank) return;
    lv_obj_set_style_bg_color(blank, lv_color_hex(g_profile.bg), 0);
    lv_scr_load(blank);
    overlay = 0;
    saver_active = false;
    lv_obj_t* old[6] = {};
    uint8_t old_count = view_count;
    for (uint8_t i = 0; i < old_count; ++i) old[i] = views[i].screen;
    lv_obj_t* old_launcher = scr_launcher, *old_control = scr_control;
    memset(views, 0, sizeof(views));
    scr_launcher = scr_control = nullptr;
    for (uint8_t i = 0; i < old_count; ++i) if (old[i]) lv_obj_del(old[i]);
    if (old_launcher) lv_obj_del(old_launcher);
    if (old_control) lv_obj_del(old_control);
    view_count = g_profile.page_count;
    if (current_page >= view_count) current_page = 0;
    bg = g_profile.bg; grad = g_profile.grad; card = g_profile.card; accent = g_profile.accent;
    text_color = g_profile.text; sub_color = g_profile.sub;
    card_edge = g_profile.card_edge;
    card_opa = g_profile.card_opa;
    memset(fired_dates, 0, sizeof(fired_dates));
    alarm_active = false;
    for (uint8_t i = 0; i < view_count; ++i) build_page(i);
    build_launcher();
    build_control();
    lv_scr_load(views[current_page].screen);
    lv_obj_del(blank);
    render_all();
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
        new_text == text_color && new_sub == sub_color) return;
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
    static const char* WD = "一二三四五六日";
    static char out[8];
    snprintf(out, sizeof(out), "星期%c", WD[(wd + 6) % 7]);
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
        lv_obj_t* quote = lv_label_create(scr_saver);
        lv_obj_set_pos(quote, 24, 150);
        lv_obj_set_width(quote, 272);
        lv_label_set_long_mode(quote, LV_LABEL_LONG_WRAP);
        lv_obj_set_style_text_font(quote, &cn_24, 0);
        lv_obj_set_style_text_color(quote, lv_color_hex(0x8CA3C7), 0);
        lv_label_set_text(quote, saver_msg[0] ? saver_msg : "代码写完了吗？\n起来喝口水吧");
        saver_date_lbl = label(scr_saver, "", 20, 0x3A4A5F, 10, 430, 300);
        label(scr_saver, "", 16, sub_color, 0, 0, 0);
    } else {
        // 极简时钟（默认）
        saver_time_lbl = label(scr_saver, hm, 48, 0x6B7A8F, 0, 190, 320);
        lv_obj_set_style_text_align(saver_time_lbl, LV_TEXT_ALIGN_CENTER, 0);
        saver_date_lbl = label(scr_saver, "", 24, 0x3A4A5F, 0, 262, 320);
        lv_obj_set_style_text_align(saver_date_lbl, LV_TEXT_ALIGN_CENTER, 0);
    }
}

void update_saver() {
    if (!saver_time_lbl) return;
    int secs = clock_seconds();
    static char st[16], sd[48];
    snprintf(st, sizeof(st), "%02d:%02d", secs / 3600, (secs / 60) % 60);
    lv_label_set_text(saver_time_lbl, st);
    int wd = weekday(latest.date);
    static const char* WD2 = "一二三四五六日";
    snprintf(sd, sizeof(sd), "%s 星期%c", latest.date, WD2[(wd + 6) % 7]);
    lv_label_set_text(saver_date_lbl, sd);
}

void ui_theme_tick() {
    uint32_t now = millis();
    alarm_tick();
    // 息屏主题：无触摸超过阈值 → 息屏（4 款样式）；任何触摸 → 唤醒
    if (idle_dim_secs > 0 && view_count) {
        bool should = lv_disp_get_inactive_time(nullptr) > (uint32_t)idle_dim_secs * 1000;
        if (should && !saver_active && !overlay && !alarm_active) {
            saver_active = true;
            build_saver();
            lv_scr_load(scr_saver);
            display_brightness(20);
        } else if (!should && saver_active) {
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
}

void ui_set_saver(uint8_t style) {
    saver_style = style > 3 ? 3 : style;
    if (saver_active) { build_saver(); lv_scr_load(scr_saver); }
}

void ui_set_saver_msg(const char* msg) {
    snprintf(saver_msg, sizeof(saver_msg), "%s", msg ? msg : "");
    if (saver_active && saver_style == 3) { build_saver(); lv_scr_load(scr_saver); }
}


void ui_set_topbar(bool clock_on, bool wx_on) {
    if (top_clock == clock_on && top_wx == wx_on) return;
    top_clock = clock_on;
    top_wx = wx_on;
    ui_rebuild();
}


void ui_set_card_opa(uint8_t opa) {
    card_opa = opa;
    ui_rebuild();
}

void ui_set_page(uint8_t page) {
    // 上位机/托盘请求切页（PAGE=N，0 基）。同页号会被 switch_page 拦截。
    if (page < view_count) switch_page(page, true);
}
