/**
 * @file actuators.c
 * @brief DC motor, gearbox and revolute joint (equations and references in actuators.h).
 */
#include "hil_twin/actuators.h"

#include <string.h>

/* ------------------------------------------------------------------------------------------------------------------
 *  DC motor
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_motor_init(tw_motor* m, const tw_motor_params* p)
{
    if (!m || !p) return -1;
    /* ---- every constant must be physical; a zero resistance or inertia would divide by zero below */
    if (!(p->r20_ohm > 0.0) || !(p->l_h >= 0.0) || !(p->kt > 0.0) || !(p->j_rotor > 0.0) || !(p->i_max_a > 0.0))
        return -1;
    if (!isfinite(p->alpha_cu) || !isfinite(p->r20_ohm) || !isfinite(p->kt)) return -1;
    memset(m, 0, sizeof(*m));
    m->p = *p;
    return 0;
}

int tw_motor_step(tw_motor* m, double duty, double v_bus, double t_winding_c, double w_shaft, double dt)
{
    if (!m || !(dt > 0.0) || !isfinite(dt) || !isfinite(v_bus) || v_bus < 0.0 || !isfinite(t_winding_c) ||
        !isfinite(w_shaft))
        return -1;
    int rc = 0;

    /* ---- command conditioning: NaN means the firmware wrote garbage; drive off and say so */
    if (!isfinite(duty)) {
        duty = 0.0;
        rc = 1;
    }
    if (duty > 1.0) duty = 1.0;
    if (duty < -1.0) duty = -1.0;

    const tw_motor_params* p = &m->p;
    const double v = duty * v_bus;

    /* ---- resistance at the winding temperature (never below 10 % of R20: the model is linear) */
    double r = p->r20_ohm * (1.0 + p->alpha_cu * (t_winding_c - 20.0));
    if (r < 0.1 * p->r20_ohm) r = 0.1 * p->r20_ohm;

    /* ---- electrical: exact exponential step towards the quasi-static current */
    const double i_inf = (v - p->kt * w_shaft) / r;
    if (p->l_h > 0.0) {
        const double e = exp(-dt * r / p->l_h);
        m->i_a = i_inf + (m->i_a - i_inf) * e;
    } else {
        m->i_a = i_inf;
    }

    /* ---- drive current limit, both directions (the drive's own hardware limit, not the firmware's) */
    if (m->i_a > p->i_max_a) m->i_a = p->i_max_a;
    if (m->i_a < -p->i_max_a) m->i_a = -p->i_max_a;

    /* ---- outputs */
    m->torque_nm = p->kt * m->i_a;
    m->p_copper_w = m->i_a * m->i_a * r;
    m->i_bus_a = duty * m->i_a;
    return rc;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Joint
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_joint_init(tw_joint* j, const tw_joint_params* p, double q0)
{
    if (!j || !p) return -1;
    if (!(p->ratio > 0.0) || !(p->efficiency > 0.0) || p->efficiency > 1.0 || !(p->j_link > 0.0)) return -1;
    if (!(p->m_link >= 0.0) || !(p->l_com >= 0.0) || !(p->gravity >= 0.0) || !(p->b_out >= 0.0)) return -1;
    if (!(p->tau_coulomb >= 0.0) || !(p->qd_slip > 0.0) || !(p->q_max > p->q_min)) return -1;
    if (!(p->k_stop >= 0.0) || !(p->c_stop >= 0.0) || !isfinite(q0)) return -1;
    memset(j, 0, sizeof(*j));
    j->p = *p;
    j->q = q0;
    return 0;
}

double tw_joint_inertia(const tw_joint* j, double j_rotor)
{
    if (!j) return 0.0;
    return j->p.j_link + j->p.ratio * j->p.ratio * j_rotor;
}

/** @brief Hard-stop torque: a spring-damper that only pushes the joint back inside its range. */
static double stop_torque(const tw_joint_params* p, double q, double qd)
{
    double t = 0.0;
    if (q > p->q_max) {
        t = -p->k_stop * (q - p->q_max) - p->c_stop * qd;
        if (t > 0.0) t = 0.0;
    } else if (q < p->q_min) {
        t = -p->k_stop * (q - p->q_min) - p->c_stop * qd;
        if (t < 0.0) t = 0.0;
    }
    return t;
}

double tw_joint_actuator_torque(const tw_joint* j, double tau_motor, double q, double qd)
{
    if (!j) return 0.0;
    const tw_joint_params* p = &j->p;
    /* ---- gearbox output, friction, stops (no gravity, no inertia: those belong to whoever integrates) */
    return p->efficiency * p->ratio * tau_motor - p->b_out * qd - p->tau_coulomb * tanh(qd / p->qd_slip) +
           stop_torque(p, q, qd);
}

int tw_joint_step(tw_joint* j, double tau_motor, double tau_ext, double j_rotor, double dt)
{
    if (!j || !isfinite(tau_motor) || !isfinite(tau_ext) || !(j_rotor >= 0.0) || !(dt > 0.0) || !isfinite(dt))
        return -1;
    const tw_joint_params* p = &j->p;

    /* ---- torque balance at the output */
    const double tau_g = -p->m_link * p->gravity * p->l_com * cos(j->q);
    const double tau_act = tw_joint_actuator_torque(j, tau_motor, j->q, j->qd);
    const double J = tw_joint_inertia(j, j_rotor);
    const double qdd = (tau_act + tau_g + tau_ext) / J;

    /* ---- semi-implicit Euler: symplectic for the conservative part, stable with the stop springs at 1 kHz */
    const double qd_new = j->qd + dt * qdd;
    const double q_new = j->q + dt * qd_new;
    if (!isfinite(qd_new) || !isfinite(q_new)) return -2;
    j->qdd = qdd;
    j->qd = qd_new;
    j->q = q_new;
    j->tau_stop = stop_torque(p, j->q, j->qd);
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  PMSM + FOC drive
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_pmsm_init(tw_pmsm* m, const tw_pmsm_params* p)
{
    if (!m || !p) return -1;
    if (!(p->r_ohm > 0.0) || !(p->ld_h > 0.0) || !(p->lq_h > 0.0) || !(p->psi_m > 0.0) || p->pole_pairs < 1) return -1;
    if (!(p->j_rotor > 0.0) || !isfinite(p->alpha_cu) || !(p->i_max_a > 0.0)) return -1;
    if (!(p->kp_v_per_a >= 0.0) || !(p->ki_v_per_as >= 0.0) || p->n_sub < 1 || p->n_sub > 200) return -1;
    memset(m, 0, sizeof(*m));
    m->p = *p;
    return 0;
}

/** @brief Exact first-order step of L di/dt = v_eff - R i over h. */
static double rl_step(double i, double v_eff, double r, double l, double h)
{
    const double i_inf = v_eff / r;
    return i_inf + (i - i_inf) * exp(-h * r / l);
}

int tw_pmsm_step(tw_pmsm* m, double iq_cmd, double id_cmd, double v_bus, double t_winding_c, double w_shaft,
                 double dt)
{
    if (!m || !(dt > 0.0) || !isfinite(dt) || !isfinite(v_bus) || v_bus < 0.0 || !isfinite(t_winding_c) ||
        !isfinite(w_shaft))
        return -1;
    int rc = 0;
    if (!isfinite(iq_cmd)) { iq_cmd = 0.0; rc = 1; }
    if (!isfinite(id_cmd)) { id_cmd = 0.0; rc = 1; }
    const tw_pmsm_params* p = &m->p;
    if (iq_cmd > p->i_max_a) iq_cmd = p->i_max_a;
    if (iq_cmd < -p->i_max_a) iq_cmd = -p->i_max_a;

    double r = p->r_ohm * (1.0 + p->alpha_cu * (t_winding_c - 20.0));
    if (r < 0.1 * p->r_ohm) r = 0.1 * p->r_ohm;
    const double we = p->pole_pairs * w_shaft;
    const double vmax = v_bus / sqrt(3.0);
    const double h = dt / p->n_sub;
    m->saturated = 0;

    for (int k = 0; k < p->n_sub; ++k) {
        /* ---- drive: PI per axis plus the decoupling feed-forward a real FOC drive applies */
        const double ed = id_cmd - m->id, eq = iq_cmd - m->iq;
        double vd = m->int_d + p->kp_v_per_a * ed - we * p->lq_h * m->iq;
        double vq = m->int_q + p->kp_v_per_a * eq + we * (p->ld_h * m->id + p->psi_m);

        /* ---- voltage limit (linear SVPWM range); integrators only run while unsaturated (anti-windup) */
        const double vmag = sqrt(vd * vd + vq * vq);
        if (vmag > vmax && vmag > 0.0) {
            vd *= vmax / vmag;
            vq *= vmax / vmag;
            m->saturated = 1;
        } else {
            m->int_d += p->ki_v_per_as * ed * h;
            m->int_q += p->ki_v_per_as * eq * h;
        }

        /* ---- machine: each axis exact over h, the speed-voltage cross terms held */
        const double vd_eff = vd + we * p->lq_h * m->iq;
        const double vq_eff = vq - we * (p->ld_h * m->id + p->psi_m);
        m->id = rl_step(m->id, vd_eff, r, p->ld_h, h);
        m->iq = rl_step(m->iq, vq_eff, r, p->lq_h, h);
        m->vd = vd;
        m->vq = vq;
    }

    /* ---- outputs: torque, losses, bus current from the power balance */
    m->torque_nm = 1.5 * p->pole_pairs * (p->psi_m * m->iq + (p->ld_h - p->lq_h) * m->id * m->iq);
    m->p_copper_w = 1.5 * r * (m->id * m->id + m->iq * m->iq);
    m->i_bus_a = v_bus > 1e-6 ? 1.5 * (m->vd * m->id + m->vq * m->iq) / v_bus : 0.0;
    return rc;
}
