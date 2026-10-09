/**
 * WorkBuddy 硬件引脚与主题配色（必须与需求文档引脚表一致）
 */
#pragma once

// ---- 屏幕 ST7796 SPI ----
#define PIN_SCK   12
#define PIN_MOSI  11
#define PIN_CS    10
#define PIN_DC    13
#define PIN_RST   14
#define PIN_BL    6    // 背光 PWM

// ---- 触摸 FT6236 I2C ----
#define PIN_TSDA  8
#define PIN_TSCL  9

// ---- DHT11 ----
#define PIN_DHT   4

// ---- 预留（本期不启用，引脚已锁定）----
#define PIN_LED1  1
#define PIN_LED2  2
#define PIN_LED3  3
#define PIN_LED4  5
#define PIN_PIR   7

// ---- 面板颜色序（BGR 位）----
// 实机配色核验文档 §2 判定：照片里卡片呈草绿（#3F6A07/#8EBB53），
// 而「紫夜」卡片应为 #3A2D5C（紫），并指出唯一吻合的变换是 **G/B 互换**。
//
// ⚠️ 重要：LovyanGFX 的 panel.config().rgb_order 对应 ST7796 的 MADCTL 第3位
//    (BGR)，它只能表达 **R/B 互换**，无法表达 G/B 互换。
//    因此在未做下面的实机红/绿/蓝验证之前，**不要**盲目把 rgb_order 改成 true
//    —— 那只会把「G/B 错位」变成「叠加了 R/B 错位」，配色更糟。
//
// 两分钟定论实验（改 PANEL_COLOR_SELFTEST 后烧录，拍一张照片即可）：
//     1. 屏幕会分成 4 条纯色带：上=纯红 FF0000 / 中上=纯绿 00FF00
//        / 中下=纯蓝 0000FF / 下=纯白 FFFFFF
//     2. 对照实际显示的颜色，把 PANEL_COLOR_ORDER_BGR 设为 true / false：
//        · 红带显示蓝、蓝带显示红      → 设true（R/B 互换）
//        · 红带显示绿、绿带显示红      → 是 G/B 错位，rgb_order 解决不了，
//                                      需查 LV_COLOR_16_SWAP 与 MADCTL 组合
//        · 红绿蓝三条显示正确          → 保持 false，问题不在颜色序
#define PANEL_COLOR_ORDER_BGR false   // ST7796 MADCTL BGR 位；默认 RGB
#define PANEL_COLOR_SELFTEST 0        // 置1 刷四色校验屏

// ---- 主题（极简科技风：深蓝底 + 青蓝高亮 + 白色主文字）----
// 卡片色必须与 bg 拉开亮度差：原 #141E32 对 #0B1220 仅 1.12:1（屏幕底部
// 渐变处 1.03:1），实机卡片边界等于不存在。现用 #274064：
//   card vs bg顶 1.79:1 / vs bg底 1.64:1（均 ≥1.5 底线）
//   text vs card 仍达 9.8:1，sub vs card 5.5:1 均可读。
// 配合 box() 的 1px COL_CARD_EDGE 描边，边界在弱对比屏上也能辨认。
//
// ⚠️ 这些只是**编译期兜底**。运行时的唯一真源是 profile.json 经 CFG 下发的
//    theme（见实机配色核验文档 §1）。排查配色问题先读 profile。
#define COL_BG        0x060A12
#define COL_CARD      0x28384E
#define COL_CARD_EDGE 0x3C5070
#define COL_ACCENT    0x4CC9F0
#define COL_TEXT      0xF1F5F9
#define COL_SUB       0xA6B6C9
#define COL_OK        0x34D399
#define COL_WARN      0xFBBF24
#define COL_ERR       0xF87171

#define DEFAULT_BRIGHT 50   // 0-100
#define LINK_TIMEOUT_MS 12000  // 超过该时间无客户端心跳判定离线
