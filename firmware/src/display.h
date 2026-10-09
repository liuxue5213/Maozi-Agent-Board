#pragma once
#include <LovyanGFX.hpp>

extern lgfx::LGFX_Device* gfx();  // 单例访问

void display_init();       // 屏幕初始化 + LVGL 驱动注册
void display_brightness(int percent);  // 0-100 PWM 调光
int  display_brightness_get();
