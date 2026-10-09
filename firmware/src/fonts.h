/**
 * Noto Sans CJK 简体中文字库（tools/gen_fonts.py 生成，Medium 字重）。
 * 20/24 覆盖 GB2312 全字（用户自定义文字永不缺字）；
 * 32/48 仅含界面文案字，缺字回退到 24。
 *
 * 字号选择：20px 是 320×480 上的可读下限，故 UI 正文一律 >= 20px。
 * cn_16 已彻底移除（small 字号在 4bpp 抗锯齿下糊化），ui.cpp 的 label()
 * 把 <=20px 请求统一映射到 cn_20，不存在 16px 渲染路径。
 */
#pragma once
#include <lvgl.h>

extern const lv_font_t cn_20;
extern const lv_font_t cn_24;
extern const lv_font_t cn_32;
extern const lv_font_t cn_48;
