/**
 * @file plant.c
 * @brief The example actuator rig: params -> models, the hosted HAL, the step in native and external mode.
 */
#include "plant.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#define PI 3.14159265358979323846

/* ------------------------------------------------------------------------------------------------------------------
 *  Parameter reading: the first problem is reported with the key, then everything stops
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Reader state: the set, the error buffer and whether a problem has been seen. */
typedef struct {
    tw_params* p;
    char* err;
    size_t errlen;
    int bad;
} reader;

/** @brief Required number. */
static double req(reader* r, const char* key)
{
    double v = 0.0;
    int rc = tw_params_num(r->p, key, &v);
    if (rc != 0 && !r->bad) {
        r->bad = 1;
        if (r->err && r->errlen)
            snprintf(r->err, r->errlen, "params: '%s' %s", key, rc == -1 ? "is missing" : "is not one finite number");
    }
    return v;
}

/** @brief Optional number with a default (a malformed value is still an error). */
static double opt(reader* r, const char* key, double def)
{
    double v = def;
    if (tw_params_num_or(r->p, key, def, &v) != 0 && !r->bad) {
        r->bad = 1;
        if (r->err && r->errlen) snprintf(r->err, r->errlen, "params: '%s' is not one finite number", key);
    }
    return v;
}

/** @brief Fail with a message (first one wins). */
static void fail(reader* r, const char* msg, const char* what)
{
    if (r->bad) return;
    r->bad = 1;
    if (r->err && r->errlen) snprintf(r->err, r->errlen, "params: %s%s", msg, what ? what : "");
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Byte queues
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_byteq_push(tw_byteq* q, const uint8_t* d, int n)
{
    if (!q || !d || n < 0) return -1;
    int pushed = 0;
    for (int i = 0; i < n; ++i) {
        int next = (q->head + 1) % (int)sizeof q->buf;
        if (next == q->tail) {
            q->dropped += (uint32_t)(n - i);
            break;
        }
        q->buf[q->head] = d[i];
        q->head = next;
        ++pushed;
    }
    return pushed;
}

int tw_byteq_pop(tw_byteq* q, uint8_t* out, int cap)
{
    if (!q || !out || cap <= 0) return 0;
    int n = 0;
    while (n < cap && q->tail != q->head) {
        out[n++] = q->buf[q->tail];
        q->tail = (q->tail + 1) % (int)sizeof q->buf;
    }
    return n;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  The hosted HAL: raw values from the true plant state, exactly what the pins and registers would show
 * ------------------------------------------------------------------------------------------------------------------ */

static uint64_t hal_time_us(void* ctx)
{
    const tw_plant* pl = (const tw_plant*)ctx;
    return (uint64_t)llround(pl->t * 1e6);
}

static int hal_encoder_read(void* ctx, int32_t* counts)
{
    tw_plant* pl = (tw_plant*)ctx;
    if (!counts) return -1;
    if (pl->fault_encoder) return -1; /* injected: the encoder stops answering */
    /* ---- motor-side counts, quantised: floor(motor angle * cpr / 2 pi) + the power-up offset */
    const double motor_rev = pl->joint.q * pl->joint.p.ratio / (2.0 * PI);
    *counts = (int32_t)floor(motor_rev * pl->enc_cpr) + pl->enc_offset;
    return 0;
}

static int hal_adc_read(void* ctx, int ch, uint32_t* code)
{
    tw_plant* pl = (tw_plant*)ctx;
    if (!code || ch < 0 || ch >= HIL_ADC_COUNT) return -1;
    double v = 0.0;
    const double vref = pl->adc[ch].p.vref_v;
    switch (ch) {
    case HIL_ADC_VBAT:
        v = pl->batt.v_term / pl->vbat_divider;
        break;
    case HIL_ADC_ISENSE: {
        const double i = pl->kind == TW_MOTOR_DC ? pl->dc.i_a : pl->bl.iq;
        v = pl->isense_offset_v + pl->isense_v_per_a * i;
        break;
    }
    case HIL_ADC_NTC:
        v = pl->fault_ntc_open ? vref : tw_ntc_divider_v(&pl->ntc, vref, pl->th.t_c[pl->n_case]);
        break;
    default:
        return -1;
    }
    *code = tw_adc_sample(&pl->adc[ch], v);
    return 0;
}

static int hal_motor_write(void* ctx, float cmd)
{
    tw_plant* pl = (tw_plant*)ctx;
    if (!isfinite(cmd)) return -1;
    pl->cmd = cmd;
    ++pl->motor_writes;
    return 0;
}

static int hal_link_recv(void* ctx, uint8_t* buf, int cap)
{
    tw_plant* pl = (tw_plant*)ctx;
    return tw_byteq_pop(&pl->to_fw, buf, cap);
}

static int hal_link_send(void* ctx, const uint8_t* buf, int len)
{
    tw_plant* pl = (tw_plant*)ctx;
    return tw_byteq_push(&pl->from_fw, buf, len) == len ? 0 : -1;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Thermal network from the params: thermal.nodes = a b c; thermal.<n>.c / .g_amb / .t0; thermal.links = a:b:G ...
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Index of a node name, or -1. */
static int node_index(char names[][32], int n, const char* name)
{
    for (int i = 0; i < n; ++i)
        if (strcmp(names[i], name) == 0) return i;
    return -1;
}

static void read_thermal(reader* r, tw_plant* pl)
{
    char names[TW_THERMAL_MAX_NODES][32];
    memset(names, 0, sizeof names);
    const char* list = tw_params_str(r->p, "thermal.nodes");
    if (!list) {
        fail(r, "'thermal.nodes' is missing", NULL);
        return;
    }
    tw_thermal* th = &pl->th;
    memset(th, 0, sizeof(*th));
    th->t_amb_c = req(r, "thermal.ambient_c");

    /* ---- node names (whitespace separated) */
    const char* s = list;
    while (*s) {
        while (*s == ' ' || *s == '\t') ++s;
        if (!*s) break;
        int k = 0;
        char nm[32];
        while (*s && *s != ' ' && *s != '\t' && k < 31) nm[k++] = *s++;
        nm[k] = 0;
        if (th->n >= TW_THERMAL_MAX_NODES) {
            fail(r, "too many thermal nodes", NULL);
            return;
        }
        snprintf(names[th->n], sizeof names[th->n], "%s", nm);
        char key[96];
        snprintf(key, sizeof key, "thermal.%s.c_j_per_k", nm);
        th->c[th->n] = req(r, key);
        snprintf(key, sizeof key, "thermal.%s.g_amb_w_per_k", nm);
        th->g_amb[th->n] = opt(r, key, 0.0);
        th->t_c[th->n] = th->t_amb_c;
        ++th->n;
    }

    /* ---- links "a:b:G" */
    const char* links = tw_params_str(r->p, "thermal.links");
    s = links ? links : "";
    while (*s) {
        while (*s == ' ' || *s == '\t') ++s;
        if (!*s) break;
        char a[32], b[32];
        double g = 0.0;
        int used = 0;
        if (sscanf(s, "%31[^:]:%31[^:]:%lf%n", a, b, &g, &used) != 3 || used <= 0) {
            fail(r, "thermal.links: expected 'a:b:G' entries near: ", s);
            return;
        }
        s += used;
        int ia = node_index(names, th->n, a), ib = node_index(names, th->n, b);
        if (ia < 0 || ib < 0) {
            fail(r, "thermal.links names an unknown node: ", ia < 0 ? a : b);
            return;
        }
        if (th->n_links >= TW_THERMAL_MAX_LINKS) {
            fail(r, "too many thermal links", NULL);
            return;
        }
        th->link[th->n_links].a = ia;
        th->link[th->n_links].b = ib;
        th->link[th->n_links].g = g;
        ++th->n_links;
    }

    /* ---- which node is which (the plant's heat sources and the NTC) */
    const char* w = tw_params_str(r->p, "thermal.winding_node");
    const char* c = tw_params_str(r->p, "thermal.case_node");
    const char* bt = tw_params_str(r->p, "thermal.battery_node");
    pl->n_winding = w ? node_index(names, th->n, w) : -1;
    pl->n_case = c ? node_index(names, th->n, c) : -1;
    pl->n_batt = bt ? node_index(names, th->n, bt) : -1;
    if (pl->n_winding < 0 || pl->n_case < 0 || pl->n_batt < 0)
        fail(r, "thermal.winding_node / case_node / battery_node must name nodes of thermal.nodes", NULL);
    else if (tw_thermal_validate(th) != 0)
        fail(r, "thermal network invalid (capacities > 0, every node with a path to ambient)", NULL);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Create
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_plant_create(tw_plant* pl, tw_params* p, uint64_t seed, const char* trace, char* err, size_t errlen)
{
    if (!pl || !p) return -1;
    memset(pl, 0, sizeof(*pl));
    reader r = {p, err, errlen, 0};
    pl->seed = seed;
    pl->dt = req(&r, "dt_s");
    if (!r.bad && !(pl->dt > 0.0 && pl->dt <= 0.01)) fail(&r, "dt_s must be in (0, 0.01] s", NULL);
    if (!r.bad && fabs(pl->dt - (double)HIL_CORE_DT) > 1e-9)
        fail(&r, "dt_s must equal the firmware tick HIL_CORE_DT (lockstep)", NULL);

    /* ---- motor */
    const char* kind = tw_params_str(p, "motor.kind");
    if (!kind) fail(&r, "'motor.kind' is missing (dc or pmsm)", NULL);
    else if (strcmp(kind, "dc") == 0) pl->kind = TW_MOTOR_DC;
    else if (strcmp(kind, "pmsm") == 0) pl->kind = TW_MOTOR_PMSM;
    else fail(&r, "motor.kind must be dc or pmsm, got ", kind);
    double j_rotor = 0.0;
    if (!r.bad && pl->kind == TW_MOTOR_DC) {
        tw_motor_params mp;
        mp.r20_ohm = req(&r, "motor.dc.r20_ohm");
        mp.l_h = req(&r, "motor.dc.l_h");
        mp.kt = req(&r, "motor.dc.kt_nm_per_a");
        mp.j_rotor = req(&r, "motor.dc.j_rotor_kgm2");
        mp.i_max_a = req(&r, "motor.dc.i_max_a");
        mp.alpha_cu = opt(&r, "motor.dc.alpha_cu", 0.00393);
        if (!r.bad && tw_motor_init(&pl->dc, &mp) != 0) fail(&r, "motor.dc.* out of range", NULL);
        j_rotor = mp.j_rotor;
    } else if (!r.bad) {
        tw_pmsm_params bp;
        bp.r_ohm = req(&r, "motor.pmsm.r_ohm");
        bp.ld_h = req(&r, "motor.pmsm.ld_h");
        bp.lq_h = req(&r, "motor.pmsm.lq_h");
        bp.psi_m = req(&r, "motor.pmsm.psi_m_vs");
        bp.pole_pairs = (int)req(&r, "motor.pmsm.pole_pairs");
        bp.j_rotor = req(&r, "motor.pmsm.j_rotor_kgm2");
        bp.alpha_cu = opt(&r, "motor.pmsm.alpha_cu", 0.00393);
        bp.i_max_a = req(&r, "motor.pmsm.i_max_a");
        bp.kp_v_per_a = req(&r, "motor.pmsm.drive_kp_v_per_a");
        bp.ki_v_per_as = req(&r, "motor.pmsm.drive_ki_v_per_as");
        bp.n_sub = (int)opt(&r, "motor.pmsm.drive_substeps", 20);
        if (!r.bad && tw_pmsm_init(&pl->bl, &bp) != 0) fail(&r, "motor.pmsm.* out of range", NULL);
        j_rotor = bp.j_rotor;
    }

    /* ---- joint and link (the link mass properties come from the CAD export include) */
    tw_joint_params jp;
    jp.ratio = req(&r, "joint.ratio");
    jp.efficiency = req(&r, "joint.efficiency");
    jp.j_link = req(&r, "joint.j_link_kgm2");
    jp.m_link = req(&r, "joint.m_link_kg");
    jp.l_com = req(&r, "joint.l_com_m");
    jp.gravity = opt(&r, "gravity_m_s2", 9.80665);
    jp.b_out = req(&r, "joint.b_out_nms");
    jp.tau_coulomb = req(&r, "joint.tau_coulomb_nm");
    jp.qd_slip = opt(&r, "joint.qd_slip_rad_s", 0.01);
    jp.q_min = req(&r, "joint.q_min_rad");
    jp.q_max = req(&r, "joint.q_max_rad");
    jp.k_stop = req(&r, "joint.k_stop_nm_per_rad");
    jp.c_stop = req(&r, "joint.c_stop_nms");
    const double q0 = req(&r, "joint.q0_rad");
    if (!r.bad && tw_joint_init(&pl->joint, &jp, q0) != 0) fail(&r, "joint.* out of range", NULL);
    (void)j_rotor;

    /* ---- battery */
    tw_battery_params bpp;
    memset(&bpp, 0, sizeof bpp);
    bpp.capacity_ah = req(&r, "battery.capacity_ah");
    bpp.n_series = (int)req(&r, "battery.n_series");
    bpp.r0_ohm = req(&r, "battery.r0_ohm");
    bpp.r1_ohm = req(&r, "battery.r1_ohm");
    bpp.c1_f = req(&r, "battery.c1_f");
    double soc_tab[TW_BATT_MAX_OCV * 2];
    bpp.n_ocv = (int)req(&r, "battery.ocv_points");
    if (!r.bad && (bpp.n_ocv < 2 || bpp.n_ocv > TW_BATT_MAX_OCV)) fail(&r, "battery.ocv_points must be 2..16", NULL);
    if (!r.bad && tw_params_vec(p, "battery.ocv_soc_v", soc_tab, bpp.n_ocv * 2) != 0)
        fail(&r, "battery.ocv_soc_v must hold ocv_points pairs 'soc v'", NULL);
    for (int i = 0; !r.bad && i < bpp.n_ocv; ++i) {
        bpp.ocv_soc[i] = soc_tab[2 * i];
        bpp.ocv_v[i] = soc_tab[2 * i + 1];
    }
    const double soc0 = req(&r, "battery.soc0");
    if (!r.bad && tw_battery_init(&pl->batt, &bpp, soc0) != 0) fail(&r, "battery.* out of range", NULL);
    pl->i_quiescent_a = opt(&r, "battery.i_quiescent_a", 0.0);

    /* ---- thermal */
    if (!r.bad) read_thermal(&r, pl);

    /* ---- sensors: true values with per-unit tolerances (the firmware only knows the nominal ones) */
    tw_rng unit;
    tw_rng_seed(&unit, seed, tw_stream_user + 1);
    pl->enc_cpr = req(&r, "sensor.encoder_cpr");
    pl->enc_offset = (int32_t)floor(tw_rng_uniform(&unit) * 100000.0); /* power-up position is arbitrary */
    pl->isense_v_per_a = req(&r, "sensor.isense_v_per_a") * (1.0 + opt(&r, "sensor.isense_gain_sigma", 0.0) * tw_rng_normal(&unit));
    pl->isense_offset_v = req(&r, "sensor.isense_offset_v") + opt(&r, "sensor.isense_offset_sigma_v", 0.0) * tw_rng_normal(&unit);
    pl->vbat_divider = req(&r, "sensor.vbat_divider");
    pl->ntc.r25_ohm = req(&r, "sensor.ntc_r25_ohm");
    pl->ntc.beta_k = req(&r, "sensor.ntc_beta_k");
    pl->ntc.r_top_ohm = req(&r, "sensor.ntc_r_top_ohm");
    tw_adc_params ap;
    ap.bits = (int)req(&r, "sensor.adc_bits");
    ap.vref_v = req(&r, "sensor.adc_vref_v");
    ap.noise_lsb = req(&r, "sensor.adc_noise_lsb");
    for (int ch = 0; !r.bad && ch < HIL_ADC_COUNT; ++ch)
        if (tw_adc_init(&pl->adc[ch], &ap, seed, (uint64_t)ch) != 0) fail(&r, "sensor.adc_* out of range", NULL);
    if (!r.bad && (!(pl->enc_cpr > 0.0) || !(pl->vbat_divider > 0.0) || !(pl->isense_v_per_a > 0.0)))
        fail(&r, "sensor.* out of range", NULL);

    /* ---- the firmware's own view of the board (fw.* keys: from the schematic, never copied from the truth) */
    hil_core_board_t b;
    memset(&b, 0, sizeof b);
    b.drive = pl->kind == TW_MOTOR_DC ? HIL_DRIVE_DC_DUTY : HIL_DRIVE_FOC_IQ;
    b.encoder_cpr = (float)req(&r, "fw.encoder_cpr");
    b.gear_ratio = (float)req(&r, "fw.gear_ratio");
    b.gear_efficiency = (float)req(&r, "fw.gear_efficiency");
    b.kt_nm_per_a = (float)req(&r, "fw.kt_nm_per_a");
    b.r_ohm = (float)opt(&r, "fw.r_ohm", 0.0);
    b.adc_bits = (int)req(&r, "fw.adc_bits");
    b.adc_vref_v = (float)req(&r, "fw.adc_vref_v");
    b.vbat_divider = (float)req(&r, "fw.vbat_divider");
    b.isense_v_per_a = (float)req(&r, "fw.isense_v_per_a");
    b.isense_offset_v = (float)req(&r, "fw.isense_offset_v");
    b.ntc_r25_ohm = (float)req(&r, "fw.ntc_r25_ohm");
    b.ntc_beta_k = (float)req(&r, "fw.ntc_beta_k");
    b.ntc_r_top_ohm = (float)req(&r, "fw.ntc_r_top_ohm");
    b.gravity_nm = (float)req(&r, "fw.gravity_nm");
    b.i_max_a = (float)req(&r, "fw.i_max_a");
    b.winding_rth_k_per_w = (float)opt(&r, "fw.winding_rth_k_per_w", 0.0);
    b.winding_tau_s = (float)opt(&r, "fw.winding_tau_s", 0.0);
    b.q_home_rad = (float)req(&r, "fw.q_home_rad");
    b.q_soft_min_rad = (float)req(&r, "fw.q_soft_min_rad");
    b.q_soft_max_rad = (float)req(&r, "fw.q_soft_max_rad");
    hil_core_tuning_t tun = hil_core_default_tuning();
    tun.vel_kp = (float)opt(&r, "fw.tune.vel_kp", tun.vel_kp);
    tun.vel_ki = (float)opt(&r, "fw.tune.vel_ki", tun.vel_ki);
    tun.pos_kp = (float)opt(&r, "fw.tune.pos_kp", tun.pos_kp);
    tun.v_min = (float)opt(&r, "fw.tune.v_min", tun.v_min);
    tun.t_derate_start_c = (float)opt(&r, "fw.tune.t_derate_start_c", tun.t_derate_start_c);
    tun.t_derate_end_c = (float)opt(&r, "fw.tune.t_derate_end_c", tun.t_derate_end_c);
    tun.t_trip_c = (float)opt(&r, "fw.tune.t_trip_c", tun.t_trip_c);
    tun.home_current_a = (float)opt(&r, "fw.tune.home_current_a", tun.home_current_a);
    if (r.bad) return -1;

    hal_t hal = {pl, hal_time_us, hal_encoder_read, hal_adc_read, hal_motor_write, hal_link_recv, hal_link_send};
    if (hil_core_init(&pl->fw, &hal, &b, &tun) != 0) {
        fail(&r, "fw.* board constants or fw.tune.* rejected by hil_core_init", NULL);
        return -1;
    }

    /* ---- trace */
    if (tw_trace_open(&pl->trace, trace, (int)opt(&r, "trace.decimate", 10)) != 0) {
        fail(&r, "cannot open the trace file ", trace);
        return -1;
    }
    tw_trace_header(&pl->trace, "t_s,q_rad,qd_rad_s,tau_motor_nm,cmd,i_a,vbat_v,soc,t_winding_c,t_case_c,fw_state");
    return r.bad ? -1 : 0;
}

void tw_plant_destroy(tw_plant* pl)
{
    if (pl) tw_trace_close(&pl->trace);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Step
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Everything but the joint integration: firmware, actuator, battery, thermal, trace. */
static int step_common(tw_plant* pl)
{
    /* ---- 1. the firmware tick reads the sensors as they are at the start of the step */
    (void)hil_core_step(&pl->fw);

    /* ---- 2. actuator */
    const double w_shaft = pl->joint.p.ratio * pl->joint.qd;
    const double t_w = pl->th.t_c[pl->n_winding];
    double i_bus = 0.0, p_cu = 0.0;
    if (pl->kind == TW_MOTOR_DC) {
        if (tw_motor_step(&pl->dc, pl->cmd, pl->batt.v_term, t_w, w_shaft, pl->dt) < 0) return -1;
        pl->tau_motor = pl->dc.torque_nm;
        i_bus = pl->dc.i_bus_a;
        p_cu = pl->dc.p_copper_w;
    } else {
        if (tw_pmsm_step(&pl->bl, pl->cmd, 0.0, pl->batt.v_term, t_w, w_shaft, pl->dt) < 0) return -1;
        pl->tau_motor = pl->bl.torque_nm;
        i_bus = pl->bl.i_bus_a;
        p_cu = pl->bl.p_copper_w;
    }

    /* ---- 3. battery and thermal */
    if (tw_battery_step(&pl->batt, i_bus + pl->i_quiescent_a, pl->dt) < 0) return -1;
    for (int i = 0; i < pl->th.n; ++i) pl->th.p_w[i] = 0.0;
    pl->th.p_w[pl->n_winding] += p_cu;
    pl->th.p_w[pl->n_batt] += pl->batt.heat_w;
    if (tw_thermal_step(&pl->th, pl->dt) != 0) return -1;
    return 0;
}

/** @brief Trace row and clock. */
static void finish(tw_plant* pl)
{
    pl->t += pl->dt;
    ++pl->steps;
    const double i = pl->kind == TW_MOTOR_DC ? pl->dc.i_a : pl->bl.iq;
    const double row[11] = {pl->t, pl->joint.q, pl->joint.qd, pl->tau_motor, pl->cmd, i, pl->batt.v_term,
                            pl->batt.soc, pl->th.t_c[pl->n_winding], pl->th.t_c[pl->n_case], (double)pl->fw.state};
    tw_trace_row(&pl->trace, row, 11);
}

int tw_plant_step(tw_plant* pl)
{
    if (!pl) return -1;
    pl->external = 0;
    if (step_common(pl) != 0) return -1;
    const double j_rotor = pl->kind == TW_MOTOR_DC ? pl->dc.p.j_rotor : pl->bl.p.j_rotor;
    int rc = tw_joint_step(&pl->joint, pl->tau_motor, pl->ext_torque_nm, j_rotor, pl->dt);
    if (rc != 0) return rc;
    finish(pl);
    return 0;
}

int tw_plant_step_external(tw_plant* pl, double q, double qd, double* tau_out)
{
    if (!pl || !tau_out || !isfinite(q) || !isfinite(qd)) return -1;
    pl->external = 1;
    /* ---- the host's joint state is the truth this step */
    pl->joint.q = q;
    pl->joint.qd = qd;
    if (step_common(pl) != 0) return -1;
    *tau_out = tw_joint_actuator_torque(&pl->joint, pl->tau_motor, q, qd) + pl->ext_torque_nm;
    finish(pl);
    return 0;
}
