/**
 * @file trace.c
 * @brief CSV trace with FNV-1a 64 running hash (see trace.h).
 */
#include "hil_twin/trace.h"

#include <string.h>

uint64_t tw_fnv1a64(uint64_t h, const void* data, size_t n)
{
    const unsigned char* p = (const unsigned char*)data;
    for (size_t i = 0; i < n; ++i) {
        h ^= p[i];
        h *= 0x100000001b3ull;
    }
    return h;
}

/** @brief Hash and (when a file is open) write a string. */
static void emit(tw_trace* t, const char* s, size_t n)
{
    t->hash = tw_fnv1a64(t->hash, s, n);
    if (t->f) fwrite(s, 1, n, t->f);
}

int tw_trace_open(tw_trace* t, const char* path, int decimate)
{
    if (!t) return -1;
    memset(t, 0, sizeof(*t));
    t->hash = 0xcbf29ce484222325ull;
    t->decimate = decimate < 1 ? 1 : decimate;
    if (path && *path) {
        /* ---- binary mode: "\n" stays "\n" on Windows, so the file hash matches across platforms */
        t->f = fopen(path, "wb");
        if (!t->f) return -1;
    }
    return 0;
}

void tw_trace_header(tw_trace* t, const char* csv_header)
{
    if (!t || !csv_header) return;
    emit(t, csv_header, strlen(csv_header));
    emit(t, "\n", 1);
}

void tw_trace_row(tw_trace* t, const double* v, int n)
{
    if (!t || !v || n <= 0) return;
    if ((t->calls++ % (uint64_t)t->decimate) != 0) return;
    char buf[64];
    for (int i = 0; i < n; ++i) {
        int w = snprintf(buf, sizeof buf, i ? ",%.9g" : "%.9g", v[i]);
        if (w > 0) emit(t, buf, (size_t)w);
    }
    emit(t, "\n", 1);
    ++t->rows;
}

void tw_trace_close(tw_trace* t)
{
    if (!t) return;
    if (t->f) fclose(t->f);
    t->f = NULL;
}
