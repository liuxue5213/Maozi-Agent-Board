/** LVGL 内存池 → PSRAM（8MB），失败时回退内部 RAM。C/C++ 双兼容。 */
#pragma once
#include <stddef.h>
#include <esp_heap_caps.h>

#ifdef __cplusplus
extern "C" {
#endif

void* lv_mem_alloc_ps(size_t size);
void lv_mem_free_ps(void* p);
void* lv_mem_realloc_ps(void* p, size_t size);

#ifdef __cplusplus
}
#endif
