/**
 * @file actuators.h
 * @brief DC motor (electrical + torque), gearbox, and a single revolute joint carrying a link under gravity.
 *
 * DC motor, the averaged model of [REF-007] (a brushless motor with commutation done by the drive averages to the
 * same equations at the time scale of a 1 kHz loop):
 *
 *     L di/dt = V - R(T) i - ke w_m            V = duty * V_bus, duty -1..1 (an ideal averaged H-bridge)
 *     tau_m   = kt i                           kt = ke in SI units
 *     R(T)    = R_20 (1 + alpha_cu (T_winding - 20 degC))      alpha_cu = 0.00393 / K for copper
 *
 * The electrical equation is advanced with its exact exponential (w_m held over the step), so L / R far below the
 * step never destabilises it. The copper loss i^2 R(T) goes to the thermal network, which closes the loop: a hot
 * winding has more resistance and gives less torque at the same duty.
 *
 * Gearbox and joint (rigid transmission, ratio N, efficiency eta; textbook robot joint model [REF-015]):
 *
 *     J_eq qdd = eta N tau_m - b_out qd - tau_c sign_smooth(qd) - m g l_c cos(q) + tau_stop(q, qd) + tau_ext
 *     J_eq     = J_link + N^2 J_rotor          (reflected rotor inertia)
 *     w_m      = N qd
 *
 * q = 0 is the link horizontal, positive up. Hard stops are penalty springs past [q_min, q_max]. Coulomb friction
 * uses a tanh of qd / qd_slip so the model stays smooth and the integration step does not have to resolve a stick
 * / slip switch. Constants come from the datasheets of the motor and the gearhead, or from a bench run (a BENCH id).
 */
#ifndef HIL_TWIN_ACTUATORS_H
#define HIL_TWIN_ACTUATORS_H

#include "hil_twin/twin_math.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------------------------------------------------------
 *  DC motor
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Motor parameters (SI). */
typedef struct {
    double r20_ohm;   /**< terminal resistance at 20 degC, ohm, > 0 */
    double l_h;       /**< terminal inductance, H, >= 0 (0 = quasi-static current) */
    double kt;        /**< torque constant, N m / A (= back-EMF constant, V s / rad) */
    double j_rotor;   /**< rotor inertia, kg m^2, > 0 */
    double i_max_a;   /**< drive current limit, A, > 0 */
    double alpha_cu;  /**< resistance temperature coefficient, 1/K */
} tw_motor_params;

/** @brief Motor state and the outputs of the last step. */
typedef struct {
    tw_motor_params p;
    double i_a;         /**< winding current, A */
    double torque_nm;   /**< motor shaft torque, N m */
    double p_copper_w;  /**< copper loss, W */
    double i_bus_a;     /**< averaged current drawn from the bus, A (negative = regeneration) */
} tw_motor;

/** @brief Validate parameters and zero the state. @return 0 ok, -1 bad parameters */
int tw_motor_init(tw_motor* m, const tw_motor_params* p);

/**
 * @brief Advance the electrical state one step.
 * @param m            motor
 * @param duty         commanded duty -1..1 (clamped; NaN is treated as 0 and reported)
 * @param v_bus        bus voltage, V (>= 0)
 * @param t_winding_c  winding temperature, degC
 * @param w_shaft      motor shaft speed, rad/s
 * @param dt           step, s
 * @return 0 ok; 1 the duty was not finite and 0 was used; -1 bad arguments
 */
int tw_motor_step(tw_motor* m, double duty, double v_bus, double t_winding_c, double w_shaft, double dt);

/* ------------------------------------------------------------------------------------------------------------------
 *  Geared revolute joint with a link under gravity
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Joint parameters (SI). */
typedef struct {
    double ratio;          /**< N: motor turns per joint turn, > 0 */
    double efficiency;     /**< eta, 0 < eta <= 1 */
    double j_link;         /**< link inertia about the joint axis, kg m^2 (from the CAD export), > 0 */
    double m_link;         /**< link mass, kg (from the CAD export), >= 0 */
    double l_com;          /**< joint axis to link CoM, m, >= 0 */
    double gravity;        /**< m/s^2 */
    double b_out;          /**< viscous friction at the output, N m s / rad, >= 0 */
    double tau_coulomb;    /**< Coulomb friction at the output, N m, >= 0 */
    double qd_slip;        /**< rad/s; speed at which the friction reaches tanh(1) of tau_coulomb, > 0 */
    double q_min, q_max;   /**< hard stops, rad */
    double k_stop, c_stop; /**< stop stiffness N m / rad and damping N m s / rad */
} tw_joint_params;

/** @brief Joint state. */
typedef struct {
    tw_joint_params p;
    double q, qd, qdd;     /**< angle rad, rate rad/s, acceleration rad/s^2 */
    double tau_stop;       /**< stop torque of the last step, N m (logs, "hit the stop" checks) */
} tw_joint;

/** @brief Validate and set the initial angle. @return 0 ok, -1 invalid */
int tw_joint_init(tw_joint* j, const tw_joint_params* p, double q0);

/** @brief Equivalent inertia at the output (link + reflected rotor), kg m^2. */
double tw_joint_inertia(const tw_joint* j, double j_rotor);

/**
 * @brief Output torque of everything except inertia, for the host-physics mode: what the actuator applies to the
 *        joint (motor through the gearbox, friction, stops). Gravity and the link inertia are left to the host.
 */
double tw_joint_actuator_torque(const tw_joint* j, double tau_motor, double q, double qd);

/**
 * @brief Integrate one step (semi-implicit Euler: velocity first, then position).
 * @param tau_motor  motor shaft torque, N m
 * @param tau_ext    external torque at the output (a payload, a contact), N m
 * @param j_rotor    motor rotor inertia (for the reflected inertia)
 * @return 0 ok; -1 bad arguments; -2 the state became non-finite (kept at the last good value)
 */
int tw_joint_step(tw_joint* j, double tau_motor, double tau_ext, double j_rotor, double dt);


/* ------------------------------------------------------------------------------------------------------------------
 *  Brushless (PMSM / BLDC) motor in the rotor dq frame, with a field-oriented drive in torque mode
 *
 *  The standard dq model of a surface or interior permanent-magnet machine [REF-016]:
 *
 *     v_d = R i_d + L_d di_d/dt - w_e L_q i_q
 *     v_q = R i_q + L_q di_q/dt + w_e (L_d i_d + psi_m)
 *     tau = 1.5 p (psi_m i_q + (L_d - L_q) i_d i_q)          w_e = p w_m, p = pole pairs
 *
 *  The drive is what hobby and industrial FOC controllers do in torque (current) mode (SimpleFOC, ODrive, VESC,
 *  a vendor FOC chip): a PI current loop per axis at the drive rate with the voltage vector limited to the
 *  linear range of space-vector modulation, |v_dq| <= V_bus / sqrt(3). The model sub-steps that loop inside the
 *  twin step (n_sub sub-steps), each axis advanced with its exact exponential and the cross terms held over the
 *  sub-step. When your firmware runs the FOC itself (phase PWM, phase currents and the rotor angle through the
 *  HAL), step the twin at the PWM rate instead and feed the averaged phase voltages to the same equations.
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief PMSM + FOC drive parameters (SI). */
typedef struct {
    double r_ohm;          /**< phase resistance (line-to-line / 2 for a wye winding), ohm, > 0 */
    double ld_h, lq_h;     /**< d and q inductance, H, > 0 */
    double psi_m;          /**< magnet flux linkage, V s / rad (electrical); Kt = 1.5 p psi_m */
    int pole_pairs;        /**< p, >= 1 */
    double j_rotor;        /**< rotor inertia, kg m^2, > 0 */
    double alpha_cu;       /**< resistance temperature coefficient, 1/K */
    double i_max_a;        /**< drive current limit on |i_q|, A, > 0 */
    double kp_v_per_a;     /**< drive current loop proportional gain, V/A */
    double ki_v_per_as;    /**< drive current loop integral gain, V/(A s) */
    int n_sub;             /**< drive sub-steps per twin step, 1..200 */
} tw_pmsm_params;

/** @brief PMSM state and outputs. */
typedef struct {
    tw_pmsm_params p;
    double id, iq;         /**< A */
    double vd, vq;         /**< last applied voltages, V */
    double int_d, int_q;   /**< drive integrators, V */
    double torque_nm;      /**< shaft torque, N m */
    double p_copper_w;     /**< 1.5 R (id^2 + iq^2), W */
    double i_bus_a;        /**< averaged DC bus current, A (power balance 1.5 (vd id + vq iq) / V_bus) */
    int saturated;         /**< 1 when the voltage limit clipped the last sub-step */
} tw_pmsm;

/** @brief Validate and zero. @return 0 ok, -1 invalid */
int tw_pmsm_init(tw_pmsm* m, const tw_pmsm_params* p);

/**
 * @brief One twin step of drive + machine.
 * @param iq_cmd   torque-producing current command, A (clamped to +-i_max; NaN -> 0, reported)
 * @param id_cmd   field current command, A (0 for a surface PMSM below base speed)
 * @param v_bus    DC bus, V
 * @param t_winding_c winding temperature, degC
 * @param w_shaft  mechanical shaft speed, rad/s
 * @return 0 ok, 1 a command was not finite, -1 bad arguments
 */
int tw_pmsm_step(tw_pmsm* m, double iq_cmd, double id_cmd, double v_bus, double t_winding_c, double w_shaft,
                 double dt);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_ACTUATORS_H */
