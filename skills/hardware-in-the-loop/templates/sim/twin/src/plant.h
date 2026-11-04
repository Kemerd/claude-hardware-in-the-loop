/**
 * @file plant.h
 * @brief The example plant: a single-joint actuator rig (motor -> gearbox -> link under gravity, hard stops) with
 *        its electronics (battery, current sense, NTC, encoder) and thermal network, running the REAL firmware core
 *        (firmware/core) through a hosted HAL.
 *
 * This is the file a project replaces with its own plant. Keep its shape:
 *   - everything the plant knows comes from the params file (no constants in code);
 *   - the firmware only sees raw sensor values through hal_t and only acts through hal_t;
 *   - each step: firmware tick, actuators, mechanics, electrical, thermal, trace, in that order;
 *   - two coupling modes: the twin integrates the mechanics (native), or a host engine does and the twin supplies
 *     the actuator torque (external: Unreal Chaos, Isaac PhysX).
 */
#ifndef HIL_TWIN_PLANT_H
#define HIL_TWIN_PLANT_H

#include <stdint.h>

#include "hil_core/ctrl.h"
#include "hil_twin/actuators.h"
#include "hil_twin/battery.h"
#include "hil_twin/params.h"
#include "hil_twin/sensors.h"
#include "hil_twin/thermal.h"
#include "hil_twin/trace.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Byte queue between the firmware and a host (one direction). */
typedef struct {
    uint8_t buf[4096];
    int head, tail;      /**< head = next write, tail = next read; empty when equal */
    uint32_t dropped;    /**< bytes lost to a full queue (a host that never reads) */
} tw_byteq;

/** @brief Motor technology of the rig. */
typedef enum { TW_MOTOR_DC = 0, TW_MOTOR_PMSM = 1 } tw_motor_kind;

/** @brief The plant. */
typedef struct {
    double dt, t;
    uint64_t seed;
    uint64_t steps;
    /* ---- mechanics and actuators */
    tw_motor_kind kind;
    tw_motor dc;
    tw_pmsm bl;
    tw_joint joint;
    double tau_motor;          /**< motor shaft torque of the last step */
    double i_quiescent_a;      /**< electronics draw from the battery */
    /* ---- electrical and thermal */
    tw_battery batt;
    tw_thermal th;
    int n_winding, n_case, n_batt; /**< thermal node indices */
    /* ---- sensors (TRUE hardware values, with per-unit tolerances drawn at create) */
    double enc_cpr;
    int32_t enc_offset;        /**< counts at q = 0: unknown to the firmware until it homes */
    double isense_v_per_a, isense_offset_v;
    double vbat_divider;
    tw_ntc_params ntc;
    tw_adc adc[HIL_ADC_COUNT];
    /* ---- environment and fault injection (set through hil_twin_set_scalar) */
    double ext_torque_nm;
    int fault_encoder, fault_ntc_open;
    /* ---- firmware */
    hil_core_t fw;
    double cmd;                /**< last motor_write value */
    uint32_t motor_writes;
    tw_byteq to_fw, from_fw;
    /* ---- coupling and logging */
    int external;              /**< 1 = a host engine integrates the joint */
    tw_trace trace;
} tw_plant;

/**
 * @brief Build the plant from a parameter set and boot the firmware.
 * @param pl        plant (filled)
 * @param p         parameters (every key the plant reads is marked used; tw_params_report_unused lists the rest)
 * @param seed      run seed: noise streams and the per-unit tolerance draws
 * @param trace     trace file or NULL (hash only)
 * @param err       message buffer
 * @return 0 ok, -1 a parameter is missing or invalid (err names it)
 */
int tw_plant_create(tw_plant* pl, tw_params* p, uint64_t seed, const char* trace, char* err, size_t errlen);

/** @brief Close the trace. */
void tw_plant_destroy(tw_plant* pl);

/**
 * @brief One step in native mode (the twin integrates the joint).
 * @return 0 ok, -1 a model refused its input (the run is invalid), -2 the state went non-finite
 */
int tw_plant_step(tw_plant* pl);

/**
 * @brief One step in external mode: the host integrated the joint and hands its state over; the twin runs the
 *        firmware, the actuator, the electrical and thermal models, and returns the actuator torque to apply.
 * @param q, qd     joint angle and rate measured by the host engine
 * @param tau_out   actuator torque at the output (motor through gearbox, friction, stops); the host adds gravity
 */
int tw_plant_step_external(tw_plant* pl, double q, double qd, double* tau_out);

/** @brief Queue helpers. */
int tw_byteq_push(tw_byteq* q, const uint8_t* d, int n);
int tw_byteq_pop(tw_byteq* q, uint8_t* out, int cap);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_PLANT_H */
