/**
 * @file thermal_sweep_kernel.h
 * @brief ONE source for the per-case thermal sweep, compiled twice: by the C compiler for the CPU reference and by
 *        nvcc (thermal_sweep.cu) for the GPU. The same IEEE double operations in the same order, built on both
 *        sides without fused multiply-add (-ffp-contract=off / -fmad=false), so the two backends agree to the last
 *        bit or within a stated tolerance; the CPU path is the reference.
 *
 * The case: a motor winding and its case (two lumped nodes, [REF-008]) under a duty-cycled current. Copper loss uses
 * the winding's temperature-dependent resistance (lagged one step). Backward Euler on the 2x2 system, solved by
 * Cramer's rule. Output: the peak winding temperature. A sweep over many cases (per-unit tolerances on C, G, R and
 * the load) answers "what fraction of built units exceed the insulation class", the question a single nominal run
 * cannot answer.
 */
#ifndef HIL_TWIN_THERMAL_SWEEP_KERNEL_H
#define HIL_TWIN_THERMAL_SWEEP_KERNEL_H

#if defined(__CUDACC__)
#define TW_HD __host__ __device__
#else
#define TW_HD static inline
#endif

/** @brief One sweep case (all SI, temperatures in degC). Plain old data: copied to the GPU as is. */
typedef struct {
    double c_w, c_c;        /**< winding and case heat capacity, J/K */
    double g_wc, g_ca;      /**< winding-case and case-ambient conductance, W/K */
    double r20, alpha;      /**< winding resistance at 20 degC, ohm; temperature coefficient, 1/K */
    double i_on;            /**< current while on, A */
    double t_on, t_off;     /**< duty cycle, s */
    double t_amb;           /**< ambient, degC */
    double t_total, dt;     /**< run length and step, s */
} tw_sweep_case;

/**
 * @brief Integrate one case; returns the peak winding temperature (degC), or a negative number for an invalid case.
 */
TW_HD double tw_sweep_case_peak(const tw_sweep_case* k)
{
    if (!(k->c_w > 0.0) || !(k->c_c > 0.0) || !(k->g_wc > 0.0) || !(k->g_ca > 0.0) || !(k->dt > 0.0) ||
        !(k->t_total > 0.0) || !(k->t_on + k->t_off > 0.0))
        return -1.0;
    double tw = k->t_amb, tc = k->t_amb, peak = k->t_amb;
    const double period = k->t_on + k->t_off;
    const long n = (long)(k->t_total / k->dt);
    for (long s = 0; s < n; ++s) {
        /* ---- phase of the duty cycle (computed from the step index: no accumulated time error) */
        const double t = (double)s * k->dt;
        const double ph = t - period * (double)(long)(t / period);
        const double i = ph < k->t_on ? k->i_on : 0.0;
        double r = k->r20 * (1.0 + k->alpha * (tw - 20.0));
        if (r < 0.1 * k->r20) r = 0.1 * k->r20;
        const double p = i * i * r;

        /* ---- (C/dt + G) T' = C/dt T + P + g_ca T_amb, 2x2 by Cramer */
        const double a11 = k->c_w / k->dt + k->g_wc, a12 = -k->g_wc;
        const double a21 = -k->g_wc, a22 = k->c_c / k->dt + k->g_wc + k->g_ca;
        const double b1 = k->c_w / k->dt * tw + p;
        const double b2 = k->c_c / k->dt * tc + k->g_ca * k->t_amb;
        const double det = a11 * a22 - a12 * a21;
        tw = (b1 * a22 - a12 * b2) / det;
        tc = (a11 * b2 - b1 * a21) / det;
        if (tw > peak) peak = tw;
    }
    return peak;
}

#endif /* HIL_TWIN_THERMAL_SWEEP_KERNEL_H */
