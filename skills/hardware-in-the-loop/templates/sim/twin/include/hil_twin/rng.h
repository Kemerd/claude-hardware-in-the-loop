/**
 * @file rng.h
 * @brief Deterministic random streams for the twin: xoshiro256** seeded through splitmix64 [REF-002].
 *
 * Why not rand() or <random>: the C library generator differs between platforms and C++ distributions are not
 * specified bit for bit, so the same seed would give a different trace on another compiler. These generators are a
 * few lines of integer arithmetic and give the same stream everywhere.
 *
 * One stream per noise source: every sensor or disturbance owns its own tw_rng, seeded from (run seed, stream id).
 * Adding a new sensor then never shifts the numbers an existing sensor sees, so old traces stay comparable.
 */
#ifndef HIL_TWIN_RNG_H
#define HIL_TWIN_RNG_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief xoshiro256** state (never all zero; tw_rng_seed guarantees that). */
typedef struct { uint64_t s[4]; } tw_rng;

/**
 * @brief Seed a stream from the run seed and a stream id.
 * @param r       stream to initialise (ignored when NULL)
 * @param seed    the run seed (the scenario seed of a sweep)
 * @param stream  a constant per noise source (tw_stream_* below); distinct ids give independent streams
 */
void tw_rng_seed(tw_rng* r, uint64_t seed, uint64_t stream);

/** @brief Next raw 64-bit value. */
uint64_t tw_rng_next(tw_rng* r);

/** @brief Uniform double in [0, 1) with 53 random bits. */
double tw_rng_uniform(tw_rng* r);

/**
 * @brief Standard normal draw (mean 0, sigma 1) by the Box-Muller transform.
 * @note Uses exactly two uniforms per call and keeps no cached second value, so the stream position after N draws is
 *       always 2N: easy to reason about when a trace is compared step by step.
 */
double tw_rng_normal(tw_rng* r);

/** @brief Stream ids (keep them stable; append new ones at the end). */
enum {
    tw_stream_gyro = 1,
    tw_stream_accel = 2,
    tw_stream_baro = 3,
    tw_stream_range = 4,
    tw_stream_adc = 5,
    tw_stream_unit = 6, /* per-unit tolerance draws (scale factors, offsets) made once at create */
    tw_stream_user = 100 /* first id free for project models */
};

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_RNG_H */
