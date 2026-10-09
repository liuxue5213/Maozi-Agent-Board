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

// ---- 主题（极简科技风：深蓝底 + 青蓝高亮 + 白色主文字）----
// 卡片色必须与 bg 拉开亮度差：原 #141E32 对 #0B1220 仅 1.12:1（屏幕底部
// 渐变处 1.03:1），实机卡片边界等于不存在。现用 #274064：
//   card vs bg顶 1.79:1 / vs bg底 1.64:1（均 ≥1.5 底线）
//   text vs card 仍达 9.8:1，sub vs card 5.5:1 均可读。
// 配合 box() 的 1px COL_CARD_EDGE 描边，边界在弱对比屏上也能辨认。
#define COL_BG        0x0B1220
#define COL_CARD      0x274064
#define COL_CARD_EDGE 0x3E506E
#define COL_ACCENT    0x22D3EE
#define COL_TEXT      0xF3F7FF
#define COL_SUB       0xA8BDD9
#define COL_OK        0x34D399
#define COL_WARN      0xFBBF24
#define COL_ERR       0xF87171

#define DEFAULT_BRIGHT 90   // 0-100
#define LINK_TIMEOUT_MS 12000  // 超过该时间无客户端心跳判定离线
