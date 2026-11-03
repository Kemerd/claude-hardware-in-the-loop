/**
 * @file    hil_check.h
 * @brief   Header-only check macros for host-compiled firmware test suites (C99 / C++11, no dependencies).
 *
 * The firmware's control code (estimators, controllers, state machines, protocol codecs) is compiled for the PC and
 * exercised by thousands of checks that run in CI without hardware. Every suite ends with ONE summary line that the CI
 * runner parses, so a crash or a truncated run can never look green:
 *
 *     [hil] <suite>: <passed>/<total> checks passed, <failed> failed
 *
 * Usage (one translation unit per suite):
 * @code
 *   #define HIL_CHECK_IMPLEMENTATION      // in exactly one .c file of the suite
 *   #include "hil_check.h"
 *   int main(void) {
 *       HIL_CHECK(crc16(buf, 4) == 0x29B1u);
 *       HIL_CHECK_NEAR(est.alt_m, 1.25, 0.01);
 *       HIL_CHECK_EQ_INT(sm.state, STATE_ARMED);
 *       return hil_check_summary("estimator");   // prints the summary line, returns 0 only when nothing failed
 *   }
 * @endcode
 *
 * Interpreter: any C99 or C++11 compiler (gcc, clang, MSVC). Build: see test_hil_check.c.
 * @note  Not thread-safe by design: suites run their checks on one thread; a multi-threaded test joins first.
 */
#ifndef HIL_CHECK_H
#define HIL_CHECK_H

#include <math.h>
#include <stdio.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Running totals of one suite (one instance per test executable). */
typedef struct hil_check_state {
    unsigned long total;   /**< checks evaluated */
    unsigned long failed;  /**< checks that failed */
    unsigned long shown;   /**< failure messages printed (capped, see HIL_CHECK_MAX_SHOWN) */
} hil_check_state;

/** @brief Failure messages printed before further ones are only counted (keeps logs readable). */
#ifndef HIL_CHECK_MAX_SHOWN
#define HIL_CHECK_MAX_SHOWN 200ul
#endif

/** @brief The suite's totals (defined in the translation unit that sets HIL_CHECK_IMPLEMENTATION). */
extern hil_check_state hil_check_g;

/**
 * @brief  Records one check result and prints a failure line with file, line and expression.
 * @param  ok    non-zero when the check passed
 * @param  expr  the checked expression as text (never NULL: the macros pass a string literal)
 * @param  file  source file of the check
 * @param  line  source line of the check
 * @param  detail optional extra text (values that were compared), may be NULL
 * @return ok, so a test can branch on a failed precondition
 */
int hil_check_record(int ok, const char *expr, const char *file, int line, const char *detail);

/**
 * @brief  Prints the suite's summary line in the exact format the CI runner parses.
 * @param  suite  suite name (NULL prints "suite")
 * @return 0 when no check failed AND at least one check ran, 1 otherwise (an empty suite is a failure)
 */
int hil_check_summary(const char *suite);

/** @brief Plain boolean check. */
#define HIL_CHECK(expr) hil_check_record(!!(expr), #expr, __FILE__, __LINE__, NULL)

/** @brief Integer equality with both values in the failure message (printed through double: old C runtimes lack %lld). */
#define HIL_CHECK_EQ_INT(a, b) do {                                                          \
        long long hil_a_ = (long long)(a), hil_b_ = (long long)(b);                          \
        char hil_d_[96];                                                                     \
        snprintf(hil_d_, sizeof hil_d_, "%.17g != %.17g", (double)hil_a_, (double)hil_b_);   \
        hil_check_record(hil_a_ == hil_b_, #a " == " #b, __FILE__, __LINE__, hil_d_);        \
    } while (0)

/** @brief |a - b| <= tol, with NaN and infinity always failing (a NaN never sneaks through a tolerance). */
#define HIL_CHECK_NEAR(a, b, tol) do {                                                       \
        double hil_a_ = (double)(a), hil_b_ = (double)(b), hil_t_ = (double)(tol);           \
        int hil_ok_ = isfinite(hil_a_) && isfinite(hil_b_) && isfinite(hil_t_) &&            \
                      fabs(hil_a_ - hil_b_) <= hil_t_;                                       \
        char hil_d_[128];                                                                    \
        snprintf(hil_d_, sizeof hil_d_, "%.9g vs %.9g (tol %.3g)", hil_a_, hil_b_, hil_t_);  \
        hil_check_record(hil_ok_, #a " ~= " #b, __FILE__, __LINE__, hil_d_);                 \
    } while (0)

#ifdef HIL_CHECK_IMPLEMENTATION
hil_check_state hil_check_g = {0ul, 0ul, 0ul};

int hil_check_record(int ok, const char *expr, const char *file, int line, const char *detail)
{
    hil_check_g.total++;
    if (ok) {
        return 1;
    }
    hil_check_g.failed++;
    /* cap the printed failures; the count stays exact */
    if (hil_check_g.shown < HIL_CHECK_MAX_SHOWN) {
        hil_check_g.shown++;
        fprintf(stdout, "FAIL %s:%d: %s%s%s\n", file ? file : "?", line, expr ? expr : "?",
                detail ? "  -> " : "", detail ? detail : "");
    }
    return 0;
}

int hil_check_summary(const char *suite)
{
    unsigned long passed = hil_check_g.total - hil_check_g.failed;
    fprintf(stdout, "[hil] %s: %lu/%lu checks passed, %lu failed\n", suite ? suite : "suite", passed,
            hil_check_g.total, hil_check_g.failed);
    fflush(stdout);
    return (hil_check_g.failed == 0ul && hil_check_g.total > 0ul) ? 0 : 1;
}
#endif /* HIL_CHECK_IMPLEMENTATION */

#ifdef __cplusplus
}
#endif

#endif /* HIL_CHECK_H */
