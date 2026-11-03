/**
 * @file rigid_body.h
 * @brief 6-DOF rigid body with an RK4 integrator and a penalty ground contact.
 *
 * The body is described about its centre of mass (CoM): mass, inertia tensor about the CoM in body axes. Forces are
 * applied in the world frame at the CoM, torques in the body frame about the CoM. The equations are the Newton-Euler
 * equations with the gyroscopic term [REF-003]:
 *
 *     m dv/dt         = F_world + m g
 *     J dw/dt         = tau_body - w x (J w)
 *     dq/dt           = 0.5 q * (0, w)          (body rate, Hamilton, scalar first [REF-001])
 *
 * RK4 with the wrench held constant over the step, and the quaternion renormalised after the step. At the 1 kHz
 * step the twin uses, the energy drift of a free spinning body is far below anything the gates look at; the test
 * suite checks it (tests/test_twin_models.c, "torque-free spin").
 *
 * Ground contact: the plane z = 0, touched through a list of contact points fixed in the body (feet, skids, the
 * corners of a box). Each touching point gets a spring-damper normal force and a regularised Coulomb friction force
 * (a smooth tanh of the slip speed instead of a stick / slip switch), the penalty approach discussed in [REF-004].
 * Stiffness and damping come from the params; keep the contact frequency sqrt(k n / m) / (2 pi) at or below a
 * tenth of the step rate so RK4 stays well inside its stability region.
 */
#ifndef HIL_TWIN_RIGID_BODY_H
#define HIL_TWIN_RIGID_BODY_H

#include "hil_twin/twin_math.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Maximum number of body-fixed contact points. */
#define TW_MAX_CONTACTS 16

/** @brief Kinematic state of the body. */
typedef struct {
    tw_vec3 pos; /**< CoM position, world, m */
    tw_vec3 vel; /**< CoM velocity, world, m/s */
    tw_quat q;   /**< body -> world rotation */
    tw_vec3 w;   /**< angular velocity, BODY frame, rad/s */
} tw_body_state;

/** @brief Mass properties and contact geometry. */
typedef struct {
    double mass;                           /**< kg, > 0 */
    tw_mat3 inertia;                       /**< kg m^2 about the CoM, body axes, symmetric positive definite */
    tw_mat3 inertia_inv;                   /**< computed by tw_body_init */
    double gravity;                        /**< m/s^2, positive number (9.80665 standard) */
    int n_contacts;                        /**< 0..TW_MAX_CONTACTS */
    tw_vec3 contact_body[TW_MAX_CONTACTS]; /**< contact points relative to the CoM, body frame, m */
    double contact_k;                      /**< normal stiffness per point, N/m */
    double contact_c;                      /**< normal damping per point, N s/m */
    double contact_mu;                     /**< Coulomb friction coefficient */
    double contact_v_slip;                 /**< m/s; slip speed at which friction reaches tanh(1) of mu N */
} tw_body_params;

/** @brief The body: parameters plus state plus the last contact result (for sensors and logs). */
typedef struct {
    tw_body_params p;
    tw_body_state s;
    int ground_enabled;       /**< 1 = the twin owns contact (native backend); 0 = a host engine does */
    int n_touching;           /**< contact points touching after the last step */
    double contact_force_n;   /**< total normal force after the last step, N */
    tw_vec3 last_accel_world; /**< CoM acceleration of the last step (for the accelerometer), m/s^2 */
} tw_body;

/**
 * @brief Validate parameters and compute the inverse inertia.
 * @return 0 ok; -1 bad pointer, non-positive or non-finite mass, asymmetric or singular inertia, contact count out
 *         of range
 */
int tw_body_init(tw_body* b, const tw_body_params* p);

/**
 * @brief Advance one step with RK4.
 * @param b          body (state updated in place)
 * @param f_world    external force at the CoM, world frame, N (gravity and contact are added inside)
 * @param tau_body   external torque about the CoM, body frame, N m
 * @param dt         step, s (> 0, finite)
 * @return 0 ok; -1 bad input; -2 the state became non-finite (the state is left at the last finite value)
 */
int tw_body_step(tw_body* b, tw_vec3 f_world, tw_vec3 tau_body, double dt);

/** @brief Kinetic energy, J (translation + rotation). */
double tw_body_kinetic_energy(const tw_body* b);

/** @brief Angular momentum in the world frame, kg m^2/s. */
tw_vec3 tw_body_angular_momentum_world(const tw_body* b);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_RIGID_BODY_H */
