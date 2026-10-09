#include "lv_mem_ps.h"

extern "C" {

void* lv_mem_alloc_ps(size_t size) {
    void* p = heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return p ? p : malloc(size);
}

void lv_mem_free_ps(void* p) {
    heap_caps_free(p);
}

void* lv_mem_realloc_ps(void* p, size_t size) {
    void* n = heap_caps_realloc(p, size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    return n ? n : realloc(p, size);
}

}
