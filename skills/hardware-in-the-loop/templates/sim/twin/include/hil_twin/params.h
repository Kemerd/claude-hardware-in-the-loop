/**
 * @file params.h
 * @brief The twin's parameter file: "key = value" lines, '#' comments, numbers and number lists, includes.
 *
 * Format (one file or several):
 *
 *     # mass properties come from the CAD export (tools/cad_to_twin.py); never edit by hand
 *     include = ../out/mass.txt
 *     motor.0.r20_ohm = 0.10          # [REF-007] datasheet phase resistance x 1.5 (wye, line to line / 2 ...)
 *     imu.gyro.noise_density = 4.9e-5 # rad/s/sqrt(Hz), datasheet table 1
 *     contact.point.0 = 0.10 0.10 -0.05
 *
 * Rules the loader enforces, because each one has cost a day somewhere:
 *   - a key defined twice is an error that names both places (no silent "last one wins");
 *   - a value that does not parse completely as the requested numbers is an error (no "12abc" -> 12);
 *   - after the plant has read what it needs, tw_params_report_unused lists every key nobody read: a typo in a key
 *     name otherwise leaves the model on its default without a word;
 *   - includes resolve relative to the including file, nest at most 8 deep, and a cycle is an error.
 * Loading allocates once; reads never allocate, so nothing here runs in the physics loop.
 */
#ifndef HIL_TWIN_PARAMS_H
#define HIL_TWIN_PARAMS_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Opaque parameter set. */
typedef struct tw_params tw_params;

/**
 * @brief Load a file (and its includes).
 * @param path    the top file
 * @param err     buffer for a message naming file and line on failure (may be NULL)
 * @param errlen  size of err
 * @return a new set, or NULL on failure
 */
tw_params* tw_params_load(const char* path, char* err, size_t errlen);

/**
 * @brief Build a set from a text in memory (tests, hosts that embed their parameters).
 * @param text    the file contents
 * @param origin  a name used in messages, and the base for relative includes (may be NULL: includes refused)
 */
tw_params* tw_params_parse(const char* text, const char* origin, char* err, size_t errlen);

/** @brief Free a set (NULL is fine). */
void tw_params_free(tw_params* p);

/** @brief True when the key exists (does not mark it used). */
int tw_params_has(const tw_params* p, const char* key);

/**
 * @brief Read n numbers.
 * @return 0 ok; -1 missing; -2 present but not exactly n finite numbers
 */
int tw_params_vec(tw_params* p, const char* key, double* out, int n);

/** @brief Read one number. Same return codes as tw_params_vec. */
int tw_params_num(tw_params* p, const char* key, double* out);

/** @brief Read one number, or use the default when the key is missing (a malformed value is still -2). */
int tw_params_num_or(tw_params* p, const char* key, double def, double* out);

/** @brief Raw string value or NULL (marks the key used). */
const char* tw_params_str(tw_params* p, const char* key);

/**
 * @brief Write every key that was never read into buf, one "file:line key" per line.
 * @return number of unused keys
 */
int tw_params_report_unused(const tw_params* p, char* buf, size_t buflen);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_PARAMS_H */
