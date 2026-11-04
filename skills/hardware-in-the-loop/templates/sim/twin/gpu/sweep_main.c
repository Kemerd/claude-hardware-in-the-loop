/**
 * @file sweep_main.c
 * @brief hil_twin_sweep: thermal Monte Carlo over per-unit tolerances, on the CPU or the GPU, and the GPU check.
 *
 * Usage:
 *     hil_twin_sweep [--n 100000] [--seed 1] [--backend cpu|gpu|auto] [--limit-c 130]
 *     hil_twin_sweep --check [--n 4096]        GPU vs CPU on the same cases (needs HIL_GPU=1 and a GPU)
 * Output:
 *     [sweep] n=<n> backend=<cpu|cuda> mean=<degC> p50=<> p99=<> max=<> over_limit=<fraction> (<why>)
 *     --check: [hil] gpu_sweep: P/T checks passed, F failed
 *
 * The nominal case and its tolerances below are the example rig's (sim/twin/params/example_rig.txt): a project
 * replaces them with its own and cites where each sigma comes from (datasheet tolerance, bench spread).
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "gpu_sweep.h"
#include "hil_twin/rng.h"

/** @brief Build n cases: the nominal plus normal per-unit draws (deterministic from the seed). */
static void make_cases(tw_sweep_case* c, int n, uint64_t seed)
{
    tw_rng r;
    tw_rng_seed(&r, seed, tw_stream_user + 7);
    for (int i = 0; i < n; ++i) {
        tw_sweep_case k;
        k.c_w = 8.0 * (1.0 + 0.05 * tw_rng_normal(&r));     /* copper mass tolerance */
        k.c_c = 60.0 * (1.0 + 0.05 * tw_rng_normal(&r));
        k.g_wc = 0.5 * (1.0 + 0.10 * tw_rng_normal(&r));    /* potting / winding contact spread */
        k.g_ca = 0.25 * (1.0 + 0.10 * tw_rng_normal(&r));   /* mounting and airflow spread */
        k.r20 = 2.0 * (1.0 + 0.03 * tw_rng_normal(&r));     /* datasheet resistance tolerance */
        k.alpha = 0.00393;
        k.i_on = 2.4 * (1.0 + 0.05 * tw_rng_normal(&r));    /* load spread of the duty */
        k.t_on = 20.0;
        k.t_off = 10.0;
        k.t_amb = 25.0 + 20.0 * tw_rng_uniform(&r);          /* the specified ambient range, 25..45 degC */
        k.t_total = 900.0;
        k.dt = 0.05;
        c[i] = k;
    }
}

/** @brief Sort helper. */
static int cmp_d(const void* a, const void* b)
{
    double x = *(const double*)a, y = *(const double*)b;
    return (x > y) - (x < y);
}

int main(int argc, char** argv)
{
    int n = 100000, check = 0;
    unsigned long long seed = 1;
    double limit = 130.0;
    tw_backend be = TW_BACKEND_AUTO;
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--n") && i + 1 < argc) n = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--seed") && i + 1 < argc) seed = strtoull(argv[++i], NULL, 10);
        else if (!strcmp(argv[i], "--limit-c") && i + 1 < argc) limit = atof(argv[++i]);
        else if (!strcmp(argv[i], "--check")) check = 1;
        else if (!strcmp(argv[i], "--backend") && i + 1 < argc) {
            const char* b = argv[++i];
            be = !strcmp(b, "cpu") ? TW_BACKEND_CPU : !strcmp(b, "gpu") ? TW_BACKEND_GPU : TW_BACKEND_AUTO;
        } else {
            fprintf(stderr, "usage: hil_twin_sweep [--n N] [--seed S] [--backend cpu|gpu|auto] [--limit-c C] [--check]\n");
            return 2;
        }
    }
    if (check && n > 4096) n = 4096;
    if (n < 1 || n > 10000000) {
        fprintf(stderr, "[sweep] --n out of range\n");
        return 2;
    }
    tw_sweep_case* c = (tw_sweep_case*)malloc(sizeof(tw_sweep_case) * (size_t)n);
    double* a = (double*)malloc(sizeof(double) * (size_t)n);
    double* b = (double*)malloc(sizeof(double) * (size_t)n);
    if (!c || !a || !b) {
        fprintf(stderr, "[sweep] out of memory\n");
        return 2;
    }
    make_cases(c, n, seed);
    char why[256];
    int used = 0;

    /* ---- the GPU check: same cases on both backends, the largest relative difference */
    if (check) {
        int pass = 0, total = 0;
        tw_sweep_run(c, n, a, TW_BACKEND_CPU, &used, why, sizeof why);
        tw_sweep_run(c, n, b, TW_BACKEND_GPU, &used, why, sizeof why);
        ++total;
        if (used) ++pass;
        else printf("FAIL the GPU did not run: %s\n", why);
        double worst = 0.0;
        for (int i = 0; used && i < n; ++i) {
            double d = fabs(a[i] - b[i]) / fmax(1.0, fabs(a[i]));
            if (d > worst || isnan(d)) worst = isnan(d) ? INFINITY : d;
        }
        ++total;
        if (used && worst <= 1e-12) ++pass;
        else if (used) printf("FAIL GPU vs CPU max relative difference %.3g > 1e-12\n", worst);
        printf("[sweep] check n=%d %s, max rel diff %.3g\n", n, why, worst);
        printf("[hil] gpu_sweep: %d/%d checks passed, %d failed\n", pass, total, total - pass);
        free(c); free(a); free(b);
        return pass == total ? 0 : 1;
    }

    /* ---- the sweep */
    tw_sweep_run(c, n, a, be, &used, why, sizeof why);
    double sum = 0.0;
    int over = 0, bad = 0;
    for (int i = 0; i < n; ++i) {
        if (a[i] < -0.5) ++bad;
        sum += a[i];
        if (a[i] > limit) ++over;
    }
    qsort(a, (size_t)n, sizeof(double), cmp_d);
    printf("[sweep] n=%d backend=%s mean=%.3f p50=%.3f p99=%.3f max=%.3f over_limit=%.5f invalid=%d (%s)\n", n,
           used ? "cuda" : "cpu", sum / n, a[n / 2], a[(int)(0.99 * (n - 1))], a[n - 1], (double)over / n, bad, why);
    free(c); free(a); free(b);
    return bad ? 1 : 0;
}
