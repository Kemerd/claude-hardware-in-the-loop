/**
 * @file trace.h
 * @brief CSV trace writer with a running FNV-1a 64-bit hash of every byte written.
 *
 * The hash is the determinism check: run the same scenario with the same seed twice and the two hashes (and the
 * SHA-256 of the two files, which CI computes) must match. A difference means hidden state: uninitialised memory,
 * wall-clock time, iteration over a hash container, a thread race, or a library function that differs between
 * runs. Numbers are printed with a fixed format ("%.9g") so the text itself is reproducible.
 * The hash runs even with no file open (hosts that only want the number).
 */
#ifndef HIL_TWIN_TRACE_H
#define HIL_TWIN_TRACE_H

#include <stdint.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Trace writer. */
typedef struct {
    FILE* f;          /**< may be NULL: hash only */
    uint64_t hash;    /**< FNV-1a 64 of every byte so far */
    uint64_t rows;
    int decimate;     /**< write every n-th row (>= 1) */
    uint64_t calls;
} tw_trace;

/** @brief Start a trace; path NULL = hash only. @return 0 ok, -1 the file could not be opened */
int tw_trace_open(tw_trace* t, const char* path, int decimate);
/** @brief Write the header line (always written, never decimated). */
void tw_trace_header(tw_trace* t, const char* csv_header);
/** @brief Offer one row of n values; written and hashed when the decimation counter says so. */
void tw_trace_row(tw_trace* t, const double* v, int n);
/** @brief Close the file (the hash stays readable). */
void tw_trace_close(tw_trace* t);
/** @brief FNV-1a 64 over a buffer, continuing from h (start with 0xcbf29ce484222325). */
uint64_t tw_fnv1a64(uint64_t h, const void* data, size_t n);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_TRACE_H */
