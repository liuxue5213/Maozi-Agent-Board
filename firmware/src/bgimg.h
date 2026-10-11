#include <lvgl.h>
/**
 * 背景图：接收（IMG=BEGIN/DATA/END 分帧事务）→ LittleFS /bg.jpg →
 * JPEG 解码到 PSRAM RGB565 → 提供 LVGL 图像源给各屏幕做背景层。
 */
#pragma once
#include <Arduino.h>

// IMG= 事务喂入口（proto.cpp 转发）。返回 true = 一张新图已应用。
bool bgimg_receive_cfg(const char* value);
// 删除 /bg.jpg 并清除背景（"恢复纯色"）
bool bgimg_delete();
// 启动时调用：确认文件系统并解码已存的背景图（如有）
bool bgimg_init();
// 解码 /bg.jpg 到 PSRAM（启动或新图应用后调用）；返回是否成功
bool bgimg_decode();
// LVGL 图像源是否可用（解码成功且未被删除）
bool bgimg_ready();
// lv_img_dsc_t 指针（仅 bgimg_ready() 为 true 时有效）
const void* bgimg_source();

// 屏保相册（/sv1..8.jpg，独立 PSRAM 缓冲，不干扰页面背景）
bool album_decode_index(uint8_t idx);
const lv_img_dsc_t* album_source();
uint8_t album_count();
void album_delete(const char* name);

// 角色图（/pet_<状态>.jpg）：单缓冲按需解码；供宠物页在状态切换时换图
bool petimg_exists(const char* name);
bool petimg_decode_name(const char* name);
const lv_img_dsc_t* petimg_source();
const char* petimg_loaded();   // 当前缓冲对应的帧名（空=无）
