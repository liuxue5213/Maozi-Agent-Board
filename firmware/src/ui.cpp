#include "ui.h"
#include "config.h"
#include "display.h"
#include "fonts.h"
#include "profile.h"
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
constexpr int FOOT_Y = 448;      // 页码行（底边 472，留 8px 安全边距）
constexpr int EDGE_TOP = 90;     // 顶部下拉判定区
constexpr int EDGE_BOTTOM = SCR_H - 90;

uint32_t card_edge = COL_CARD_EDGE;

struct WidgetView {
    WidgetKind kind = W_INVALID;
    lv_obj_t *root = nullptr, *main = nullptr, *sub = nullptr;
    lv_obj_t* extra[8] = {};
    lv_obj_t* rows[3] = {};
    uint8_t row_slot[3] = {};
};
struct PageView {
    lv_obj_t *screen = nullptr, *conn = nullptr, *alert = nullptr;
    WidgetView widgets[4];
};

PageView views[6];
uint8_t view_count = 0, current_page = 0;
uint32_t bg = COL_BG, grad = COL_BG, card = COL_CARD, accent = COL_ACCENT;
uint32_t text_color = COL_TEXT, sub_color = COL_SUB;
bool online = false, have_data = false, alarm_active = false;
Telemetry latest;
char pinned_id[9] = "";
char fired_dates[16][11] = {};
uint32_t last_clock_ms = 0, last_render_ms = 0, alarm_started_ms = 0;
uint32_t seen_hms = 0;

// ---- 覆盖面板：0 无 1 启动器 2 控制中心 ----
uint8_t overlay = 0;
lv_obj_t *scr_launcher = nullptr, *scr_control = nullptr;
lv_obj_t *launch_cells[12] = {};
char launch_ids[12][13];
uint8_t launch_count = 0;
lv_obj_t *ctrl_vol_lbl = nullptr, *ctrl_mute_lbl = nullptr, *ctrl_bright_lbl = nullptr;

lv_obj_t* label(lv_obj_t* parent, const char* value, int size, uint32_t color,
                int x, int y, int width) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_label_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_width(obj, width);
    lv_label_set_long_mode(obj, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_color(obj, lv_color_hex(color), 0);
    // 字号→字库映射。16px 中文在 320×480 上笔画间隙仅 1~2px，4bpp 抗锯齿
    // 的灰边会吃掉间隙导致糊字，因此 cn_16 已不再被任何元素引用（省 5.5MB
    // flash）。20px 是可读下限：请求 <=20 一律走 cn_20。
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

lv_obj_t* box(lv_obj_t* parent, int x, int y, int w, int h) {
    if (!parent) return nullptr;
    lv_obj_t* obj = lv_obj_create(parent);
    if (!obj) return nullptr;
    lv_obj_set_pos(obj, x, y);
    lv_obj_set_size(obj, w, h);
    lv_obj_set_style_bg_color(obj, lv_color_hex(card), 0);
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
    if (direction == LV_DIR_BOTTOM && launch_count) {
        open_overlay(1);
        return;
    }
    if (direction == LV_DIR_TOP) {
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
    // 层级修正：主状态必须最大。原先 main=20 / sub=32，导致"等待编辑器活动"
    // 这句辅助说明比"暂无活跃会话"更醒目，视觉权重与信息重要性相反。
    view.sub  = label(view.root, "等待 AI 编辑器活动", 20, sub_color, 14, 44, 276);
    view.main = label(view.root, "暂无会话", 32, text_color, 14, 10, 276);
    view.extra[0] = label(view.root, "", 20, sub_color, 14, 90, 276);
    for (int i = 0; i < 3; ++i) {
        lv_obj_t* row = box(view.root, 10, 134 + 64 * i, 284, 56);
        lv_obj_set_style_bg_color(row, lv_color_hex(bg), 0);
        lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(row, choose_task, LV_EVENT_CLICKED, &view);
        view.rows[i] = row;
        view.extra[i + 1] = label(row, "", 20, sub_color, 8, 16, 268);
    }
}

void make_hardware(WidgetView& view, lv_obj_t* screen) {
    const char* titles[4] = {"处理器", "内存", "显卡", "磁盘"};
    // 竖屏 2×2：每卡 148×186，间距统一 8px（原来水平 8 / 垂直 6 不一致）。
    // 卡内元素按"标题 / 数值 / 说明"成组垂直居中，不再把说明锚在卡底
    // 导致数值与说明相隔 94px、看起来互不相关。
    for (int i = 0; i < 4; ++i) {
        lv_obj_t* panel = box(screen, 8 + (i % 2) * 156, BODY_Y + (i / 2) * 194, 148, 186);
        label(panel, titles[i], 20, sub_color, 12, 34, 124);
        view.extra[i * 2] = label(panel, "--", 32, text_color, 12, 78, 124);
        view.extra[i * 2 + 1] = label(panel, "", 20, sub_color, 12, 128, 124);
    }
}

void make_generic(WidgetView& view, lv_obj_t* screen, int x, int y, int w, int h) {
    view.root = box(screen, x, y, w, h);
    const char* title = "";
    switch (view.kind) {
    case W_CLOCK: title = "本地时间"; break;
    case W_ENVIRONMENT: title = "桌面环境"; break;
    case W_REMINDERS: title = "提醒列表"; break;
    case W_CALENDAR: title = "今日日历"; break;
    case W_ALARM: title = "闹钟"; break;
    default: break;
    }
    label(view.root, title, 20, sub_color, 12, 12, w - 24);
    // compact 阈值随字号上调：20px 正文需要约 76px 才不挤，90 太贴近。
    bool compact = h < 110;
    view.main = label(view.root, "--", compact ? 20 : 28, text_color,
                      12, compact ? 44 : 68, w - 24);
    if (!compact) view.sub = label(view.root, "", 20, sub_color, 12, h - 40, w - 24);
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
    if (!launch_count)
        label(scr_launcher, "未配置应用\n在电脑设置页添加", 24, sub_color, 30, 180, 260);
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
    label(page.screen, config.title, 20, text_color, 10, HEAD_Y, 200);
    page.conn = label(page.screen, online ? "在线" : "电脑离线", 20,
                      online ? 0x34D399 : COL_ERR, 210, HEAD_Y + 2, 100);
    if (index == 0) {
        label(page.screen, "下滑启动应用 · 上滑控制中心", 20, sub_color, 10, 32, 300);
    }
    // 页码按钮（底部居中）。
    // 原实现两处不一致：总宽按 6px 间距算、绘制按 40px 步进，导致圆点组
    // 实际偏左且页数越多偏移越大。同时 34x24 的热区远低于 44x44 可触下限。
    // 现改为：视觉 32x22，但热区扩到 44x44（居中放置），间距统一 10px。
    uint8_t n = view_count;
    const int DOT_W = 32, DOT_H = 22, HIT = 44, GAP = 10;
    int total = n * DOT_W + (n - 1) * GAP;
    int x0 = (SCR_W - total) / 2;
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

void render_ai(WidgetView& view) {
    if (!view.root) return;
    int focus = -1;
    for (int i = 0; i < 4; ++i)
        if (latest.tasks[i].used && pinned_id[0] && !strcmp(latest.tasks[i].id, pinned_id))
            focus = i;
    if (focus < 0)
        for (int i = 0; i < 4 && focus < 0; ++i) if (latest.tasks[i].used) focus = i;
    if (focus < 0) {
        set_text(view.main, "暂无活跃会话");
        set_text(view.sub, "等待编辑器活动");
        set_text(view.extra[0], "Claude Code / Codex / ZCode");
    } else {
        const AITask& task = latest.tasks[focus];
        char line[96];
        snprintf(line, sizeof(line), "%s · %s  #%s", task.tool,
                 task.proj[0] ? task.proj : "未命名项目", task.id);
        set_text(view.main, line);
        const char* status = !strcmp(task.state, "ASK") ? "等待确认" :
            !strcmp(task.state, "WAIT_INPUT") ? "等你回复" :
            !strcmp(task.state, "ERROR") ? "任务出错" :
            !strcmp(task.state, "DONE") ? "已完成" :
            !strcmp(task.state, "TOOL") ? "执行工具" :
            !strcmp(task.state, "THINKING") ? "思考中" : "处理中";
        set_text(view.sub, status);
        snprintf(line, sizeof(line), "运行 %d:%02d · %d 秒前更新 · 日志推断",
                 task.elapse / 60, task.elapse % 60, task.age);
        set_text(view.extra[0], line);
    }
    int row = 0;
    for (int i = 0; i < 4 && row < 3; ++i) {
        if (!latest.tasks[i].used || i == focus) continue;
        const AITask& task = latest.tasks[i];
        char line[100];
        snprintf(line, sizeof(line), "%s · %s #%s   %s", task.tool,
                 task.proj[0] ? task.proj : "未命名", task.id,
                 !strcmp(task.state, "ASK") ? "待确认" :
                 !strcmp(task.state, "ERROR") ? "出错" :
                 !strcmp(task.state, "WAIT_INPUT") ? "等回复" : "处理中");
        set_text(view.extra[row + 1], line);
        view.row_slot[row] = i;
        lv_obj_clear_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
        ++row;
    }
    for (; row < 3; ++row) lv_obj_add_flag(view.rows[row], LV_OBJ_FLAG_HIDDEN);
}

void render_hardware(WidgetView& view) {
    if (!view.root) return;
    char line[48];
    if (!have_data || !online) {
        for (int i = 0; i < 4; ++i) {
            set_text(view.extra[i*2], "--"); set_text(view.extra[i*2+1], "数据不可用");
        }
        return;
    }
    snprintf(line, sizeof(line), "%d%%", latest.cpu); set_text(view.extra[0], line);
    snprintf(line, sizeof(line), "负载%s", latest.cpu_temp_valid ? " · 温度可用" : "");
    set_text(view.extra[1], line);
    snprintf(line, sizeof(line), "%d%%", latest.mem); set_text(view.extra[2], line);
    snprintf(line, sizeof(line), "%.1f/%.1f GB", latest.memused, latest.memtotal);
    set_text(view.extra[3], line);
    if (latest.gpu_valid) {
        snprintf(line, sizeof(line), "%d%%", latest.gpu); set_text(view.extra[4], line);
        set_text(view.extra[5], "负载");
    } else {set_text(view.extra[4], "未提供"); set_text(view.extra[5], "GPU 不可用");}
    snprintf(line, sizeof(line), "%d%%", latest.disk); set_text(view.extra[6], line);
    snprintf(line, sizeof(line), "余 %.0f GB", latest.diskfree); set_text(view.extra[7], line);
}

void render_generic(WidgetView& view) {
    if (!view.root) return;
    char line[96], sub[96];
    line[0] = 0; sub[0] = 0;
    switch (view.kind) {
    case W_CLOCK: {
        if (!latest.date[0]) {strcpy(line, "时间未同步"); break;}
        int secs = clock_seconds();
        snprintf(line, sizeof(line), "%02d:%02d", secs / 3600, (secs / 60) % 60);
        snprintf(sub, sizeof(sub), "%s%s", latest.date,
                 online ? "" : " · 离线估算");
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
        set_text(views[i].conn, connected ? "在线" : "电脑离线");
        lv_obj_set_style_text_color(views[i].conn,
            lv_color_hex(connected ? 0x34D399 : COL_ERR), 0);
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

void ui_theme_tick() {
    uint32_t now = millis();
    alarm_tick();
    if (now - last_render_ms >= 1000) {
        last_render_ms = now;
        render_all();
    }
}
