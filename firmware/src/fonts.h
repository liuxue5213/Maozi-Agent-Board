/**
 * Noto Sans CJK 简体中文字库（tools/gen_fonts.py 生成）。
 * 20/24 覆盖 GB2312 全字（用户自定义文字永不缺字）；
 * 32/48 仅含界面文案字，缺字回退到 24。
 *
 * 字号选择：20px 是 320×480 上的可读下限。16px 中文笔画间隙只有 1~2px，
 * 4bpp 抗锯齿的灰边会吃掉间隙导致糊字，故UI 正文一律 >= 20px，
 * cn_16 已不再被ui.cpp 引用（保留声明以便回退）。
 */
#pragma once
#include <lvgl.h>

extern const lv_font_t cn_16;
extern const lv_font_t cn_20;
extern const lv_font_t cn_24;
extern const lv_font_t cn_32;
extern const lv_font_t cn_48;
