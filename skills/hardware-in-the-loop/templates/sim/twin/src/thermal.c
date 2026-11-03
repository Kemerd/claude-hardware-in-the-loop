/**
 * @file thermal.c
 * @brief Lumped thermal network, backward Euler, dense solve with partial pivoting (see thermal.h).
 */
#include "hil_twin/thermal.h"

#include <math.h>
#include <string.h>

/* ------------------------------------------------------------------------------------------------------------------
 *  Linear solve: A x = b, n <= TW_THERMAL_MAX_NODES, Gaussian elimination with partial pivoting, in place
 * ------------------------------------------------------------------------------------------------------------------ */

/**
 * @brief Solve in place.
 * @param n  size
 * @param A  n x n row-major matrix in a TW_THERMAL_MAX_NODES stride (destroyed)
 * @param b  right-hand side (replaced by the solution)
 * @return 0 ok, -1 singular
 */
static int solve(int n, double A[TW_THERMAL_MAX_NODES][TW_THERMAL_MAX_NODES], double* b)
{
    for (int k = 0; k < n; ++k) {
        /* ---- pivot: the largest magnitude in column k at or below the diagonal */
        int piv = k;
        for (int r = k + 1; r < n; ++r)
            if (fabs(A[r][k]) > fabs(A[piv][k])) piv = r;
        if (!(fabs(A[piv][k]) > 1e-300)) return -1;
        if (piv != k) {
            for (int c = 0; c < n; ++c) {
                double t = A[k][c];
                A[k][c] = A[piv][c];
                A[piv][c] = t;
            }
            double t = b[k];
            b[k] = b[piv];
            b[piv] = t;
        }
        /* ---- eliminate below */
        for (int r = k + 1; r < n; ++r) {
            double f = A[r][k] / A[k][k];
            if (f == 0.0) continue;
            for (int c = k; c < n; ++c) A[r][c] -= f * A[k][c];
            b[r] -= f * b[k];
        }
    }
    /* ---- back substitution */
    for (int k = n - 1; k >= 0; --k) {
        double s = b[k];
        for (int c = k + 1; c < n; ++c) s -= A[k][c] * b[c];
        b[k] = s / A[k][k];
        if (!isfinite(b[k])) return -1;
    }
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Validation
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_thermal_validate(const tw_thermal* th)
{
    if (!th || th->n < 1 || th->n > TW_THERMAL_MAX_NODES) return -1;
    if (th->n_links < 0 || th->n_links > TW_THERMAL_MAX_LINKS || !isfinite(th->t_amb_c)) return -1;
    for (int i = 0; i < th->n; ++i) {
        if (!(th->c[i] > 0.0) || !isfinite(th->c[i])) return -1;
        if (!(th->g_amb[i] >= 0.0) || !isfinite(th->g_amb[i])) return -1;
        if (!isfinite(th->t_c[i]) || !isfinite(th->p_w[i])) return -1;
    }
    for (int k = 0; k < th->n_links; ++k) {
        const tw_thermal_link* l = &th->link[k];
        if (l->a < 0 || l->a >= th->n || l->b < 0 || l->b >= th->n || l->a == l->b) return -1;
        if (!(l->g >= 0.0) || !isfinite(l->g)) return -1;
    }

    /* ---- reachability of ambient: flood from every node with g_amb > 0 over links with g > 0 */
    int reach[TW_THERMAL_MAX_NODES] = {0};
    for (int i = 0; i < th->n; ++i) reach[i] = th->g_amb[i] > 0.0;
    for (int pass = 0; pass < th->n; ++pass)
        for (int k = 0; k < th->n_links; ++k) {
            const tw_thermal_link* l = &th->link[k];
            if (l->g > 0.0 && (reach[l->a] || reach[l->b])) reach[l->a] = reach[l->b] = 1;
        }
    for (int i = 0; i < th->n; ++i)
        if (!reach[i]) return -1;
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Conductance matrix: G = Laplacian of the links + diag(g_amb)
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Fill A with G (A is zeroed first). */
static void conductance(const tw_thermal* th, double A[TW_THERMAL_MAX_NODES][TW_THERMAL_MAX_NODES])
{
    memset(A, 0, sizeof(double) * TW_THERMAL_MAX_NODES * TW_THERMAL_MAX_NODES);
    for (int i = 0; i < th->n; ++i) A[i][i] = th->g_amb[i];
    for (int k = 0; k < th->n_links; ++k) {
        const tw_thermal_link* l = &th->link[k];
        A[l->a][l->a] += l->g;
        A[l->b][l->b] += l->g;
        A[l->a][l->b] -= l->g;
        A[l->b][l->a] -= l->g;
    }
}

int tw_thermal_step(tw_thermal* th, double dt)
{
    if (!(dt > 0.0) || !isfinite(dt) || tw_thermal_validate(th) != 0) return -1;
    double A[TW_THERMAL_MAX_NODES][TW_THERMAL_MAX_NODES];
    double b[TW_THERMAL_MAX_NODES];

    /* ---- (C/dt + G) T_new = C/dt T_old + P + g_amb T_amb */
    conductance(th, A);
    for (int i = 0; i < th->n; ++i) {
        A[i][i] += th->c[i] / dt;
        b[i] = th->c[i] / dt * th->t_c[i] + th->p_w[i] + th->g_amb[i] * th->t_amb_c;
    }
    if (solve(th->n, A, b) != 0) return -2;
    for (int i = 0; i < th->n; ++i) th->t_c[i] = b[i];
    return 0;
}

int tw_thermal_steady(const tw_thermal* th, double* out_c)
{
    if (!out_c || tw_thermal_validate(th) != 0) return -1;
    double A[TW_THERMAL_MAX_NODES][TW_THERMAL_MAX_NODES];
    double b[TW_THERMAL_MAX_NODES];
    conductance(th, A);
    for (int i = 0; i < th->n; ++i) b[i] = th->p_w[i] + th->g_amb[i] * th->t_amb_c;
    if (solve(th->n, A, b) != 0) return -2;
    for (int i = 0; i < th->n; ++i) out_c[i] = b[i];
    return 0;
}
