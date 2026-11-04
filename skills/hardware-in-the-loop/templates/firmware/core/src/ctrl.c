/**
 * @file ctrl.c
 * @brief Example joint controller: link handling, measurement, supervision, homing, cascaded servo, drive output.
 *
 * Order inside one tick (fixed, so the twin and the target behave the same):
 *     1. link      drain received bytes, act on complete frames
 *     2. measure   encoder -> angle and filtered rate; ADC -> battery voltage, motor current, motor temperature
 *     3. supervise temperature derate and trip, low battery
 *     4. control   the state machine: IDLE / HOMING / RUN / FAULT
 *     5. output    one motor_write per tick, always (0 when not driving)
 *     6. telemetry every 20 ticks (50 Hz)
 */
#include "hil_core/ctrl.h"

#include <math.h>
#include <string.h>

#define TWO_PI_F 6.28318530718f

/* ------------------------------------------------------------------------------------------------------------------
 *  Small helpers
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Clamp to [lo, hi]. */
static float clampf(float x, float lo, float hi) { return x < lo ? lo : (x > hi ? hi : x); }

/** @brief True for a finite float (no NaN, no infinity). */
static int finitef(float x) { return isfinite(x) != 0; }

/** @brief ADC code to pin voltage. */
static float code_to_v(const hil_core_board_t* b, uint32_t code)
{
    const float full = (float)((1u << b->adc_bits) - 1u);
    return (float)code / full * b->adc_vref_v;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Defaults and init
 * ------------------------------------------------------------------------------------------------------------------ */

hil_core_tuning_t hil_core_default_tuning(void)
{
    hil_core_tuning_t t;
    memset(&t, 0, sizeof t);
    t.pos_kp = 30.0f;
    t.vel_kp = 0.6f;
    t.vel_ki = 6.0f;
    t.cur_kp = 0.15f;
    t.cur_ki = 120.0f;
    t.vel_max = 3.0f;
    t.acc_max = 15.0f;
    t.vel_filter_hz = 80.0f;
    t.home_speed = -0.4f;
    t.home_current_a = 1.2f;
    t.home_timeout_s = 15.0f;
    t.t_derate_start_c = 70.0f;
    t.t_derate_end_c = 90.0f;
    t.derate_min = 0.3f;
    t.t_trip_c = 100.0f;
    t.v_min = 10.5f;
    t.stall_time_s = 1.0f;
    return t;
}

int hil_core_init(hil_core_t* c, const hal_t* hal, const hil_core_board_t* b, const hil_core_tuning_t* t)
{
    if (!c || !hal || !b || !t) return -1;
    /* ---- every HAL entry must exist: a missing driver is a build problem, found at boot */
    if (!hal->time_us || !hal->encoder_read || !hal->adc_read || !hal->motor_write || !hal->link_recv ||
        !hal->link_send)
        return -1;
    /* ---- board constants must be physical */
    if (b->drive != HIL_DRIVE_DC_DUTY && b->drive != HIL_DRIVE_FOC_IQ) return -1;
    if (!(b->encoder_cpr > 0.0f) || !(b->gear_ratio > 0.0f) || !(b->gear_efficiency > 0.0f) ||
        b->gear_efficiency > 1.0f || !(b->kt_nm_per_a > 0.0f) || !(b->r_ohm >= 0.0f))
        return -1;
    if (b->adc_bits < 8 || b->adc_bits > 24 || !(b->adc_vref_v > 0.0f) || !(b->vbat_divider > 0.0f) ||
        !(b->isense_v_per_a > 0.0f))
        return -1;
    if (!(b->ntc_r25_ohm > 0.0f) || !(b->ntc_beta_k > 0.0f) || !(b->ntc_r_top_ohm > 0.0f) || !(b->i_max_a > 0.0f))
        return -1;
    if (!(b->winding_rth_k_per_w >= 0.0f) || !(b->winding_tau_s >= 0.0f)) return -1;
    if (!finitef(b->gravity_nm) || !finitef(b->q_home_rad) || !(b->q_soft_max_rad > b->q_soft_min_rad)) return -1;
    /* ---- tunables must be finite and the derate band ordered */
    if (!(t->t_derate_end_c > t->t_derate_start_c) || !(t->t_trip_c >= t->t_derate_end_c) ||
        !(t->derate_min > 0.0f && t->derate_min <= 1.0f) || !(t->vel_max > 0.0f) || !(t->acc_max > 0.0f) ||
        !(t->vel_filter_hz > 0.0f) || !(t->home_current_a > 0.0f) || !(t->home_timeout_s > 0.0f))
        return -1;

    memset(c, 0, sizeof(*c));
    c->hal = *hal;
    c->board = *b;
    c->tun = *t;
    c->state = HIL_ST_IDLE;
    c->derate = 1.0f;
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  1. Link
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Leave every motion state and clear the loop memories. */
static void to_state(hil_core_t* c, hil_core_state_t s)
{
    c->state = s;
    c->vel_int = c->cur_int = 0.0f;
    c->i_cmd = c->cmd_out = 0.0f;
    c->home_t = c->home_still_t = c->stall_t = 0.0f;
    c->qd_sp = 0.0f;
    c->q_sp = c->q;
}

/** @brief Act on one complete frame. */
static void on_frame(hil_core_t* c, uint8_t type, const uint8_t* pl, int len)
{
    switch (type) {
    case HIL_MSG_ARM:
        if (len != 1) break;
        if (pl[0] == 0) {
            /* ---- disarm: always allowed, clears the faults */
            c->fault = 0;
            to_state(c, HIL_ST_IDLE);
        } else if (c->state == HIL_ST_IDLE && c->fault == 0) {
            /* ---- arming with a latched fault (a sensor that failed while idle) is refused */
            to_state(c, HIL_ST_HOMING);
        }
        break;
    case HIL_MSG_SET_POS:
        if (len != 4) break;
        {
            float q = hil_get_f32(pl);
            if (finitef(q)) c->q_target = clampf(q, c->board.q_soft_min_rad, c->board.q_soft_max_rad);
        }
        break;
    case HIL_MSG_STOP:
        c->q_target = c->q;
        break;
    default:
        break; /* unknown types are ignored, never fatal: a newer host may send more */
    }
}

/** @brief Drain the receive path (bounded per tick so a flood cannot starve the loop). */
static void link_poll(hil_core_t* c)
{
    uint8_t buf[64];
    int n = c->hal.link_recv(c->hal.ctx, buf, (int)sizeof buf);
    if (n <= 0) return;
    for (int i = 0; i < n; ++i) {
        uint8_t type = 0, pl[HIL_LINK_MAX_PAYLOAD];
        int len = 0;
        int r = hil_link_feed(&c->rx, buf[i], &type, pl, &len);
        if (r == 1) {
            ++c->link_frames;
            on_frame(c, type, pl, len);
        } else if (r < 0) {
            ++c->link_bad;
        }
    }
}

/* ------------------------------------------------------------------------------------------------------------------
 *  2. Measurement
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Read the encoder and the ADC; returns 0 or a negative HAL code (fault bits set). */
static int measure(hil_core_t* c)
{
    const hil_core_board_t* b = &c->board;
    int rc = 0;

    /* ---- encoder -> joint angle and a low-passed rate */
    int32_t cnt = 0;
    if (c->hal.encoder_read(c->hal.ctx, &cnt) != 0) {
        c->fault |= HIL_FAULT_ENCODER;
        rc = -1;
    } else {
        const float scale = TWO_PI_F / (b->encoder_cpr * b->gear_ratio);
        if (!c->enc_have) {
            c->enc_prev = cnt;
            c->enc_have = 1;
        }
        /* ---- difference in 32-bit modular arithmetic: survives a counter wrap */
        const int32_t d = (int32_t)((uint32_t)cnt - (uint32_t)c->enc_prev);
        c->enc_prev = cnt;
        const float qd_raw = (float)d * scale / HIL_CORE_DT;
        const float a = 1.0f - expf(-TWO_PI_F * c->tun.vel_filter_hz * HIL_CORE_DT);
        c->qd += a * (qd_raw - c->qd);
        c->q = (float)cnt * scale + c->q_offset;
    }

    /* ---- ADC: battery, current, temperature */
    uint32_t code[HIL_ADC_COUNT] = {0};
    for (int ch = 0; ch < HIL_ADC_COUNT; ++ch) {
        if (c->hal.adc_read(c->hal.ctx, ch, &code[ch]) != 0) {
            c->fault |= HIL_FAULT_ADC;
            rc = -1;
        }
    }
    if (rc == 0) {
        c->vbat = code_to_v(b, code[HIL_ADC_VBAT]) * b->vbat_divider;
        c->i_meas = (code_to_v(b, code[HIL_ADC_ISENSE]) - b->isense_offset_v) / b->isense_v_per_a;

        /* ---- NTC: invert the divider, then the beta model [REF-013]. A pin at either rail is an open or a short */
        const float v = code_to_v(b, code[HIL_ADC_NTC]);
        if (v < 0.01f * b->adc_vref_v || v > 0.99f * b->adc_vref_v) {
            c->fault |= HIL_FAULT_ADC;
            rc = -1;
        } else {
            const float r = b->ntc_r_top_ohm * v / (b->adc_vref_v - v);
            const float inv_t = 1.0f / 298.15f + logf(r / b->ntc_r25_ohm) / b->ntc_beta_k;
            c->t_motor_c = 1.0f / inv_t - 273.15f;
        }
    }

    /* ---- winding observer: d(dT)/dt = (P R_th - dT) / tau. P = k i^2 R(T): k = 1 for a brushed motor, 1.5 for
     *      a PMSM whose i is the dq current amplitude; R at the estimated winding temperature */
    if (rc == 0 && b->winding_rth_k_per_w > 0.0f && b->winding_tau_s > 0.0f) {
        const float t_w = c->t_motor_c + c->dt_winding;
        const float r = b->r_ohm * (1.0f + 0.00393f * (t_w - 20.0f));
        const float k = b->drive == HIL_DRIVE_FOC_IQ ? 1.5f : 1.0f;
        const float p = k * c->i_meas * c->i_meas * (r > 0.0f ? r : 0.0f);
        c->dt_winding += (p * b->winding_rth_k_per_w - c->dt_winding) * (HIL_CORE_DT / b->winding_tau_s);
    }
    return rc;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  3. Supervision
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Derate from temperature; trip on over-temperature and low battery while driving. */
static void supervise(hil_core_t* c)
{
    const hil_core_tuning_t* t = &c->tun;
    /* ---- the hotter of the case (measured) and the winding (estimated) drives the derate and the trip */
    const float t_hot = c->t_motor_c + (c->dt_winding > 0.0f ? c->dt_winding : 0.0f);
    /* ---- linear derate between start and end, floor at derate_min */
    if (t_hot <= t->t_derate_start_c) {
        c->derate = 1.0f;
    } else if (t_hot >= t->t_derate_end_c) {
        c->derate = t->derate_min;
    } else {
        const float f = (t_hot - t->t_derate_start_c) / (t->t_derate_end_c - t->t_derate_start_c);
        c->derate = 1.0f - f * (1.0f - t->derate_min);
    }
    const int driving = c->state == HIL_ST_HOMING || c->state == HIL_ST_RUN;
    if (driving && t_hot > t->t_trip_c) c->fault |= HIL_FAULT_OVERTEMP;
    if (driving && c->vbat < t->v_min) c->fault |= HIL_FAULT_LOW_BATT;
    if (c->fault && c->state != HIL_ST_FAULT && c->state != HIL_ST_IDLE) to_state(c, HIL_ST_FAULT);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  4. Control
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Velocity loop -> current command, with a current limit and integrator clamping. */
static float velocity_loop(hil_core_t* c, float vel_cmd, float tau_ff, float i_lim)
{
    const hil_core_board_t* b = &c->board;
    const float k_i_to_tau = b->gear_ratio * b->gear_efficiency * b->kt_nm_per_a;
    const float tau_lim = i_lim * k_i_to_tau;
    const float e = vel_cmd - c->qd;

    /* ---- integrator clamped to the torque the current limit allows (anti-windup) */
    c->vel_int = clampf(c->vel_int + c->tun.vel_ki * e * HIL_CORE_DT, -tau_lim, tau_lim);
    const float tau = c->tun.vel_kp * e + c->vel_int + tau_ff;
    return clampf(tau / k_i_to_tau, -i_lim, i_lim);
}

/** @brief Shape the setpoint: velocity and acceleration limited, decelerates to stop on the target. */
static void shape_setpoint(hil_core_t* c)
{
    const float e = c->q_target - c->q_sp;
    /* ---- the fastest speed from which the acceleration limit still stops on the target */
    float v_des = sqrtf(2.0f * c->tun.acc_max * fabsf(e));
    v_des = fminf(v_des, c->tun.vel_max);
    if (e < 0.0f) v_des = -v_des;
    const float dv = clampf(v_des - c->qd_sp, -c->tun.acc_max * HIL_CORE_DT, c->tun.acc_max * HIL_CORE_DT);
    c->qd_sp += dv;
    c->q_sp += c->qd_sp * HIL_CORE_DT;
}

/** @brief The state machine; sets c->i_cmd. */
static void control(hil_core_t* c)
{
    const hil_core_board_t* b = &c->board;
    const hil_core_tuning_t* t = &c->tun;
    c->i_cmd = 0.0f;

    switch (c->state) {
    case HIL_ST_HOMING: {
        /* ---- creep towards the stop with a low current limit; no gravity feed-forward (the angle is not known) */
        c->home_t += HIL_CORE_DT;
        const float i_lim = fminf(t->home_current_a, b->i_max_a * c->derate);
        c->i_cmd = velocity_loop(c, t->home_speed, 0.0f, i_lim);
        /* ---- on the stop: the loop saturates and the joint stops moving */
        if (fabsf(c->qd) < 0.02f && fabsf(c->i_cmd) >= 0.95f * i_lim) c->home_still_t += HIL_CORE_DT;
        else c->home_still_t = 0.0f;
        if (c->home_t > 0.3f && c->home_still_t > 0.3f) {
            /* ---- the stop is q_home: re-zero, back off a little, start servoing */
            c->q_offset += b->q_home_rad - c->q;
            c->q = b->q_home_rad;
            to_state(c, HIL_ST_RUN);
            c->q_target = clampf(b->q_home_rad + 0.1f * (t->home_speed < 0.0f ? 1.0f : -1.0f), b->q_soft_min_rad,
                                 b->q_soft_max_rad);
        } else if (c->home_t > t->home_timeout_s) {
            c->fault |= HIL_FAULT_HOMING;
            to_state(c, HIL_ST_FAULT);
        }
        break;
    }
    case HIL_ST_RUN: {
        shape_setpoint(c);
        const float i_lim = b->i_max_a * c->derate;
        const float vel_cmd = c->qd_sp + t->pos_kp * (c->q_sp - c->q);
        const float tau_g = b->gravity_nm * cosf(c->q);
        c->i_cmd = velocity_loop(c, vel_cmd, tau_g, i_lim);
        /* ---- stall: saturated at the FULL limit (a thermal derate that sags the joint is intended, not a stall),
         *      not moving, far from the setpoint, for stall_time */
        if (c->derate >= 0.999f && fabsf(c->i_cmd) >= 0.95f * i_lim && fabsf(c->qd) < 0.01f &&
            fabsf(c->q_sp - c->q) > 0.05f)
            c->stall_t += HIL_CORE_DT;
        else
            c->stall_t = 0.0f;
        if (c->stall_t > t->stall_time_s) {
            c->fault |= HIL_FAULT_STALL;
            to_state(c, HIL_ST_FAULT);
        }
        break;
    }
    case HIL_ST_IDLE:
    case HIL_ST_FAULT:
    default:
        c->q_target = c->q;
        c->q_sp = c->q;
        break;
    }
}

/* ------------------------------------------------------------------------------------------------------------------
 *  5. Output
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Turn the current command into the drive command for this board. */
static float drive_command(hil_core_t* c)
{
    const hil_core_board_t* b = &c->board;
    if (c->state != HIL_ST_HOMING && c->state != HIL_ST_RUN) {
        c->cur_int = 0.0f;
        return 0.0f;
    }
    if (b->drive == HIL_DRIVE_FOC_IQ) return c->i_cmd; /* the drive closes the current loop */

    /* ---- brushed DC: PI on the measured current plus the back-EMF and IR feed-forward, in duty */
    const float vbat = c->vbat > 1.0f ? c->vbat : 1.0f;
    const float e = c->i_cmd - c->i_meas;
    c->cur_int = clampf(c->cur_int + c->tun.cur_ki * e * HIL_CORE_DT / vbat, -1.0f, 1.0f);
    const float ff = (b->kt_nm_per_a * b->gear_ratio * c->qd + b->r_ohm * c->i_cmd) / vbat;
    return clampf(c->tun.cur_kp * e + c->cur_int + ff, -1.0f, 1.0f);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  6. Telemetry and the tick
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Send one telemetry frame. */
static void telemetry(hil_core_t* c)
{
    hil_telem_t t;
    t.state = (uint8_t)c->state;
    t.fault = c->fault;
    t.q_rad = c->q;
    t.qd_rad_s = c->qd;
    t.i_a = c->i_meas;
    t.vbat_v = c->vbat;
    t.t_motor_c = c->t_motor_c;
    t.t_winding_est_c = c->t_motor_c + c->dt_winding;
    t.derate = c->derate;
    uint8_t pl[HIL_TELEM_LEN], frame[HIL_LINK_MAX_FRAME];
    hil_telem_pack(&t, pl);
    int n = hil_link_encode(HIL_MSG_TELEM, pl, HIL_TELEM_LEN, frame, (int)sizeof frame);
    if (n > 0) (void)c->hal.link_send(c->hal.ctx, frame, n);
}

int hil_core_step(hil_core_t* c)
{
    if (!c) return -1;
    int rc = 0;
    link_poll(c);
    if (measure(c) != 0) rc = -1;
    supervise(c);
    control(c);

    /* ---- exactly one motor write per tick; a failing driver is a fault and the next tick writes 0 */
    c->cmd_out = drive_command(c);
    if (c->hal.motor_write(c->hal.ctx, c->cmd_out) != 0) {
        c->fault |= HIL_FAULT_DRIVE;
        if (c->state != HIL_ST_IDLE) to_state(c, HIL_ST_FAULT);
        rc = -1;
    }
    if ((c->tick++ % 20u) == 0u) telemetry(c);
    return rc;
}
