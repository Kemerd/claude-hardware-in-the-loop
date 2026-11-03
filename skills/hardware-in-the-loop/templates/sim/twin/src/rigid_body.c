/**
 * @file rigid_body.c
 * @brief Newton-Euler rigid body, RK4, penalty ground contact (see rigid_body.h for the equations and references).
 */
#include "hil_twin/rigid_body.h"

#include <string.h>

/* ------------------------------------------------------------------------------------------------------------------
 *  Setup
 * ------------------------------------------------------------------------------------------------------------------ */

int tw_body_init(tw_body* b, const tw_body_params* p)
{
    if (!b || !p) return -1;
    if (!(p->mass > 0.0) || !isfinite(p->mass)) return -1;
    if (!(p->gravity >= 0.0) || !isfinite(p->gravity)) return -1;
    if (p->n_contacts < 0 || p->n_contacts > TW_MAX_CONTACTS) return -1;
    if (!(p->contact_k >= 0.0) || !(p->contact_c >= 0.0) || !(p->contact_mu >= 0.0) || !(p->contact_v_slip > 0.0))
        return -1;

    /* ---- the inertia must be symmetric and invertible; asymmetry beyond rounding means a units or frame error */
    for (int i = 0; i < 3; ++i)
        for (int j = 0; j < 3; ++j) {
            double a = p->inertia.m[i][j], c = p->inertia.m[j][i];
            if (!isfinite(a)) return -1;
            if (fabs(a - c) > 1e-9 * (fabs(a) + fabs(c) + 1e-12)) return -1;
        }
    for (int i = 0; i < 3; ++i)
        if (!(p->inertia.m[i][i] > 0.0)) return -1;

    /* ---- contact points must be finite */
    for (int i = 0; i < p->n_contacts; ++i)
        if (!tw_finite3(p->contact_body[i])) return -1;

    memset(b, 0, sizeof(*b));
    b->p = *p;
    if (tw_mat_inv(&p->inertia, &b->p.inertia_inv) != 0) return -1;
    b->s.q = tw_q_identity();
    b->ground_enabled = 1;
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Contact: spring-damper normal + regularised Coulomb friction per touching point
 * ------------------------------------------------------------------------------------------------------------------ */

/**
 * @brief Contact wrench on the body for a given state.
 * @param b        body (parameters and ground_enabled are read)
 * @param s        state to evaluate (an RK4 stage)
 * @param f_out    total contact force, world, N
 * @param tau_out  total contact torque about the CoM, BODY frame, N m
 * @param n_touch  number of touching points (may be NULL)
 * @param fn_sum   sum of the normal forces, N (may be NULL)
 */
static void contact_wrench(const tw_body* b, const tw_body_state* s, tw_vec3* f_out, tw_vec3* tau_out, int* n_touch,
                           double* fn_sum)
{
    tw_vec3 f = tw_v3(0, 0, 0), tau = tw_v3(0, 0, 0);
    int touching = 0;
    double fn_total = 0.0;
    if (b->ground_enabled) {
        const tw_vec3 w_world = tw_q_rotate(s->q, s->w);
        for (int i = 0; i < b->p.n_contacts; ++i) {
            /* ---- point position and velocity in the world */
            tw_vec3 r_w = tw_q_rotate(s->q, b->p.contact_body[i]);
            tw_vec3 p = tw_add(s->pos, r_w);
            if (p.z >= 0.0) continue;
            tw_vec3 v = tw_add(s->vel, tw_cross(w_world, r_w));

            /* ---- normal force: never pulls the body into the floor (clamped at zero) */
            double fn = -b->p.contact_k * p.z - b->p.contact_c * v.z;
            if (fn <= 0.0) continue;

            /* ---- friction: opposes the tangential slip, saturates smoothly at mu * fn */
            tw_vec3 vt = tw_v3(v.x, v.y, 0.0);
            double sp = tw_norm(vt);
            tw_vec3 ft = tw_v3(0, 0, 0);
            if (sp > 1e-12) {
                double mag = b->p.contact_mu * fn * tanh(sp / b->p.contact_v_slip);
                ft = tw_scale(vt, -mag / sp);
            }
            tw_vec3 fi = tw_v3(ft.x, ft.y, fn);
            f = tw_add(f, fi);
            /* ---- torque about the CoM, expressed in the body frame */
            tau = tw_add(tau, tw_q_rotate_inv(s->q, tw_cross(r_w, fi)));
            ++touching;
            fn_total += fn;
        }
    }
    *f_out = f;
    *tau_out = tau;
    if (n_touch) *n_touch = touching;
    if (fn_sum) *fn_sum = fn_total;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Derivatives and RK4
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Time derivative of the state (dpos, dvel, dq as a quaternion, dw). */
typedef struct {
    tw_vec3 dp, dv;
    tw_quat dq;
    tw_vec3 dw;
} deriv;

/** @brief Evaluate the Newton-Euler derivative at a state. */
static deriv derivative(const tw_body* b, const tw_body_state* s, tw_vec3 f_ext, tw_vec3 tau_ext)
{
    deriv d;
    tw_vec3 fc, tc;
    contact_wrench(b, s, &fc, &tc, NULL, NULL);

    /* ---- translation: external + contact + gravity */
    tw_vec3 f = tw_add(f_ext, fc);
    d.dp = s->vel;
    d.dv = tw_add(tw_scale(f, 1.0 / b->p.mass), tw_v3(0, 0, -b->p.gravity));

    /* ---- rotation: Euler's equation with the gyroscopic term */
    tw_vec3 tau = tw_add(tau_ext, tc);
    tw_vec3 Jw = tw_mat_mul_v(&b->p.inertia, s->w);
    d.dw = tw_mat_mul_v(&b->p.inertia_inv, tw_sub(tau, tw_cross(s->w, Jw)));

    /* ---- attitude: q_dot = 0.5 q * (0, w_body) */
    tw_quat wq = {0.0, s->w.x, s->w.y, s->w.z};
    tw_quat qd = tw_q_mul(s->q, wq);
    d.dq.w = 0.5 * qd.w;
    d.dq.x = 0.5 * qd.x;
    d.dq.y = 0.5 * qd.y;
    d.dq.z = 0.5 * qd.z;
    return d;
}

/** @brief s + h * d (the quaternion is renormalised only at the end of the full step). */
static tw_body_state advance(const tw_body_state* s, const deriv* d, double h)
{
    tw_body_state r;
    r.pos = tw_add(s->pos, tw_scale(d->dp, h));
    r.vel = tw_add(s->vel, tw_scale(d->dv, h));
    r.q.w = s->q.w + h * d->dq.w;
    r.q.x = s->q.x + h * d->dq.x;
    r.q.y = s->q.y + h * d->dq.y;
    r.q.z = s->q.z + h * d->dq.z;
    r.w = tw_add(s->w, tw_scale(d->dw, h));
    return r;
}

int tw_body_step(tw_body* b, tw_vec3 f_world, tw_vec3 tau_body, double dt)
{
    if (!b || !(dt > 0.0) || !isfinite(dt) || !tw_finite3(f_world) || !tw_finite3(tau_body)) return -1;
    const tw_body_state s0 = b->s;

    /* ---- classic RK4 stages */
    deriv k1 = derivative(b, &s0, f_world, tau_body);
    tw_body_state s1 = advance(&s0, &k1, 0.5 * dt);
    deriv k2 = derivative(b, &s1, f_world, tau_body);
    tw_body_state s2 = advance(&s0, &k2, 0.5 * dt);
    deriv k3 = derivative(b, &s2, f_world, tau_body);
    tw_body_state s3 = advance(&s0, &k3, dt);
    deriv k4 = derivative(b, &s3, f_world, tau_body);

    /* ---- weighted sum (1, 2, 2, 1) / 6 */
    deriv k;
    const double a = 1.0 / 6.0;
    k.dp = tw_scale(tw_add(tw_add(k1.dp, tw_scale(tw_add(k2.dp, k3.dp), 2.0)), k4.dp), a);
    k.dv = tw_scale(tw_add(tw_add(k1.dv, tw_scale(tw_add(k2.dv, k3.dv), 2.0)), k4.dv), a);
    k.dw = tw_scale(tw_add(tw_add(k1.dw, tw_scale(tw_add(k2.dw, k3.dw), 2.0)), k4.dw), a);
    k.dq.w = a * (k1.dq.w + 2.0 * (k2.dq.w + k3.dq.w) + k4.dq.w);
    k.dq.x = a * (k1.dq.x + 2.0 * (k2.dq.x + k3.dq.x) + k4.dq.x);
    k.dq.y = a * (k1.dq.y + 2.0 * (k2.dq.y + k3.dq.y) + k4.dq.y);
    k.dq.z = a * (k1.dq.z + 2.0 * (k2.dq.z + k3.dq.z) + k4.dq.z);

    tw_body_state next = advance(&s0, &k, dt);
    next.q = tw_q_normalize(next.q);

    /* ---- refuse a non-finite result: keep the last good state and report it */
    if (!tw_finite3(next.pos) || !tw_finite3(next.vel) || !tw_finite3(next.w)) return -2;

    b->last_accel_world = k.dv;
    b->s = next;

    /* ---- contact summary at the new state, for sensors and logs */
    tw_vec3 fc, tc;
    contact_wrench(b, &b->s, &fc, &tc, &b->n_touching, &b->contact_force_n);
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Invariants used by the tests
 * ------------------------------------------------------------------------------------------------------------------ */

double tw_body_kinetic_energy(const tw_body* b)
{
    if (!b) return 0.0;
    tw_vec3 Jw = tw_mat_mul_v(&b->p.inertia, b->s.w);
    return 0.5 * b->p.mass * tw_dot(b->s.vel, b->s.vel) + 0.5 * tw_dot(b->s.w, Jw);
}

tw_vec3 tw_body_angular_momentum_world(const tw_body* b)
{
    if (!b) return tw_v3(0, 0, 0);
    return tw_q_rotate(b->s.q, tw_mat_mul_v(&b->p.inertia, b->s.w));
}
