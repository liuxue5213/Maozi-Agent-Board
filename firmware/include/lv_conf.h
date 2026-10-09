/**
 * LVGL 8.3 配置（精简：未定义项走 lv_conf_internal.h 默认值）
 */
#pragma once
#define LV_CONF_H

#define LV_COLOR_DEPTH 16
#define LV_COLOR_16_SWAP 0

/* LVGL 对象堆放 PSRAM（8MB），内部 RAM 不足时自动回退——
   6 页 + 启动器 + 控制中心的控件规模远超内部 RAM 小池。 */
#define LV_MEM_CUSTOM 1
#define LV_MEM_CUSTOM_INCLUDE "lv_mem_ps.h"
#define LV_MEM_CUSTOM_ALLOC lv_mem_alloc_ps
#define LV_MEM_CUSTOM_FREE lv_mem_free_ps
#define LV_MEM_CUSTOM_REALLOC lv_mem_realloc_ps

#define LV_FONT_FMT_TXT_LARGE 1
#define LV_DISP_DEF_REFR_PERIOD 16
#define LV_INDEV_DEF_READ_PERIOD 20
#define LV_DPI_DEF 130

#define LV_TICK_CUSTOM 1
#define LV_TICK_CUSTOM_INCLUDE "Arduino.h"
#define LV_TICK_CUSTOM_SYS_TIME_EXPR ((uint32_t)millis())

/* 中文使用 src/fonts 中的 Noto Sans CJK 字库（16/24 全 GB2312）。 */
#define LV_FONT_MONTSERRAT_12 1
#define LV_FONT_MONTSERRAT_16 1
#define LV_FONT_MONTSERRAT_20 1
#define LV_FONT_MONTSERRAT_24 1
#define LV_FONT_MONTSERRAT_28 1
#define LV_FONT_MONTSERRAT_32 1
#define LV_FONT_MONTSERRAT_40 1
#define LV_FONT_MONTSERRAT_48 1
#define LV_FONT_DEFAULT &lv_font_montserrat_16

/* 手势更易触发（默认 50px，竖滑易被误判为横滑） */
#define LV_INDEV_DEF_GESTURE_LIMIT 30
