/**
 * @file thermal.h
 * @brief Lumped-capacitance thermal network: N nodes, conductances between nodes and to ambient, heat inputs.
 *
 * Each node is a lump at one temperature (a motor winding, a motor case, a regulator junction, a board, a battery
 * pack, the enclosure air). The model is the lumped-capacitance method of [REF-008] chapter 5, generalised to a
 * network (the thermal analogue of an RC circuit: K = A, W = V, J/K = F):
 *
 *     C_i dT_i/dt = P_i - G_amb,i (T_i - T_amb) - sum_j G_ij (T_i - T_j)
 *
 * Integrated with backward (implicit) Euler: unconditionally stable for any step, so a tiny capacitance (a
 * junction) next to a huge one (an enclosure) never forces a small step. The linear system is at most
 * TW_THERMAL_MAX_NODES wide and is solved in place with partial pivoting every step.
 *
 * Where the numbers come from: junction-to-case and junction-to-ambient resistances from the part datasheet
 * (measured to the JEDEC JESD51 conditions [REF-009]; note the board those figures assume), heat capacities from
 * mass x specific heat, convection conductances h A with h from a correlation or a bench run (a BENCH id).
 * The lumped method holds while the Biot number h L / k of each lump stays well below 0.1 [REF-008].
 */
#ifndef HIL_TWIN_THERMAL_H
#define HIL_TWIN_THERMAL_H

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Maximum number of nodes. */
#define TW_THERMAL_MAX_NODES 16
/** @brief Maximum number of node-to-node links. */
#define TW_THERMAL_MAX_LINKS 32

/** @brief A conductance between two nodes. */
typedef struct {
    int a, b;    /**< node indices, distinct, 0..n-1 */
    double g;    /**< W/K, >= 0 */
} tw_thermal_link;

/** @brief The network. */
typedef struct {
    int n;                                      /**< node count, 1..TW_THERMAL_MAX_NODES */
    double c[TW_THERMAL_MAX_NODES];             /**< heat capacity, J/K, > 0 */
    double g_amb[TW_THERMAL_MAX_NODES];         /**< conductance to ambient, W/K, >= 0 */
    double t_c[TW_THERMAL_MAX_NODES];           /**< temperature, degC (state) */
    double p_w[TW_THERMAL_MAX_NODES];           /**< heat input this step, W (set by the plant before each step) */
    int n_links;
    tw_thermal_link link[TW_THERMAL_MAX_LINKS];
    double t_amb_c;                             /**< ambient, degC */
} tw_thermal;

/**
 * @brief Validate the network (capacities positive, links in range, no self link, every node connected to
 *        ambient through some path so a steady state exists).
 * @return 0 ok, -1 invalid
 */
int tw_thermal_validate(const tw_thermal* th);

/**
 * @brief One backward-Euler step with the present p_w inputs.
 * @return 0 ok; -1 invalid network or step; -2 the linear system was singular (state unchanged)
 */
int tw_thermal_step(tw_thermal* th, double dt);

/**
 * @brief Steady-state temperatures for the present p_w and ambient (solves G T = P + G_amb T_amb).
 * @param th     network
 * @param out_c  n temperatures, degC
 * @return 0 ok, -1 invalid, -2 singular (a node with no path to ambient)
 */
int tw_thermal_steady(const tw_thermal* th, double* out_c);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_THERMAL_H */
