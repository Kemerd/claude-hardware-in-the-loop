/**
 * @file twin_math.h
 * @brief Small vector / quaternion / matrix helpers for the native twin (C11, header-only, double precision).
 *
 * Conventions used everywhere in the twin (state them once, never mix them):
 *   - SI units: m, s, kg, N, N m, rad, A, V, W, K (temperatures in the API are degC for readability).
 *   - World frame: right-handed, Z up, origin on the floor. Gravity is (0, 0, -g).
 *   - Body frame: FLU (x forward, y left, z up), origin at the body reference point given in the params.
 *   - Quaternions are Hamilton, scalar first: q = (w, x, y, z), and rotate BODY -> WORLD: v_w = q * v_b * q^-1.
 *     Reference for the algebra and the integration: [REF-001] Sola, "Quaternion kinematics for the error-state
 *     Kalman filter", sections 2 and 4.
 *
 * Everything is inline and allocation-free so the physics loop never touches the heap.
 */
#ifndef HIL_TWIN_MATH_H
#define HIL_TWIN_MATH_H

#include <math.h>

#ifdef __cplusplus
extern "C" {
#endif

/** @brief A 3-vector of doubles. */
typedef struct { double x, y, z; } tw_vec3;

/** @brief A Hamilton quaternion, scalar first. */
typedef struct { double w, x, y, z; } tw_quat;

/** @brief A row-major 3x3 matrix (m[row][col]). */
typedef struct { double m[3][3]; } tw_mat3;

/* ------------------------------------------------------------------------------------------------------------------
 *  Vectors
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Build a vector. */
static inline tw_vec3 tw_v3(double x, double y, double z) { tw_vec3 r = {x, y, z}; return r; }
/** @brief a + b. */
static inline tw_vec3 tw_add(tw_vec3 a, tw_vec3 b) { return tw_v3(a.x + b.x, a.y + b.y, a.z + b.z); }
/** @brief a - b. */
static inline tw_vec3 tw_sub(tw_vec3 a, tw_vec3 b) { return tw_v3(a.x - b.x, a.y - b.y, a.z - b.z); }
/** @brief s * a. */
static inline tw_vec3 tw_scale(tw_vec3 a, double s) { return tw_v3(a.x * s, a.y * s, a.z * s); }
/** @brief Dot product. */
static inline double tw_dot(tw_vec3 a, tw_vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
/** @brief Cross product a x b. */
static inline tw_vec3 tw_cross(tw_vec3 a, tw_vec3 b)
{
    return tw_v3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x);
}
/** @brief Euclidean norm. */
static inline double tw_norm(tw_vec3 a) { return sqrt(tw_dot(a, a)); }
/** @brief True when all three components are finite (no NaN, no infinity). */
static inline int tw_finite3(tw_vec3 a) { return isfinite(a.x) && isfinite(a.y) && isfinite(a.z); }

/* ------------------------------------------------------------------------------------------------------------------
 *  Matrices
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief M * v. */
static inline tw_vec3 tw_mat_mul_v(const tw_mat3* M, tw_vec3 v)
{
    return tw_v3(M->m[0][0] * v.x + M->m[0][1] * v.y + M->m[0][2] * v.z,
                 M->m[1][0] * v.x + M->m[1][1] * v.y + M->m[1][2] * v.z,
                 M->m[2][0] * v.x + M->m[2][1] * v.y + M->m[2][2] * v.z);
}

/**
 * @brief Inverse of a 3x3 matrix by the adjugate.
 * @param A    matrix to invert
 * @param out  inverse (untouched on failure)
 * @return 0 on success, -1 when A is singular or not finite (|det| below 1e-18 of the scale)
 */
static inline int tw_mat_inv(const tw_mat3* A, tw_mat3* out)
{
    const double (*a)[3] = A->m;
    double c00 = a[1][1] * a[2][2] - a[1][2] * a[2][1];
    double c01 = a[1][2] * a[2][0] - a[1][0] * a[2][2];
    double c02 = a[1][0] * a[2][1] - a[1][1] * a[2][0];
    double det = a[0][0] * c00 + a[0][1] * c01 + a[0][2] * c02;
    double scale = fabs(a[0][0]) + fabs(a[1][1]) + fabs(a[2][2]);
    if (!isfinite(det) || !(fabs(det) > 1e-18 * scale * scale * scale) || !out) return -1;
    double id = 1.0 / det;
    out->m[0][0] = c00 * id;
    out->m[0][1] = (a[0][2] * a[2][1] - a[0][1] * a[2][2]) * id;
    out->m[0][2] = (a[0][1] * a[1][2] - a[0][2] * a[1][1]) * id;
    out->m[1][0] = c01 * id;
    out->m[1][1] = (a[0][0] * a[2][2] - a[0][2] * a[2][0]) * id;
    out->m[1][2] = (a[0][2] * a[1][0] - a[0][0] * a[1][2]) * id;
    out->m[2][0] = c02 * id;
    out->m[2][1] = (a[0][1] * a[2][0] - a[0][0] * a[2][1]) * id;
    out->m[2][2] = (a[0][0] * a[1][1] - a[0][1] * a[1][0]) * id;
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Quaternions
 * ------------------------------------------------------------------------------------------------------------------ */

/** @brief Identity rotation. */
static inline tw_quat tw_q_identity(void) { tw_quat q = {1.0, 0.0, 0.0, 0.0}; return q; }

/** @brief Hamilton product a * b. */
static inline tw_quat tw_q_mul(tw_quat a, tw_quat b)
{
    tw_quat r;
    r.w = a.w * b.w - a.x * b.x - a.y * b.y - a.z * b.z;
    r.x = a.w * b.x + a.x * b.w + a.y * b.z - a.z * b.y;
    r.y = a.w * b.y - a.x * b.z + a.y * b.w + a.z * b.x;
    r.z = a.w * b.z + a.x * b.y - a.y * b.x + a.z * b.w;
    return r;
}

/** @brief Conjugate (the inverse of a unit quaternion). */
static inline tw_quat tw_q_conj(tw_quat q) { tw_quat r = {q.w, -q.x, -q.y, -q.z}; return r; }

/**
 * @brief Normalise to unit length; a degenerate or non-finite input returns the identity.
 * @note The integrator calls this every step, so a NaN never propagates through the rotation.
 */
static inline tw_quat tw_q_normalize(tw_quat q)
{
    double n = sqrt(q.w * q.w + q.x * q.x + q.y * q.y + q.z * q.z);
    if (!isfinite(n) || n < 1e-12) return tw_q_identity();
    tw_quat r = {q.w / n, q.x / n, q.y / n, q.z / n};
    return r;
}

/** @brief Rotate a body vector into the world: q * v * q^-1 (expanded, no temporaries). */
static inline tw_vec3 tw_q_rotate(tw_quat q, tw_vec3 v)
{
    /* t = 2 * (q.xyz x v);  v' = v + w t + q.xyz x t */
    tw_vec3 u = tw_v3(q.x, q.y, q.z);
    tw_vec3 t = tw_scale(tw_cross(u, v), 2.0);
    return tw_add(tw_add(v, tw_scale(t, q.w)), tw_cross(u, t));
}

/** @brief Rotate a world vector into the body: q^-1 * v * q. */
static inline tw_vec3 tw_q_rotate_inv(tw_quat q, tw_vec3 v) { return tw_q_rotate(tw_q_conj(q), v); }

/** @brief Quaternion from an axis (any length > 0) and an angle in rad. */
static inline tw_quat tw_q_axis_angle(tw_vec3 axis, double angle)
{
    double n = tw_norm(axis);
    if (!(n > 1e-12) || !isfinite(angle)) return tw_q_identity();
    double s = sin(0.5 * angle) / n;
    tw_quat q = {cos(0.5 * angle), axis.x * s, axis.y * s, axis.z * s};
    return q;
}

/** @brief Tilt of the body z axis from world up, rad (0 = level). */
static inline double tw_q_tilt(tw_quat q)
{
    tw_vec3 zb = tw_q_rotate(q, tw_v3(0, 0, 1));
    double c = zb.z;
    if (c > 1.0) c = 1.0;
    if (c < -1.0) c = -1.0;
    return acos(c);
}

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_MATH_H */
