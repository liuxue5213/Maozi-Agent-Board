#pragma once
#include "proto.h"
#include <Arduino.h>

void ui_init();
void ui_rebuild();
void ui_update(const Telemetry& t);      // 新数据到达时刷新界面
void ui_set_online(bool online);         // 链路状态（心跳超时判断）
// 主题推送（hex 颜色 0xRRGGBB），立即生效并整屏重建
void ui_apply_theme(uint32_t bg, uint32_t card, uint32_t accent,
                    uint32_t text, uint32_t sub, uint32_t grad = 0);
void ui_theme_tick();                 // 页面切换动画结束后应用待处理主题
void ui_set_idle_dim(int seconds);    // 空闲息屏阈值（0=关闭）
void ui_set_saver(uint8_t style);             // 息屏主题 0-3
void ui_set_saver_msg(const char* msg);       // 诗词主题自定义短句
void ui_set_topbar(bool clock_on, bool wx_on); // 顶栏时钟/天气开关
void ui_notify_page(const char* page);   // 页面切换后回传 TOUCH
