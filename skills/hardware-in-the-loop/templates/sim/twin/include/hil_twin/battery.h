/**
 * @file battery.h
 * @brief Battery pack as a first-order Thevenin equivalent circuit with an OCV(SOC) table.
 *
 *     V_term = n_series * OCV(SOC) - I R0 - V1           (I > 0 discharging)
 *     dV1/dt = -V1 / (R1 C1) + I / C1                     (one RC pair: the diffusion / polarisation voltage)
 *     dSOC/dt = -I / (3600 Q_Ah)
 *     heat   = I^2 R0 + V1^2 / R1                         (to the pack node of the thermal network)
 *
 * The 1RC Thevenin model is the usual compromise between accuracy and parameter count; [REF-010] chapter 2 derives
 * it and [REF-011] compares it against richer circuits on real cells. R0, R1 and C1 come from a pulse test on the
 * real pack (a BENCH id) or from the cell datasheet's DC resistance; the OCV table from the cell datasheet or a slow
 * (C/20) discharge. The RC voltage is advanced with its exact exponential, so any step size is stable.
 * R0, R1, C1 are per pack (already scaled for series and parallel cells); the OCV table is per cell.
 */
#ifndef HIL_TWIN_BATTERY_H
#define HIL_TWIN_BATTERY_H

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Maximum OCV table points. */
#define TW_BATT_MAX_OCV 16

/** @brief Pack parameters. */
typedef struct {
    double capacity_ah;               /**< usable capacity, Ah, > 0 */
    int n_series;                     /**< cells in series, >= 1 */
    double r0_ohm;                    /**< ohmic resistance of the pack, ohm, >= 0 */
    double r1_ohm;                    /**< polarisation resistance, ohm, > 0 */
    double c1_f;                      /**< polarisation capacitance, F, > 0 */
    int n_ocv;                        /**< table points, 2..TW_BATT_MAX_OCV */
    double ocv_soc[TW_BATT_MAX_OCV];  /**< SOC 0..1, strictly increasing */
    double ocv_v[TW_BATT_MAX_OCV];    /**< cell open-circuit voltage at that SOC, V */
} tw_battery_params;

/** @brief Pack state. */
typedef struct {
    tw_battery_params p;
    double soc;        /**< 0..1 */
    double v1;         /**< RC voltage, V */
    double v_term;     /**< terminal voltage after the last step, V */
    double heat_w;     /**< heat of the last step, W */
} tw_battery;

/** @brief Validate and set the initial SOC (0..1). @return 0 ok, -1 invalid */
int tw_battery_init(tw_battery* b, const tw_battery_params* p, double soc0);

/** @brief Cell OCV at a SOC (linear interpolation, clamped to the table ends). */
double tw_battery_ocv_cell(const tw_battery_params* p, double soc);

/**
 * @brief Advance one step at a load current.
 * @param i_a  pack current, A (> 0 discharge)
 * @return 0 ok; 1 the pack is empty (SOC clamped at 0); -1 bad arguments
 */
int tw_battery_step(tw_battery* b, double i_a, double dt);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_BATTERY_H */
