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

// ---- 面板颜色校准 ----
#define PANEL_COLOR_ORDER_BGR false
#define PANEL_COLOR_SELFTEST 0        // 置1 进入 24 格 RGB565 诊断矩阵
// 2026-10-10 真机 24 格 RGB565 矩阵：用户确认第 7 格最准确。
// 第 7 格=RGB 通道不变、交换每个 RGB565 像素的两个字节、无软件反相。
// 硬件 invert=true 保留；在 LVGL flush 时换字节，混合色也能正确显示。
#define PANEL_COLOR_BYTESWAP 1

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
