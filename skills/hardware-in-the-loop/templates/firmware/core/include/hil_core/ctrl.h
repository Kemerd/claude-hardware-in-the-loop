/**
 * @file ctrl.h
 * @brief Example portable control core for a single robot joint: homing against a hard stop, a cascaded
 *        position / velocity / current loop with gravity feed-forward, thermal derating from a motor NTC, and
 *        battery and stall faults.
 *
 * It is an EXAMPLE of the shape a firmware core should have, written to run unchanged on an MCU and in the twin:
 *   - C11, float math, no heap, no stdio, no vendor headers; hardware only through hal_t;
 *   - one entry point, hil_core_step(), called at a fixed rate (1 kHz) by a FreeRTOS task, a timer ISR or a
 *     superloop on the target and by the twin's lockstep loop on the host;
 *   - every read checked; a failing sensor is a fault state with a defined reaction (motor off), never a crash.
 * Replace the plant-specific parts (the cascade, the homing, the limits) with your product's; keep the shape.
 *
 * Thermal protection: the NTC sits on the motor CASE, but the winding is what burns. The core estimates the
 * winding rise over the case with a first-order observer (copper loss through the datasheet winding-to-case
 * thermal resistance and time constant, the lumped model of [REF-008]) and derates on the hotter of the two.
 *
 * The cascade is the classic servo structure of [REF-015] chapter 8: P on position, PI on velocity, PI on current
 * (brushed DC) or a current command to a FOC drive (brushless), with the gravity torque fed forward.
 */
#ifndef HIL_CORE_CTRL_H
#define HIL_CORE_CTRL_H

#include <stdint.h>

#include "hil_core/hal.h"
#include "hil_core/link.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Top-level state. */
typedef enum {
    HIL_ST_IDLE = 0,       /**< motor off, waiting for ARM */
    HIL_ST_HOMING = 1,     /**< moving slowly onto the homing stop */
    HIL_ST_RUN = 2,        /**< servoing to the commanded position */
    HIL_ST_FAULT = 3       /**< motor off until DISARM then ARM */
} hil_core_state_t;

/** @brief Fault bits (telemetry). */
enum {
    HIL_FAULT_ENCODER = 1u << 0,   /**< encoder read failed */
    HIL_FAULT_ADC = 1u << 1,
    HIL_FAULT_OVERTEMP = 1u << 2,
    HIL_FAULT_LOW_BATT = 1u << 3,
    HIL_FAULT_DRIVE = 1u << 4,     /**< motor_write failed */
    HIL_FAULT_HOMING = 1u << 5,    /**< no stop found within the homing timeout */
    HIL_FAULT_STALL = 1u << 6      /**< full (un-derated) current, no motion, far from the setpoint: jam or slipped encoder */
};

/** @brief Tunables. Defaults from hil_core_default_tuning(); the comments give the units. */
typedef struct {
    float pos_kp;               /**< (rad/s) per rad */
    float vel_kp, vel_ki;       /**< N m per (rad/s), N m per rad */
    float cur_kp, cur_ki;       /**< duty per A, duty per (A s) (brushed DC only) */
    float vel_max, acc_max;     /**< setpoint limits, rad/s and rad/s^2 */
    float vel_filter_hz;        /**< velocity estimate low-pass corner */
    float home_speed;           /**< rad/s towards the stop (negative = towards q_min) */
    float home_current_a;       /**< current limit while homing */
    float home_timeout_s;
    float t_derate_start_c, t_derate_end_c, derate_min, t_trip_c;
    float v_min;                /**< pack voltage below which the core faults, V */
    float stall_time_s;         /**< full current with no derating, no motion, for this long = stall fault */
} hil_core_tuning_t;

/** @brief The core's whole state (one instance per firmware image; no globals). */
typedef struct {
    hal_t hal;
    hil_core_board_t board;
    hil_core_tuning_t tun;
    hil_core_state_t state;
    uint8_t fault;
    /* ---- measurement */
    int32_t enc_prev;
    int enc_have;
    float q_offset;            /**< joint angle = counts * scale + q_offset (set by homing) */
    float q, qd;               /**< joint angle rad, filtered rate rad/s */
    float i_meas, vbat, t_motor_c;
    float dt_winding;          /**< winding rise over the case from the thermal observer, K */
    /* ---- control */
    float q_target;            /**< commanded by the link */
    float q_sp, qd_sp;         /**< shaped setpoint */
    float vel_int, cur_int;
    float i_cmd, cmd_out, derate;
    float home_t, home_still_t, stall_t;
    /* ---- link */
    hil_link_rx_t rx;
    uint32_t tick;
    uint32_t link_frames, link_bad;
} hil_core_t;

/** @brief Defaults suited to the example rig in sim/twin/params/example_rig.txt. */
hil_core_tuning_t hil_core_default_tuning(void);

/**
 * @brief Initialise. Refuses a HAL table with any NULL entry and non-physical board constants.
 * @return 0 ok, -1 invalid
 */
int hil_core_init(hil_core_t* c, const hal_t* hal, const hil_core_board_t* board, const hil_core_tuning_t* tun);

/**
 * @brief One control tick; call at exactly 1 kHz.
 * @return 0 ok, < 0 a HAL call failed this tick (already handled: fault bits set, motor off)
 */
int hil_core_step(hil_core_t* c);

/** @brief Control period the core is written for, s. */
#define HIL_CORE_DT 0.001f

#ifdef __cplusplus
}
#endif

#endif /* HIL_CORE_CTRL_H */
