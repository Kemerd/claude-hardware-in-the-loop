/**
 * @file hal.h
 * @brief The hardware abstraction the portable control core talks through. One implementation per target
 *        (ESP-IDF on an ESP32 / ESP32-S3 / ESP32-P4, STM32 HAL or LL, Pico SDK, Zephyr, Arduino ...) and one for the
 *        host (the digital twin, sim/twin/src/plant.c).
 *
 * Why a table of function pointers and not link-time functions: the twin can then run several firmware instances in
 * one process (a seed sweep in threads, two robots in one scene), and a host test can swap one function for a fault
 * injector without relinking. On the target the table is a const struct in flash; the indirect call costs a few
 * cycles, nothing next to a bus transfer.
 *
 * Rules for every implementation:
 *   - functions return RAW values (encoder counts, ADC codes, frame bytes); the core converts them with the board
 *     constants in hil_core_board_t, exactly as on the target;
 *   - a read that fails returns a negative code and leaves *out untouched; the core handles it (a timeout is an
 *     error path with a test, never something that "cannot happen");
 *   - nothing here blocks longer than one bus transfer.
 */
#ifndef HIL_CORE_HAL_H
#define HIL_CORE_HAL_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief ADC channels of the example board. */
enum {
    HIL_ADC_VBAT = 0,    /**< battery through a divider */
    HIL_ADC_ISENSE = 1,  /**< motor current: shunt + amplifier, bidirectional around a mid-rail offset */
    HIL_ADC_NTC = 2,     /**< motor case NTC in the low side of a divider */
    HIL_ADC_COUNT = 3
};

/** @brief What the motor output command means on this board. */
typedef enum {
    HIL_DRIVE_DC_DUTY = 0,   /**< brushed DC through an H-bridge: cmd = duty -1..1 (TB6612, DRV8833, DRV8871, VNH5019) */
    HIL_DRIVE_FOC_IQ = 1     /**< brushless through a FOC drive in torque mode: cmd = iq in A (SimpleFOC, ODrive, VESC) */
} hil_drive_kind_t;

/** @brief The HAL table. Every pointer must be set; hil_core_init refuses a table with a NULL entry. */
typedef struct {
    void* ctx;                                               /**< passed back to every call */
    uint64_t (*time_us)(void* ctx);                          /**< monotonic microseconds */
    int (*encoder_read)(void* ctx, int32_t* counts);         /**< motor-side quadrature counts (x4), 0 ok */
    int (*adc_read)(void* ctx, int ch, uint32_t* code);      /**< HIL_ADC_*, 0 ok */
    int (*motor_write)(void* ctx, float cmd);                /**< meaning per hil_drive_kind_t, 0 ok */
    int (*link_recv)(void* ctx, uint8_t* buf, int cap);      /**< bytes received (0 none, < 0 error) */
    int (*link_send)(void* ctx, const uint8_t* buf, int len); /**< 0 ok */
} hal_t;

/**
 * @brief Board constants: what the firmware believes about its hardware (schematic, datasheets, CAD).
 * @note The twin keeps its OWN copy of the physical truth in its params (sensor and plant models). A mismatch
 *       between the two (a wrong divider, a wrong encoder resolution, a swapped motor polarity) is exactly the class
 *       of bug the twin exists to catch, so never derive one from the other in code.
 */
typedef struct {
    hil_drive_kind_t drive;
    float encoder_cpr;        /**< counts per motor revolution after x4 decoding */
    float gear_ratio;         /**< motor turns per joint turn */
    float gear_efficiency;    /**< 0..1, for the torque feed-forward */
    float kt_nm_per_a;        /**< motor torque constant (for FOC: 1.5 p psi_m) */
    float r_ohm;              /**< motor resistance at 20 degC (DC: back-EMF feed-forward) */
    int adc_bits;
    float adc_vref_v;
    float vbat_divider;       /**< V_bat / V_pin */
    float isense_v_per_a;     /**< shunt x amplifier gain, V/A */
    float isense_offset_v;    /**< output at 0 A (mid-rail for a bidirectional amplifier) */
    float ntc_r25_ohm, ntc_beta_k, ntc_r_top_ohm;
    float gravity_nm;         /**< m g l_c of the link from the CAD export: the gravity feed-forward at q = 0 */
    float i_max_a;            /**< firmware current limit (below the drive's own hardware limit) */
    float winding_rth_k_per_w; /**< winding-to-case thermal resistance (motor datasheet), 0 = no winding observer */
    float winding_tau_s;      /**< winding thermal time constant (motor datasheet), s */
    float q_home_rad;         /**< joint angle at the homing stop (from the CAD) */
    float q_soft_min_rad;     /**< commanded targets are clamped to [q_soft_min, q_soft_max] */
    float q_soft_max_rad;
} hil_core_board_t;

#ifdef __cplusplus
}
#endif

#endif /* HIL_CORE_HAL_H */
