/**
 * @file hil_twin_api.h
 * @brief The C ABI of the digital twin: the ONE interface every host uses (the native runner, the Unreal plugin,
 *        the Isaac Sim harness through ctypes, a Python notebook, a test).
 *
 * Why a C ABI and not C++ classes across the boundary: every engine and language can call it (Unreal C++, Python
 * ctypes, C#, Rust), and its layout does not depend on the compiler that built the host. Rules that keep it safe:
 *   - plain C types only; no struct crosses the boundary (arrays of double and byte buffers do);
 *   - every function checks its handle and pointers and returns a negative code instead of crashing;
 *     hil_twin_last_error() then holds a message for the calling thread;
 *   - handles are independent: two twins in one process share nothing (a seed sweep in threads is safe, one
 *     handle per thread);
 *   - additions are appended and bump HIL_TWIN_ABI_VERSION; a host checks the version and binds optional
 *     functions by name, so an old host keeps working with a new DLL.
 *
 * Units: SI, angles in rad, temperatures in degC.
 */
#ifndef HIL_TWIN_API_H
#define HIL_TWIN_API_H

#include <stdint.h>

#if defined(_WIN32)
#if defined(HIL_TWIN_BUILD_DLL)
#define HIL_TWIN_API __declspec(dllexport)
#elif defined(HIL_TWIN_STATIC)
#define HIL_TWIN_API
#else
#define HIL_TWIN_API __declspec(dllimport)
#endif
#else
#define HIL_TWIN_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/** @brief ABI version of this header. */
#define HIL_TWIN_ABI_VERSION 1

/** @brief Opaque twin handle. */
typedef struct hil_twin hil_twin;

/** @brief ABI version the library was built with (compare with HIL_TWIN_ABI_VERSION). */
HIL_TWIN_API int hil_twin_abi_version(void);

/** @brief Message of the last failing call on THIS thread ("" when none). Never NULL. */
HIL_TWIN_API const char* hil_twin_last_error(void);

/**
 * @brief Create a twin from a params file and boot its firmware.
 * @param params_path  the params file (includes resolve relative to it)
 * @param seed         run seed (noise streams and per-unit tolerances)
 * @param trace_path   CSV trace to write, or NULL for none (the hash is kept either way)
 * @return a handle, or NULL (see hil_twin_last_error)
 */
HIL_TWIN_API hil_twin* hil_twin_create(const char* params_path, uint64_t seed, const char* trace_path);

/** @brief Destroy a handle (NULL is fine). */
HIL_TWIN_API void hil_twin_destroy(hil_twin* h);

/** @brief Fixed step, s (the firmware tick). Returns 0 for a bad handle. */
HIL_TWIN_API double hil_twin_dt(const hil_twin* h);

/** @brief Simulated time, s. */
HIL_TWIN_API double hil_twin_time(const hil_twin* h);

/**
 * @brief Native mode: advance n steps; the twin integrates the mechanics.
 * @return 0 ok, < 0 error (the run is invalid from here on)
 */
HIL_TWIN_API int hil_twin_step(hil_twin* h, int n);

/**
 * @brief External mode: the host engine integrated the joints over the last step; hand over their state, get the
 *        actuator torques to apply over the next step. Gravity and link inertia are the host's; add the reflected
 *        rotor inertia from hil_twin_joint_props() to the host joint (Isaac: joint armature).
 * @param q, qd     n_joints angles and rates from the host
 * @param tau_out   n_joints actuator torques, N m
 * @return 0 ok, < 0 error
 */
HIL_TWIN_API int hil_twin_step_external(hil_twin* h, const double* q, const double* qd, double* tau_out, int n_joints);

/** @brief Number of joints (1 for the example rig). */
HIL_TWIN_API int hil_twin_joint_count(const hil_twin* h);

/** @brief State of joint i: angle, rate, actuator torque at the output. @return 0 ok */
HIL_TWIN_API int hil_twin_get_joint(const hil_twin* h, int i, double* q, double* qd, double* tau);

/**
 * @brief Properties of joint i for a host that builds its own physics body: link mass, CoM distance from the
 *        axis, link inertia about the axis, reflected rotor inertia (N^2 J_rotor), limits.
 * @param out  7 doubles: m_link, l_com, j_link, j_reflected, q_min, q_max, gravity
 */
HIL_TWIN_API int hil_twin_joint_props(const hil_twin* h, int i, double* out);

/** @brief Host -> firmware bytes (the command link). @return bytes accepted */
HIL_TWIN_API int hil_twin_link_tx(hil_twin* h, const uint8_t* bytes, int n);

/** @brief Firmware -> host bytes (telemetry). @return bytes copied (0 none) */
HIL_TWIN_API int hil_twin_link_rx(hil_twin* h, uint8_t* out, int cap);

/**
 * @brief Encode a command with the firmware's own codec and send it over the link (a convenience for hosts that do
 *        not carry the protocol): "arm" (value 1 / 0), "set_pos" (rad), "stop".
 * @return 0 ok, -1 unknown name or bad value
 */
HIL_TWIN_API int hil_twin_send_command(hil_twin* h, const char* name, double value);

/**
 * @brief Read a named value. Plant truth: "q_rad", "qd_rad_s", "tau_motor_nm", "i_motor_a", "vbat_v", "soc",
 *        "t_winding_c", "t_case_c", "t_battery_c", "ambient_c", "steps". Firmware debug view (diagnostics only;
 *        scenarios judge from telemetry and truth): "fw.state", "fw.fault", "fw.q_rad", "fw.derate".
 * @return 0 ok, -1 unknown name
 */
HIL_TWIN_API int hil_twin_get_scalar(const hil_twin* h, const char* name, double* out);

/**
 * @brief Set an environment value or inject a fault: "ambient_c", "soak_c" (ambient and every thermal node),
 *        "ext_torque_nm", "fault.encoder" (1 = the
 *        encoder stops answering), "fault.ntc_open" (1 = the NTC wire breaks).
 * @return 0 ok, -1 unknown name or non-finite value
 */
HIL_TWIN_API int hil_twin_set_scalar(hil_twin* h, const char* name, double value);

/** @brief FNV-1a 64 of every trace byte so far (the determinism fingerprint). */
HIL_TWIN_API uint64_t hil_twin_trace_hash(const hil_twin* h);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_API_H */
