/**
 * @file rng.c
 * @brief xoshiro256** and splitmix64, transcribed from the reference C code of [REF-002] (public domain).
 */
#include "hil_twin/rng.h"

#include <math.h>

/** @brief splitmix64 step: used only to expand a 64-bit seed into the 256-bit xoshiro state. */
static uint64_t splitmix64(uint64_t* x)
{
    uint64_t z = (*x += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

/** @brief Rotate left. */
static inline uint64_t rotl(uint64_t x, int k) { return (x << k) | (x >> (64 - k)); }

void tw_rng_seed(tw_rng* r, uint64_t seed, uint64_t stream)
{
    if (!r) return;
    /* ---- mix the stream id in with an odd multiplier so (seed, stream) pairs never collide trivially */
    uint64_t x = seed ^ (stream * 0xD1B54A32D192ED03ull);
    for (int i = 0; i < 4; ++i) r->s[i] = splitmix64(&x);
    /* ---- the all-zero state is the one fixed point of xoshiro; splitmix64 cannot produce it, but be explicit */
    if ((r->s[0] | r->s[1] | r->s[2] | r->s[3]) == 0) r->s[0] = 1;
}

uint64_t tw_rng_next(tw_rng* r)
{
    if (!r) return 0;
    uint64_t* s = r->s;
    const uint64_t result = rotl(s[1] * 5, 7) * 9;
    const uint64_t t = s[1] << 17;
    s[2] ^= s[0];
    s[3] ^= s[1];
    s[1] ^= s[2];
    s[0] ^= s[3];
    s[2] ^= t;
    s[3] = rotl(s[3], 45);
    return result;
}

double tw_rng_uniform(tw_rng* r)
{
    /* top 53 bits -> [0, 1) exactly representable */
    return (double)(tw_rng_next(r) >> 11) * (1.0 / 9007199254740992.0);
}

double tw_rng_normal(tw_rng* r)
{
    /* ---- u1 in (0, 1]: never log(0) */
    double u1 = 1.0 - tw_rng_uniform(r);
    double u2 = tw_rng_uniform(r);
    return sqrt(-2.0 * log(u1)) * cos(6.283185307179586 * u2);
}
