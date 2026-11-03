/**
 * @file sensors.h
 * @brief Sensor models that sit BELOW the firmware HAL: IMU, barometer, rangefinder, NTC thermistor and voltage
 *        divider through an ADC.
 *
 * Rule of the method: the twin produces what the sensor would put on its pins or in its registers (counts, codes,
 * raw readings with noise, bias, quantisation and saturation), and the firmware does every conversion it does on the
 * board. A model that hands the firmware a clean engineering value skips exactly the code that breaks in the field.
 *
 * Noise models
 *   IMU      white noise + bias random walk per axis, the continuous-time model of [REF-005] and [REF-006]:
 *              y = (1 + s) x + b + n,     n ~ N(0, sigma_d^2), sigma_d = N_d * sqrt(f_s)
 *              db/dt = w_b,               b[k+1] = b[k] + N_b * sqrt(dt) * N(0, 1)
 *            N_d is the datasheet noise density (rad/s/sqrt(Hz) or m/s^2/sqrt(Hz)); N_b the bias instability /
 *            random walk read off an Allan deviation plot [REF-005]; s a per-unit scale error drawn once.
 *            The output is quantised to the LSB and clipped at the full-scale range, like the real registers.
 *   Baro     ISA troposphere [REF-012]: p = p0 (1 - L h / T0)^(g M / (R L)), plus white noise in Pa.
 *   Range    one ray from the mount along the sensor axis to the floor plane, noise sigma = a + b * range,
 *            "no return" outside [min, max] or past the field-of-view tilt.
 *   NTC      beta model R(T) = R25 exp(B (1/T - 1/T25)) [REF-013] in a divider to an ADC with its bits, reference
 *            and white noise in LSB.
 */
#ifndef HIL_TWIN_SENSORS_H
#define HIL_TWIN_SENSORS_H

#include <stdint.h>

#include "hil_twin/rng.h"
#include "hil_twin/twin_math.h"

#ifdef __cplusplus
extern "C" {
#endif

/* ------------------------------------------------------------------------------------------------------------------
 *  IMU
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Datasheet-level parameters of one 3-axis channel (gyro or accelerometer). */
typedef struct {
    double noise_density;   /**< per sqrt(Hz): rad/s/sqrt(Hz) gyro, m/s^2/sqrt(Hz) accel */
    double bias_rw;         /**< bias random walk per sqrt(s) (rad/s/sqrt(s) or m/s^2/sqrt(s)) */
    double bias0_sigma;     /**< turn-on bias, 1 sigma, drawn once per unit */
    double scale_sigma;     /**< scale-factor error, 1 sigma (0.005 = 0.5 %), drawn once per unit */
    double range;           /**< full scale, +-, in rad/s or m/s^2 */
    double lsb;             /**< one count, in rad/s or m/s^2 */
} tw_imu_channel_params;

/** @brief IMU parameters. */
typedef struct {
    tw_imu_channel_params gyro, accel;
    tw_vec3 pos_body;       /**< sensor origin relative to the CoM, body frame, m (lever arm) */
} tw_imu_params;

/** @brief One IMU sample as the registers would hold it. */
typedef struct {
    int16_t gyro_counts[3];   /**< sensor axes = body axes (rotate in the plant if the part is mounted turned) */
    int16_t accel_counts[3];
} tw_imu_sample;

/** @brief IMU state: biases, unit draws, streams, the last angular velocity (for the lever-arm term). */
typedef struct {
    tw_imu_params p;
    tw_vec3 bias_g, bias_a;
    tw_vec3 scale_g, scale_a;
    tw_vec3 w_prev;
    int have_prev;
    tw_rng rng_g, rng_a;
} tw_imu;

/** @brief Validate, draw the per-unit errors, seed the streams. @return 0 ok, -1 invalid */
int tw_imu_init(tw_imu* m, const tw_imu_params* p, uint64_t seed);

/**
 * @brief Produce one sample from the true motion.
 * @param m            IMU
 * @param q            body -> world rotation
 * @param w_body       true body rate, rad/s
 * @param a_com_world  true CoM acceleration, world, m/s^2 (gravity NOT included)
 * @param gravity      m/s^2 (positive)
 * @param dt           sample period, s
 * @param out          sample
 * @return 0 ok, -1 bad arguments
 */
int tw_imu_sample_step(tw_imu* m, tw_quat q, tw_vec3 w_body, tw_vec3 a_com_world, double gravity, double dt,
                       tw_imu_sample* out);

/* ------------------------------------------------------------------------------------------------------------------
 *  Barometer
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Barometer parameters. */
typedef struct {
    double p0_pa;         /**< static pressure at world z = 0, Pa */
    double t0_k;          /**< air temperature at z = 0, K */
    double noise_pa;      /**< white noise, 1 sigma, Pa */
    double lsb_pa;        /**< resolution, Pa (> 0) */
} tw_baro_params;

/** @brief Barometer state. */
typedef struct { tw_baro_params p; tw_rng rng; } tw_baro;

/** @brief @return 0 ok, -1 invalid */
int tw_baro_init(tw_baro* m, const tw_baro_params* p, uint64_t seed);
/** @brief ISA pressure at height h (m above world z = 0), Pa, without noise. */
double tw_isa_pressure(double p0_pa, double t0_k, double h_m);
/** @brief Raw reading in counts of lsb_pa at the sensor height. */
int32_t tw_baro_sample(tw_baro* m, double h_m);

/* ------------------------------------------------------------------------------------------------------------------
 *  Rangefinder (single beam, floor plane z = 0)
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Rangefinder parameters. */
typedef struct {
    tw_vec3 pos_body;     /**< sensor origin relative to the CoM, body frame, m */
    tw_vec3 dir_body;     /**< beam direction, body frame (normalised at init) */
    double min_m, max_m;  /**< valid range */
    double max_incidence_rad; /**< beyond this angle between beam and floor normal there is no return */
    double sigma0_m;      /**< noise floor, m */
    double sigma_k;       /**< noise growth per metre of range */
    double lsb_m;         /**< output resolution, m (> 0) */
} tw_range_params;

/** @brief Rangefinder state. */
typedef struct { tw_range_params p; tw_rng rng; } tw_range;

/** @brief @return 0 ok, -1 invalid */
int tw_range_init(tw_range* m, const tw_range_params* p, uint64_t seed);
/**
 * @brief Measure against the floor plane.
 * @param out_counts range in counts of lsb_m (0 when there is no return)
 * @return 1 valid return, 0 no return, -1 bad arguments
 */
int tw_range_sample(tw_range* m, tw_vec3 pos_world, tw_quat q, uint16_t* out_counts);

/* ------------------------------------------------------------------------------------------------------------------
 *  ADC front ends
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief An ADC: bits, reference, noise. */
typedef struct {
    int bits;             /**< 8..24 */
    double vref_v;        /**< full-scale voltage */
    double noise_lsb;     /**< white noise, 1 sigma, in LSB */
} tw_adc_params;

/** @brief NTC in the low side of a divider from vref: V_adc = vref * R_ntc / (R_ntc + R_top). */
typedef struct {
    double r25_ohm;       /**< resistance at 25 degC */
    double beta_k;        /**< B constant, K (datasheet B25/85 or B25/50) */
    double r_top_ohm;     /**< divider top resistor */
} tw_ntc_params;

/** @brief An ADC channel with its stream. */
typedef struct { tw_adc_params p; tw_rng rng; } tw_adc;

/** @brief @return 0 ok, -1 invalid */
int tw_adc_init(tw_adc* a, const tw_adc_params* p, uint64_t seed, uint64_t stream);
/** @brief Convert a pin voltage to a code (noise, rounding, clipping at 0 and 2^bits - 1). */
uint32_t tw_adc_sample(tw_adc* a, double v_pin);
/** @brief Pin voltage of an NTC divider at a temperature (degC). */
double tw_ntc_divider_v(const tw_ntc_params* n, double vref_v, double t_c);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_SENSORS_H */
