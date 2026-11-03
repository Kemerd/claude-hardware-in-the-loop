/**
 * @file sensors.c
 * @brief IMU, barometer, rangefinder and ADC models (see sensors.h for the models and references).
 */
#include "hil_twin/sensors.h"

#include <string.h>

/* ------------------------------------------------------------------------------------------------------------------
 *  Helpers
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Quantise to counts and clip to int16 and to the full-scale range. */
static int16_t to_counts(double x, double range, double lsb)
{
    if (!isfinite(x)) x = 0.0;
    if (x > range) x = range;
    if (x < -range) x = -range;
    double c = floor(x / lsb + 0.5);
    if (c > 32767.0) c = 32767.0;
    if (c < -32768.0) c = -32768.0;
    return (int16_t)c;
}

/** @brief Channel parameter check. */
static int channel_ok(const tw_imu_channel_params* c)
{
    return c && c->noise_density >= 0.0 && c->bias_rw >= 0.0 && c->bias0_sigma >= 0.0 && c->scale_sigma >= 0.0 &&
           c->range > 0.0 && c->lsb > 0.0 && isfinite(c->range) && isfinite(c->lsb) && c->range / c->lsb <= 32768.0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  IMU
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_imu_init(tw_imu* m, const tw_imu_params* p, uint64_t seed)
{
    if (!m || !p || !channel_ok(&p->gyro) || !channel_ok(&p->accel) || !tw_finite3(p->pos_body)) return -1;
    memset(m, 0, sizeof(*m));
    m->p = *p;
    tw_rng_seed(&m->rng_g, seed, tw_stream_gyro);
    tw_rng_seed(&m->rng_a, seed, tw_stream_accel);

    /* ---- per-unit draws: one physical part = one seed; the same seed is the same part every run */
    tw_rng unit;
    tw_rng_seed(&unit, seed, tw_stream_unit);
    m->bias_g = tw_v3(p->gyro.bias0_sigma * tw_rng_normal(&unit), p->gyro.bias0_sigma * tw_rng_normal(&unit),
                      p->gyro.bias0_sigma * tw_rng_normal(&unit));
    m->bias_a = tw_v3(p->accel.bias0_sigma * tw_rng_normal(&unit), p->accel.bias0_sigma * tw_rng_normal(&unit),
                      p->accel.bias0_sigma * tw_rng_normal(&unit));
    m->scale_g = tw_v3(p->gyro.scale_sigma * tw_rng_normal(&unit), p->gyro.scale_sigma * tw_rng_normal(&unit),
                       p->gyro.scale_sigma * tw_rng_normal(&unit));
    m->scale_a = tw_v3(p->accel.scale_sigma * tw_rng_normal(&unit), p->accel.scale_sigma * tw_rng_normal(&unit),
                       p->accel.scale_sigma * tw_rng_normal(&unit));
    return 0;
}

/**
 * @brief One channel: scale, bias walk, white noise, quantise.
 * @param truth  true value per axis
 * @param bias   bias state (advanced)
 * @param scale  per-unit scale error
 * @param c      channel parameters
 * @param r      stream
 * @param dt     sample period
 * @param out    counts
 */
static void channel(tw_vec3 truth, tw_vec3* bias, tw_vec3 scale, const tw_imu_channel_params* c, tw_rng* r,
                    double dt, int16_t out[3])
{
    const double sd_white = c->noise_density * sqrt(1.0 / dt);
    const double sd_walk = c->bias_rw * sqrt(dt);
    double t[3] = {truth.x, truth.y, truth.z};
    double* b[3] = {&bias->x, &bias->y, &bias->z};
    double s[3] = {scale.x, scale.y, scale.z};
    for (int i = 0; i < 3; ++i) {
        /* ---- draw order is fixed (walk, then white) so the stream position never depends on the values */
        *b[i] += sd_walk * tw_rng_normal(r);
        double y = (1.0 + s[i]) * t[i] + *b[i] + sd_white * tw_rng_normal(r);
        out[i] = to_counts(y, c->range, c->lsb);
    }
}

int tw_imu_sample_step(tw_imu* m, tw_quat q, tw_vec3 w_body, tw_vec3 a_com_world, double gravity, double dt,
                       tw_imu_sample* out)
{
    if (!m || !out || !(dt > 0.0) || !tw_finite3(w_body) || !tw_finite3(a_com_world) || !isfinite(gravity)) return -1;

    /* ---- gyro: the body rate */
    channel(w_body, &m->bias_g, m->scale_g, &m->p.gyro, &m->rng_g, dt, out->gyro_counts);

    /* ---- accelerometer: specific force at the sensor = a_sensor - g, in body axes.
     *      a_sensor = a_com + alpha x r + w x (w x r) (rigid body kinematics, body frame) */
    tw_vec3 alpha = m->have_prev ? tw_scale(tw_sub(w_body, m->w_prev), 1.0 / dt) : tw_v3(0, 0, 0);
    m->w_prev = w_body;
    m->have_prev = 1;
    const tw_vec3 r = m->p.pos_body;
    tw_vec3 a_body = tw_q_rotate_inv(q, a_com_world);
    a_body = tw_add(a_body, tw_cross(alpha, r));
    a_body = tw_add(a_body, tw_cross(w_body, tw_cross(w_body, r)));
    tw_vec3 g_body = tw_q_rotate_inv(q, tw_v3(0, 0, -gravity));
    tw_vec3 f_body = tw_sub(a_body, g_body);
    channel(f_body, &m->bias_a, m->scale_a, &m->p.accel, &m->rng_a, dt, out->accel_counts);
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Barometer (ISA troposphere)
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_baro_init(tw_baro* m, const tw_baro_params* p, uint64_t seed)
{
    if (!m || !p || !(p->p0_pa > 0.0) || !(p->t0_k > 0.0) || !(p->noise_pa >= 0.0) || !(p->lsb_pa > 0.0)) return -1;
    m->p = *p;
    tw_rng_seed(&m->rng, seed, tw_stream_baro);
    return 0;
}

double tw_isa_pressure(double p0_pa, double t0_k, double h_m)
{
    /* ---- ISA constants [REF-012]: lapse rate, gravity, molar mass of dry air, gas constant */
    const double L = 0.0065, g = 9.80665, M = 0.0289644, R = 8.3144598;
    double ratio = 1.0 - L * h_m / t0_k;
    if (ratio < 0.01) ratio = 0.01;
    return p0_pa * pow(ratio, g * M / (R * L));
}

int32_t tw_baro_sample(tw_baro* m, double h_m)
{
    if (!m || !isfinite(h_m)) return 0;
    double pa = tw_isa_pressure(m->p.p0_pa, m->p.t0_k, h_m) + m->p.noise_pa * tw_rng_normal(&m->rng);
    return (int32_t)floor(pa / m->p.lsb_pa + 0.5);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Rangefinder
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_range_init(tw_range* m, const tw_range_params* p, uint64_t seed)
{
    if (!m || !p || !tw_finite3(p->pos_body) || !(tw_norm(p->dir_body) > 1e-9)) return -1;
    if (!(p->min_m >= 0.0) || !(p->max_m > p->min_m) || !(p->lsb_m > 0.0) || p->max_m / p->lsb_m > 65535.0) return -1;
    if (!(p->sigma0_m >= 0.0) || !(p->sigma_k >= 0.0) || !(p->max_incidence_rad > 0.0)) return -1;
    m->p = *p;
    m->p.dir_body = tw_scale(p->dir_body, 1.0 / tw_norm(p->dir_body));
    tw_rng_seed(&m->rng, seed, tw_stream_range);
    return 0;
}

int tw_range_sample(tw_range* m, tw_vec3 pos_world, tw_quat q, uint16_t* out_counts)
{
    if (!m || !out_counts || !tw_finite3(pos_world)) return -1;
    *out_counts = 0;
    /* ---- the noise draw happens on every call so the stream position never depends on the geometry */
    const double n = tw_rng_normal(&m->rng);

    /* ---- beam origin and direction in the world */
    tw_vec3 o = tw_add(pos_world, tw_q_rotate(q, m->p.pos_body));
    tw_vec3 d = tw_q_rotate(q, m->p.dir_body);
    if (!(d.z < -1e-9) || o.z <= 0.0) return 0;
    if (acos(-d.z) > m->p.max_incidence_rad) return 0;

    /* ---- distance along the beam to z = 0, then noise and range window */
    double r = -o.z / d.z;
    r += (m->p.sigma0_m + m->p.sigma_k * r) * n;
    if (r < m->p.min_m || r > m->p.max_m) return 0;
    double c = floor(r / m->p.lsb_m + 0.5);
    if (c < 1.0) c = 1.0;
    if (c > 65535.0) c = 65535.0;
    *out_counts = (uint16_t)c;
    return 1;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  ADC and NTC
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_adc_init(tw_adc* a, const tw_adc_params* p, uint64_t seed, uint64_t stream)
{
    if (!a || !p || p->bits < 8 || p->bits > 24 || !(p->vref_v > 0.0) || !(p->noise_lsb >= 0.0)) return -1;
    a->p = *p;
    tw_rng_seed(&a->rng, seed, tw_stream_adc * 1000u + stream);
    return 0;
}

uint32_t tw_adc_sample(tw_adc* a, double v_pin)
{
    if (!a) return 0;
    const double full = (double)((1u << a->p.bits) - 1u);
    double noise = a->p.noise_lsb * tw_rng_normal(&a->rng);
    if (!isfinite(v_pin)) v_pin = 0.0;
    double code = floor(v_pin / a->p.vref_v * full + noise + 0.5);
    if (code < 0.0) code = 0.0;
    if (code > full) code = full;
    return (uint32_t)code;
}

double tw_ntc_divider_v(const tw_ntc_params* n, double vref_v, double t_c)
{
    if (!n || !(n->r25_ohm > 0.0) || !(n->beta_k > 0.0) || !(n->r_top_ohm > 0.0) || !isfinite(t_c)) return 0.0;
    /* ---- beta model about 25 degC [REF-013] */
    const double t_k = t_c + 273.15, t25 = 298.15;
    const double r = n->r25_ohm * exp(n->beta_k * (1.0 / t_k - 1.0 / t25));
    return vref_v * r / (r + n->r_top_ohm);
}
