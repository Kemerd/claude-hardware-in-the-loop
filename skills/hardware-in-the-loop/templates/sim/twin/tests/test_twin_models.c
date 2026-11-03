/**
 * @file test_twin_models.c
 * @brief Host suite of the twin's model library: every model against a closed-form answer.
 *
 * Each block states the analytic result it checks and the tolerance, so a failure says which law broke. Run by
 * windows/ci/twin.ps1 (suite twin_tests); prints exactly one "[hil] twin_models: N/N checks passed, 0 failed" line.
 * argv[1] (optional): a writable directory for the include test files (default: the current directory).
 */
#define HIL_CHECK_IMPLEMENTATION
#include "hil_check.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hil_twin/actuators.h"
#include "hil_twin/battery.h"
#include "hil_twin/params.h"
#include "hil_twin/rigid_body.h"
#include "hil_twin/rng.h"
#include "hil_twin/sensors.h"
#include "hil_twin/thermal.h"
#include "hil_twin/trace.h"

#define PI 3.14159265358979323846

/* ------------------------------------------------------------------------------------------------------------------
 *  RNG: reproducible, independent streams, right moments
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_rng(void)
{
    tw_rng a, b, c;
    tw_rng_seed(&a, 42, 1);
    tw_rng_seed(&b, 42, 1);
    tw_rng_seed(&c, 42, 2);
    int same = 1, diff = 0;
    for (int i = 0; i < 1000; ++i) {
        uint64_t x = tw_rng_next(&a), y = tw_rng_next(&b), z = tw_rng_next(&c);
        same &= (x == y);
        diff += (x != z);
    }
    HIL_CHECK(same);
    HIL_CHECK(diff > 990);

    /* ---- N(0,1): mean within 4 sigma of the sampling error, variance within 1 % at n = 200000 */
    const int n = 200000;
    double s = 0, s2 = 0;
    tw_rng_seed(&a, 7, 3);
    for (int i = 0; i < n; ++i) {
        double v = tw_rng_normal(&a);
        s += v;
        s2 += v * v;
    }
    double mean = s / n, var = s2 / n - mean * mean;
    HIL_CHECK_NEAR(mean, 0.0, 4.0 / sqrt((double)n));
    HIL_CHECK_NEAR(var, 1.0, 0.01);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Rigid body
 * ------------------------------------------------------------------------------------------------------------------ */
static tw_body_params box_params(void)
{
    tw_body_params p;
    memset(&p, 0, sizeof p);
    p.mass = 2.0;
    p.inertia.m[0][0] = 0.02;
    p.inertia.m[1][1] = 0.03;
    p.inertia.m[2][2] = 0.04;
    p.gravity = 9.80665;
    p.contact_k = 20000.0;
    p.contact_c = 200.0;
    p.contact_mu = 0.6;
    p.contact_v_slip = 0.01;
    return p;
}

static void test_rigid_body(void)
{
    /* ---- free fall: z = z0 - g t^2 / 2 (RK4 is exact for a constant acceleration up to rounding) */
    tw_body b;
    tw_body_params p = box_params();
    HIL_CHECK_EQ_INT(tw_body_init(&b, &p), 0);
    b.ground_enabled = 0;
    b.s.pos = tw_v3(0, 0, 10);
    for (int i = 0; i < 1000; ++i) tw_body_step(&b, tw_v3(0, 0, 0), tw_v3(0, 0, 0), 0.001);
    HIL_CHECK_NEAR(b.s.pos.z, 10.0 - 0.5 * 9.80665, 1e-9);
    HIL_CHECK_NEAR(b.s.vel.z, -9.80665, 1e-9);

    /* ---- torque-free spin about a non-principal direction: |L| and energy are invariants */
    p.gravity = 0.0;
    HIL_CHECK_EQ_INT(tw_body_init(&b, &p), 0);
    b.ground_enabled = 0;
    b.s.w = tw_v3(1.0, 5.0, 0.5);
    double e0 = tw_body_kinetic_energy(&b);
    tw_vec3 L0 = tw_body_angular_momentum_world(&b);
    for (int i = 0; i < 10000; ++i) tw_body_step(&b, tw_v3(0, 0, 0), tw_v3(0, 0, 0), 0.001);
    tw_vec3 L1 = tw_body_angular_momentum_world(&b);
    HIL_CHECK_NEAR(tw_body_kinetic_energy(&b) / e0, 1.0, 1e-6);
    HIL_CHECK_NEAR(tw_norm(tw_sub(L1, L0)) / tw_norm(L0), 0.0, 1e-6);
    double qn = sqrt(b.s.q.w * b.s.q.w + b.s.q.x * b.s.q.x + b.s.q.y * b.s.q.y + b.s.q.z * b.s.q.z);
    HIL_CHECK_NEAR(qn, 1.0, 1e-12);

    /* ---- resting on four contact points: total normal force = m g, settles level */
    p = box_params();
    p.n_contacts = 4;
    p.contact_body[0] = tw_v3(0.1, 0.1, -0.05);
    p.contact_body[1] = tw_v3(0.1, -0.1, -0.05);
    p.contact_body[2] = tw_v3(-0.1, 0.1, -0.05);
    p.contact_body[3] = tw_v3(-0.1, -0.1, -0.05);
    HIL_CHECK_EQ_INT(tw_body_init(&b, &p), 0);
    b.s.pos = tw_v3(0, 0, 0.08);
    for (int i = 0; i < 3000; ++i) tw_body_step(&b, tw_v3(0, 0, 0), tw_v3(0, 0, 0), 0.001);
    HIL_CHECK_EQ_INT(b.n_touching, 4);
    HIL_CHECK_NEAR(b.contact_force_n, p.mass * p.gravity, 0.01 * p.mass * p.gravity);
    HIL_CHECK_NEAR(b.s.pos.z, 0.05 - p.mass * p.gravity / (4.0 * p.contact_k), 1e-4);
    HIL_CHECK(tw_q_tilt(b.s.q) < 1e-6);

    /* ---- friction holds a small push, lets a big one slide (mu m g = 11.8 N) */
    tw_body_state rest = b.s;
    for (int i = 0; i < 500; ++i) tw_body_step(&b, tw_v3(3.0, 0, 0), tw_v3(0, 0, 0), 0.001);
    HIL_CHECK(fabs(b.s.vel.x) < 0.02);
    b.s = rest;
    for (int i = 0; i < 500; ++i) tw_body_step(&b, tw_v3(30.0, 0, 0), tw_v3(0, 0, 0), 0.001);
    HIL_CHECK(b.s.vel.x > 1.0);

    /* ---- bad input is refused, not integrated */
    HIL_CHECK_EQ_INT(tw_body_step(&b, tw_v3(NAN, 0, 0), tw_v3(0, 0, 0), 0.001), -1);
    tw_body_params bad = box_params();
    bad.inertia.m[0][1] = 0.01; /* asymmetric */
    HIL_CHECK_EQ_INT(tw_body_init(&b, &bad), -1);
    bad = box_params();
    bad.mass = 0.0;
    HIL_CHECK_EQ_INT(tw_body_init(&b, &bad), -1);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Thermal network
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_thermal(void)
{
    /* ---- one node: T(t) = T_amb + P / G (1 - exp(-t G / C)); check at t = tau */
    tw_thermal th;
    memset(&th, 0, sizeof th);
    th.n = 1;
    th.c[0] = 50.0;
    th.g_amb[0] = 0.5;
    th.t_amb_c = 25.0;
    th.t_c[0] = 25.0;
    th.p_w[0] = 10.0;
    const double tau = th.c[0] / th.g_amb[0];
    const double dt = tau / 10000.0;
    for (int i = 0; i < 10000; ++i) tw_thermal_step(&th, dt);
    HIL_CHECK_NEAR(th.t_c[0], 25.0 + 20.0 * (1.0 - exp(-1.0)), 0.01);

    /* ---- two nodes in series (junction -> case -> ambient): steady = T_amb + P (1/G_jc + 1/G_ca) */
    memset(&th, 0, sizeof th);
    th.n = 2;
    th.c[0] = 0.5;
    th.c[1] = 30.0;
    th.g_amb[1] = 0.25;
    th.n_links = 1;
    th.link[0].a = 0;
    th.link[0].b = 1;
    th.link[0].g = 2.0;
    th.t_amb_c = 40.0;
    th.p_w[0] = 3.0;
    th.t_c[0] = th.t_c[1] = 40.0;
    double ss[2];
    HIL_CHECK_EQ_INT(tw_thermal_steady(&th, ss), 0);
    HIL_CHECK_NEAR(ss[0], 40.0 + 3.0 * (1.0 / 2.0 + 1.0 / 0.25), 1e-9);
    HIL_CHECK_NEAR(ss[1], 40.0 + 3.0 / 0.25, 1e-9);

    /* ---- backward Euler with a step far above the junction time constant stays stable and converges */
    for (int i = 0; i < 20000; ++i) tw_thermal_step(&th, 1.0);
    HIL_CHECK_NEAR(th.t_c[0], ss[0], 1e-6);

    /* ---- a node with no path to ambient has no steady state: refused */
    th.g_amb[1] = 0.0;
    HIL_CHECK_EQ_INT(tw_thermal_validate(&th), -1);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Battery
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_battery(void)
{
    tw_battery_params p;
    memset(&p, 0, sizeof p);
    p.capacity_ah = 2.0;
    p.n_series = 3;
    p.r0_ohm = 0.05;
    p.r1_ohm = 0.03;
    p.c1_f = 1000.0;
    p.n_ocv = 3;
    p.ocv_soc[0] = 0.0; p.ocv_v[0] = 3.3;
    p.ocv_soc[1] = 0.5; p.ocv_v[1] = 3.7;
    p.ocv_soc[2] = 1.0; p.ocv_v[2] = 4.2;
    tw_battery b;
    HIL_CHECK_EQ_INT(tw_battery_init(&b, &p, 1.0), 0);
    HIL_CHECK_NEAR(tw_battery_ocv_cell(&p, 0.25), 3.5, 1e-12);

    /* ---- first step at 10 A: the ohmic drop is immediate, the RC voltage starts from zero */
    tw_battery_step(&b, 10.0, 0.001);
    HIL_CHECK_NEAR(b.v_term, 3 * tw_battery_ocv_cell(&p, b.soc) - 0.5 - b.v1, 1e-12);
    HIL_CHECK(b.v1 < 0.01);

    /* ---- after five RC time constants the polarisation is I R1 within 1 % */
    const double tau = p.r1_ohm * p.c1_f;
    for (int i = 0; i < (int)(5 * tau / 0.001); ++i) tw_battery_step(&b, 10.0, 0.001);
    HIL_CHECK_NEAR(b.v1, 0.3, 0.003);

    /* ---- coulomb counting: 1C for an hour empties it (and the empty flag comes up) */
    HIL_CHECK_EQ_INT(tw_battery_init(&b, &p, 1.0), 0);
    int rc = 0;
    for (int i = 0; i < 3601; ++i) rc = tw_battery_step(&b, 2.0, 1.0);
    HIL_CHECK_EQ_INT(rc, 1);
    HIL_CHECK_NEAR(b.soc, 0.0, 1e-12);

    /* ---- a table that is not increasing is refused */
    p.ocv_soc[1] = 1.0;
    HIL_CHECK_EQ_INT(tw_battery_init(&b, &p, 0.5), -1);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Brushed DC motor, gearbox and joint
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_dc_motor_and_joint(void)
{
    tw_motor_params mp = {.r20_ohm = 2.0, .l_h = 1e-3, .kt = 0.02, .j_rotor = 1e-6, .i_max_a = 100.0,
                          .alpha_cu = 0.00393};
    tw_motor m;
    HIL_CHECK_EQ_INT(tw_motor_init(&m, &mp), 0);

    /* ---- stall at 20 degC: i = V / R after a few L/R */
    for (int i = 0; i < 100; ++i) tw_motor_step(&m, 1.0, 12.0, 20.0, 0.0, 0.001);
    HIL_CHECK_NEAR(m.i_a, 6.0, 1e-6);
    HIL_CHECK_NEAR(m.torque_nm, 0.12, 1e-8);
    HIL_CHECK_NEAR(m.p_copper_w, 72.0, 1e-5);

    /* ---- hot winding: R(100 degC) = R20 (1 + 0.00393 * 80) */
    for (int i = 0; i < 100; ++i) tw_motor_step(&m, 1.0, 12.0, 100.0, 0.0, 0.001);
    HIL_CHECK_NEAR(m.i_a, 6.0 / (1.0 + 0.00393 * 80.0), 1e-6);

    /* ---- reverse duty, NaN duty reported */
    for (int i = 0; i < 100; ++i) tw_motor_step(&m, -0.5, 12.0, 20.0, 0.0, 0.001);
    HIL_CHECK_NEAR(m.i_a, -3.0, 1e-6);
    HIL_CHECK_EQ_INT(tw_motor_step(&m, NAN, 12.0, 20.0, 0.0, 0.001), 1);

    /* ---- no-load speed through a 10:1 gearbox, no gravity, no friction: w_m -> V / kt */
    tw_joint_params jp = {.ratio = 10.0, .efficiency = 1.0, .j_link = 1e-4, .m_link = 0.0, .l_com = 0.0,
                          .gravity = 0.0, .b_out = 0.0, .tau_coulomb = 0.0, .qd_slip = 0.01,
                          .q_min = -1e9, .q_max = 1e9, .k_stop = 0.0, .c_stop = 0.0};
    tw_joint j;
    HIL_CHECK_EQ_INT(tw_joint_init(&j, &jp, 0.0), 0);
    HIL_CHECK_EQ_INT(tw_motor_init(&m, &mp), 0);
    for (int i = 0; i < 20000; ++i) {
        tw_motor_step(&m, 1.0, 12.0, 20.0, jp.ratio * j.qd, 0.0005);
        tw_joint_step(&j, m.torque_nm, 0.0, mp.j_rotor, 0.0005);
    }
    HIL_CHECK_NEAR(jp.ratio * j.qd, 12.0 / 0.02, 0.5);

    /* ---- pendulum: small swings about hanging (q = -pi/2), point mass, period 2 pi sqrt(J / (m g l)) */
    jp.gravity = 9.80665;
    jp.m_link = 0.5;
    jp.l_com = 0.2;
    jp.j_link = 0.5 * 0.2 * 0.2;
    jp.ratio = 1.0;
    HIL_CHECK_EQ_INT(tw_joint_init(&j, &jp, -PI / 2 + 0.02), 0);
    double t = 0.0, prev = j.q + PI / 2, first = -1.0, second = -1.0;
    const double dt = 1e-4;
    for (int i = 0; i < 40000 && second < 0.0; ++i) {
        tw_joint_step(&j, 0.0, 0.0, 0.0, dt);
        t += dt;
        double x = j.q + PI / 2;
        if (prev > 0.0 && x <= 0.0) {
            if (first < 0.0) first = t;
            else second = t;
        }
        prev = x;
    }
    const double period = 2.0 * PI * sqrt(jp.j_link / (jp.m_link * jp.gravity * jp.l_com));
    HIL_CHECK(second > 0.0);
    HIL_CHECK_NEAR(second - first, period, 0.002 * period);

    /* ---- hard stop: a steady push into q_max settles at q_max + tau / k */
    jp.gravity = 0.0;
    jp.q_min = -1.0;
    jp.q_max = 1.0;
    jp.k_stop = 500.0;
    jp.c_stop = 5.0;
    HIL_CHECK_EQ_INT(tw_joint_init(&j, &jp, 0.9), 0);
    for (int i = 0; i < 20000; ++i) tw_joint_step(&j, 0.0, 2.0, 0.0, 0.001);
    HIL_CHECK_NEAR(j.q, 1.0 + 2.0 / 500.0, 1e-4);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Brushless PMSM with the FOC drive
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_pmsm(void)
{
    tw_pmsm_params pp = {.r_ohm = 0.2, .ld_h = 1e-4, .lq_h = 1e-4, .psi_m = 0.005, .pole_pairs = 7,
                         .j_rotor = 2e-5, .alpha_cu = 0.00393, .i_max_a = 20.0, .kp_v_per_a = 0.5,
                         .ki_v_per_as = 1000.0, .n_sub = 20};
    tw_pmsm m;
    HIL_CHECK_EQ_INT(tw_pmsm_init(&m, &pp), 0);

    /* ---- at standstill the drive tracks iq and the torque is 1.5 p psi_m iq */
    for (int i = 0; i < 200; ++i) tw_pmsm_step(&m, 5.0, 0.0, 24.0, 20.0, 0.0, 0.001);
    HIL_CHECK_NEAR(m.iq, 5.0, 0.01);
    HIL_CHECK_NEAR(m.id, 0.0, 0.01);
    HIL_CHECK_NEAR(m.torque_nm, 1.5 * 7 * 0.005 * 5.0, 1e-3);
    HIL_CHECK_NEAR(m.p_copper_w, 1.5 * 0.2 * 25.0, 0.05);
    HIL_CHECK_EQ_INT(m.saturated, 0);

    /* ---- far above base speed the back-EMF eats the voltage: the drive saturates and iq falls short */
    const double w_high = 1.2 * (24.0 / sqrt(3.0)) / (7 * 0.005);
    HIL_CHECK_EQ_INT(tw_pmsm_init(&m, &pp), 0);
    for (int i = 0; i < 200; ++i) tw_pmsm_step(&m, 5.0, 0.0, 24.0, 20.0, w_high, 0.001);
    HIL_CHECK_EQ_INT(m.saturated, 1);
    HIL_CHECK(m.iq < 5.0);

    /* ---- the current limit holds */
    HIL_CHECK_EQ_INT(tw_pmsm_init(&m, &pp), 0);
    for (int i = 0; i < 200; ++i) tw_pmsm_step(&m, 100.0, 0.0, 24.0, 20.0, 0.0, 0.001);
    HIL_CHECK(m.iq <= 20.0 + 1e-6);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Sensors
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_sensors(void)
{
    /* ---- gyro at rest: discrete sigma = N_d sqrt(f_s) (no bias, no scale error, fine LSB) */
    tw_imu_params ip;
    memset(&ip, 0, sizeof ip);
    ip.gyro.noise_density = 0.01;
    ip.gyro.range = 30.0;
    ip.gyro.lsb = 30.0 / 32768.0;
    ip.accel.noise_density = 0.0;
    ip.accel.range = 150.0;
    ip.accel.lsb = 150.0 / 32768.0;
    tw_imu imu;
    HIL_CHECK_EQ_INT(tw_imu_init(&imu, &ip, 1), 0);
    const int n = 100000;
    const double dt = 0.001;
    double s = 0, s2 = 0, az = 0;
    tw_imu_sample smp;
    for (int i = 0; i < n; ++i) {
        tw_imu_sample_step(&imu, tw_q_identity(), tw_v3(0, 0, 0), tw_v3(0, 0, 0), 9.80665, dt, &smp);
        double g = smp.gyro_counts[0] * ip.gyro.lsb;
        s += g;
        s2 += g * g;
        az += smp.accel_counts[2] * ip.accel.lsb;
    }
    double sd = sqrt(s2 / n - (s / n) * (s / n));
    HIL_CHECK_NEAR(sd, 0.01 * sqrt(1.0 / dt), 0.03 * 0.01 * sqrt(1.0 / dt));
    /* ---- level at rest the accelerometer reads +1 g on z (specific force), within one LSB */
    HIL_CHECK_NEAR(az / n, 9.80665, ip.accel.lsb);

    /* ---- ISA: 1000 m above sea level is 89874.6 Pa [REF-012] */
    HIL_CHECK_NEAR(tw_isa_pressure(101325.0, 288.15, 1000.0), 89874.6, 2.0);

    /* ---- rangefinder pointing down from 1 m returns 1 m; tilted past its incidence limit returns nothing */
    tw_range_params rp = {.pos_body = {0, 0, 0}, .dir_body = {0, 0, -1}, .min_m = 0.02, .max_m = 4.0,
                          .max_incidence_rad = 0.5, .sigma0_m = 0.0, .sigma_k = 0.0, .lsb_m = 0.001};
    tw_range rf;
    HIL_CHECK_EQ_INT(tw_range_init(&rf, &rp, 1), 0);
    uint16_t c = 0;
    HIL_CHECK_EQ_INT(tw_range_sample(&rf, tw_v3(0, 0, 1.0), tw_q_identity(), &c), 1);
    HIL_CHECK_EQ_INT(c, 1000);
    HIL_CHECK_EQ_INT(tw_range_sample(&rf, tw_v3(0, 0, 1.0), tw_q_axis_angle(tw_v3(1, 0, 0), 0.8), &c), 0);

    /* ---- NTC divider at 25 degC: V = vref R25 / (R25 + R_top); 12-bit code without noise */
    tw_ntc_params ntc = {.r25_ohm = 10000.0, .beta_k = 3950.0, .r_top_ohm = 10000.0};
    HIL_CHECK_NEAR(tw_ntc_divider_v(&ntc, 3.3, 25.0), 1.65, 1e-9);
    tw_adc_params ap = {.bits = 12, .vref_v = 3.3, .noise_lsb = 0.0};
    tw_adc adc;
    HIL_CHECK_EQ_INT(tw_adc_init(&adc, &ap, 1, 0), 0);
    HIL_CHECK_EQ_INT((int)tw_adc_sample(&adc, 1.65), 2048);
    HIL_CHECK_EQ_INT((int)tw_adc_sample(&adc, 5.0), 4095);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Params and trace
 * ------------------------------------------------------------------------------------------------------------------ */
static void test_params_and_trace(const char* dir)
{
    char err[512];
    tw_params* p = tw_params_parse("a = 1.5\nv = 1 2 3 # comment\n\nname = rig\nspare = 4\n", "mem", err, sizeof err);
    HIL_CHECK(p != NULL);
    double a = 0, v[3] = {0};
    HIL_CHECK_EQ_INT(tw_params_num(p, "a", &a), 0);
    HIL_CHECK_NEAR(a, 1.5, 0.0);
    HIL_CHECK_EQ_INT(tw_params_vec(p, "v", v, 3), 0);
    HIL_CHECK_NEAR(v[2], 3.0, 0.0);
    HIL_CHECK_EQ_INT(tw_params_vec(p, "v", v, 2), -2);   /* wrong count is an error, not a truncation */
    HIL_CHECK_EQ_INT(tw_params_num(p, "missing", &a), -1);
    HIL_CHECK(tw_params_str(p, "name") && strcmp(tw_params_str(p, "name"), "rig") == 0);
    char unused[256];
    HIL_CHECK_EQ_INT(tw_params_report_unused(p, unused, sizeof unused), 1);
    HIL_CHECK(strstr(unused, "spare") != NULL);
    tw_params_free(p);

    /* ---- duplicates and junk are refused with a message */
    HIL_CHECK(tw_params_parse("a = 1\na = 2\n", "mem", err, sizeof err) == NULL);
    HIL_CHECK(strstr(err, "twice") != NULL);
    p = tw_params_parse("a = 12abc\n", "mem", err, sizeof err);
    HIL_CHECK(p != NULL);
    HIL_CHECK_EQ_INT(tw_params_num(p, "a", &a), -2);
    tw_params_free(p);

    /* ---- includes resolve relative to the including file */
    char top[512], inc[512];
    snprintf(top, sizeof top, "%s/tw_test_top.txt", dir);
    snprintf(inc, sizeof inc, "%s/tw_test_inc.txt", dir);
    FILE* f = fopen(inc, "wb");
    HIL_CHECK(f != NULL);
    if (f) { fputs("mass_kg = 0.75\n", f); fclose(f); }
    f = fopen(top, "wb");
    HIL_CHECK(f != NULL);
    if (f) { fputs("include = tw_test_inc.txt\nx = 2\n", f); fclose(f); }
    p = tw_params_load(top, err, sizeof err);
    HIL_CHECK(p != NULL);
    if (p) {
        HIL_CHECK_EQ_INT(tw_params_num(p, "mass_kg", &a), 0);
        HIL_CHECK_NEAR(a, 0.75, 0.0);
        tw_params_free(p);
    }
    remove(top);
    remove(inc);

    /* ---- trace hash: same rows, same hash; one changed digit, another hash */
    tw_trace t1, t2;
    tw_trace_open(&t1, NULL, 1);
    tw_trace_open(&t2, NULL, 1);
    double r1[2] = {1.0, 2.0}, r2[2] = {1.0, 2.0000001};
    tw_trace_row(&t1, r1, 2);
    tw_trace_row(&t2, r1, 2);
    HIL_CHECK(t1.hash == t2.hash);
    tw_trace_row(&t1, r1, 2);
    tw_trace_row(&t2, r2, 2);
    HIL_CHECK(t1.hash != t2.hash);
}

int main(int argc, char** argv)
{
    const char* dir = argc > 1 ? argv[1] : ".";
    test_rng();
    test_rigid_body();
    test_thermal();
    test_battery();
    test_dc_motor_and_joint();
    test_pmsm();
    test_sensors();
    test_params_and_trace(dir);
    return hil_check_summary("twin_models");
}
