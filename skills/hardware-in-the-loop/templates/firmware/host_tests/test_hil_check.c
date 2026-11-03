/**
 * @file    test_hil_check.c
 * @brief   Self-test of hil_check.h: proves the macros count, compare and summarise correctly, including the rules
 *          that keep a broken run from looking green (NaN never passes a tolerance, an empty suite fails).
 *
 * Interpreter: any C99 compiler. Build and run:
 *     gcc -std=c99 -Wall -Wextra -O2 -o .tmp/test_hil_check test_hil_check.c -lm && .tmp/test_hil_check
 * Expected last line: "[hil] hil_check: N/N checks passed, 0 failed".
 */
#define HIL_CHECK_IMPLEMENTATION
#include "hil_check.h"

#include <math.h>
#include <string.h>

/**
 * @brief  Runs a block of checks against a scratch state and returns how many of them failed.
 *         The real totals are saved and restored around it, so deliberate failures do not count against the suite.
 */
static unsigned long failures_of(void (*fn)(void))
{
    hil_check_state saved = hil_check_g;
    unsigned long failed;
    hil_check_g.total = 0ul; hil_check_g.failed = 0ul; hil_check_g.shown = HIL_CHECK_MAX_SHOWN;   /* silence */
    fn();
    failed = hil_check_g.failed;
    hil_check_g = saved;
    return failed;
}

/* ---- blocks with a known number of failures ------------------------------------------------------------------- */
static void two_bad_ints(void)   { HIL_CHECK_EQ_INT(3, 4); HIL_CHECK_EQ_INT(-1, 1); HIL_CHECK_EQ_INT(7, 7); }
static void nan_and_inf(void)    { HIL_CHECK_NEAR(NAN, 0.0, 1e9); HIL_CHECK_NEAR(INFINITY, INFINITY, 1.0); HIL_CHECK_NEAR(1.0, 1.0, NAN); }
static void near_edges(void)     { HIL_CHECK_NEAR(1.0, 1.1, 0.1000001); HIL_CHECK_NEAR(1.0, 1.2, 0.1); }
static void plain_bools(void)    { HIL_CHECK(1); HIL_CHECK(0); HIL_CHECK(strcmp("a", "a") == 0); }

int main(void)
{
    /* integer comparison: exactly the two unequal pairs fail */
    HIL_CHECK_EQ_INT(failures_of(two_bad_ints), 2);
    /* NaN / infinity in any operand or tolerance always fails */
    HIL_CHECK_EQ_INT(failures_of(nan_and_inf), 3);
    /* tolerance is inclusive and exact at the edge */
    HIL_CHECK_EQ_INT(failures_of(near_edges), 1);
    /* plain booleans */
    HIL_CHECK_EQ_INT(failures_of(plain_bools), 1);

    /* an empty suite must report failure: summarise a fresh state, then restore */
    {
        hil_check_state saved = hil_check_g;
        int rc;
        hil_check_g.total = 0ul; hil_check_g.failed = 0ul; hil_check_g.shown = 0ul;
        rc = hil_check_summary("empty_suite_probe");
        hil_check_g = saved;
        HIL_CHECK(rc == 1);
    }
    return hil_check_summary("hil_check");
}
