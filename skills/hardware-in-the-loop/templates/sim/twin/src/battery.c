/**
 * @file battery.c
 * @brief 1RC Thevenin battery pack (see battery.h for the model and references).
 */
#include "hil_twin/battery.h"

#include <math.h>
#include <string.h>

int tw_battery_init(tw_battery* b, const tw_battery_params* p, double soc0)
{
    if (!b || !p) return -1;
    if (!(p->capacity_ah > 0.0) || p->n_series < 1 || !(p->r0_ohm >= 0.0) || !(p->r1_ohm > 0.0) || !(p->c1_f > 0.0))
        return -1;
    if (p->n_ocv < 2 || p->n_ocv > TW_BATT_MAX_OCV) return -1;
    /* ---- the table must be finite and strictly increasing in SOC, within 0..1 */
    for (int i = 0; i < p->n_ocv; ++i) {
        if (!isfinite(p->ocv_soc[i]) || !isfinite(p->ocv_v[i]) || p->ocv_soc[i] < 0.0 || p->ocv_soc[i] > 1.0)
            return -1;
        if (i > 0 && !(p->ocv_soc[i] > p->ocv_soc[i - 1])) return -1;
    }
    if (!(soc0 >= 0.0 && soc0 <= 1.0)) return -1;

    memset(b, 0, sizeof(*b));
    b->p = *p;
    b->soc = soc0;
    b->v_term = p->n_series * tw_battery_ocv_cell(p, soc0);
    return 0;
}

double tw_battery_ocv_cell(const tw_battery_params* p, double soc)
{
    if (!p || p->n_ocv < 2) return 0.0;
    if (!(soc > p->ocv_soc[0])) return p->ocv_v[0];
    if (soc >= p->ocv_soc[p->n_ocv - 1]) return p->ocv_v[p->n_ocv - 1];
    /* ---- linear interpolation in the bracketing segment */
    for (int i = 1; i < p->n_ocv; ++i)
        if (soc <= p->ocv_soc[i]) {
            double f = (soc - p->ocv_soc[i - 1]) / (p->ocv_soc[i] - p->ocv_soc[i - 1]);
            return p->ocv_v[i - 1] + f * (p->ocv_v[i] - p->ocv_v[i - 1]);
        }
    return p->ocv_v[p->n_ocv - 1];
}

int tw_battery_step(tw_battery* b, double i_a, double dt)
{
    if (!b || !isfinite(i_a) || !(dt > 0.0) || !isfinite(dt)) return -1;
    const tw_battery_params* p = &b->p;
    int rc = 0;

    /* ---- charge bookkeeping (coulomb counting) */
    b->soc -= i_a * dt / (3600.0 * p->capacity_ah);
    if (b->soc < 0.0) {
        b->soc = 0.0;
        rc = 1;
    }
    if (b->soc > 1.0) b->soc = 1.0;

    /* ---- RC branch: exact solution with I held over the step */
    const double tau = p->r1_ohm * p->c1_f;
    const double e = exp(-dt / tau);
    b->v1 = b->v1 * e + p->r1_ohm * i_a * (1.0 - e);

    /* ---- terminal voltage and heat */
    b->v_term = p->n_series * tw_battery_ocv_cell(p, b->soc) - i_a * p->r0_ohm - b->v1;
    if (b->v_term < 0.0) b->v_term = 0.0;
    b->heat_w = i_a * i_a * p->r0_ohm + b->v1 * b->v1 / p->r1_ohm;
    return rc;
}
