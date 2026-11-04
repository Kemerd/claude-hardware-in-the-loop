/**
 * @file twin_api.c
 * @brief The exported C ABI over the plant (see hil_twin_api.h for the contract).
 */
#include "hil_twin/hil_twin_api.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hil_core/link.h"
#include "plant.h"

/* ------------------------------------------------------------------------------------------------------------------
 *  Per-thread last error
 * ------------------------------------------------------------------------------------------------------------------ */
#if defined(_MSC_VER) && !defined(__clang__)
#define TW_TLS __declspec(thread)
#else
#define TW_TLS _Thread_local
#endif

static TW_TLS char g_err[512];

/** @brief Record an error for this thread and return rc. */
static int set_error(int rc, const char* msg, const char* detail)
{
    snprintf(g_err, sizeof g_err, "%s%s%s", msg ? msg : "error", detail ? ": " : "", detail ? detail : "");
    return rc;
}

/** @brief The handle: a plant plus its parameters. */
struct hil_twin {
    uint32_t magic;
    tw_plant plant;
    tw_params* params;
};

#define TW_MAGIC 0x54574E31u /* "TWN1": catches a stale or foreign pointer */

/** @brief Validate a handle. */
static int ok_handle(const hil_twin* h) { return h && h->magic == TW_MAGIC; }

/* ------------------------------------------------------------------------------------------------------------------
 *  Lifetime
 * ------------------------------------------------------------------------------------------------------------------ */

HIL_TWIN_API int hil_twin_abi_version(void) { return HIL_TWIN_ABI_VERSION; }

HIL_TWIN_API const char* hil_twin_last_error(void) { return g_err; }

HIL_TWIN_API hil_twin* hil_twin_create(const char* params_path, uint64_t seed, const char* trace_path)
{
    g_err[0] = 0;
    if (!params_path || !*params_path) {
        set_error(-1, "hil_twin_create", "params_path is empty");
        return NULL;
    }
    char err[512] = {0};
    tw_params* p = tw_params_load(params_path, err, sizeof err);
    if (!p) {
        set_error(-1, "hil_twin_create", err);
        return NULL;
    }
    /* ---- the plant is large (byte queues, firmware state): heap, once, never in the loop */
    hil_twin* h = (hil_twin*)calloc(1, sizeof(*h));
    if (!h) {
        tw_params_free(p);
        set_error(-1, "hil_twin_create", "out of memory");
        return NULL;
    }
    if (tw_plant_create(&h->plant, p, seed, trace_path, err, sizeof err) != 0) {
        tw_params_free(p);
        free(h);
        set_error(-1, "hil_twin_create", err);
        return NULL;
    }
    /* ---- every key must have been read: an unread key is a typo or a model that is not wired */
    char unused[1024];
    int n_unused = tw_params_report_unused(p, unused, sizeof unused);
    if (n_unused > 0) {
        tw_plant_destroy(&h->plant);
        tw_params_free(p);
        free(h);
        set_error(-1, "hil_twin_create: parameters nobody reads (typo or unwired model)", unused);
        return NULL;
    }
    h->params = p;
    h->magic = TW_MAGIC;
    return h;
}

HIL_TWIN_API void hil_twin_destroy(hil_twin* h)
{
    if (!ok_handle(h)) return;
    tw_plant_destroy(&h->plant);
    tw_params_free(h->params);
    h->magic = 0;
    free(h);
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Stepping
 * ------------------------------------------------------------------------------------------------------------------ */

HIL_TWIN_API double hil_twin_dt(const hil_twin* h) { return ok_handle(h) ? h->plant.dt : 0.0; }

HIL_TWIN_API double hil_twin_time(const hil_twin* h) { return ok_handle(h) ? h->plant.t : 0.0; }

HIL_TWIN_API int hil_twin_step(hil_twin* h, int n)
{
    if (!ok_handle(h)) return set_error(-1, "hil_twin_step", "bad handle");
    if (n < 0 || n > 10000000) return set_error(-1, "hil_twin_step", "n out of range");
    for (int i = 0; i < n; ++i) {
        int rc = tw_plant_step(&h->plant);
        if (rc != 0) return set_error(rc, "hil_twin_step", rc == -2 ? "state became non-finite" : "a model refused its input");
    }
    return 0;
}

HIL_TWIN_API int hil_twin_step_external(hil_twin* h, const double* q, const double* qd, double* tau_out, int n_joints)
{
    if (!ok_handle(h)) return set_error(-1, "hil_twin_step_external", "bad handle");
    if (!q || !qd || !tau_out || n_joints != 1) return set_error(-1, "hil_twin_step_external", "bad arrays or joint count");
    if (!isfinite(q[0]) || !isfinite(qd[0])) return set_error(-1, "hil_twin_step_external", "non-finite joint state");
    int rc = tw_plant_step_external(&h->plant, q[0], qd[0], tau_out);
    return rc == 0 ? 0 : set_error(rc, "hil_twin_step_external", "a model refused its input");
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Joints
 * ------------------------------------------------------------------------------------------------------------------ */

HIL_TWIN_API int hil_twin_joint_count(const hil_twin* h) { return ok_handle(h) ? 1 : 0; }

HIL_TWIN_API int hil_twin_get_joint(const hil_twin* h, int i, double* q, double* qd, double* tau)
{
    if (!ok_handle(h) || i != 0) return set_error(-1, "hil_twin_get_joint", "bad handle or index");
    const tw_plant* pl = &h->plant;
    if (q) *q = pl->joint.q;
    if (qd) *qd = pl->joint.qd;
    if (tau) *tau = tw_joint_actuator_torque(&pl->joint, pl->tau_motor, pl->joint.q, pl->joint.qd);
    return 0;
}

HIL_TWIN_API int hil_twin_joint_props(const hil_twin* h, int i, double* out)
{
    if (!ok_handle(h) || i != 0 || !out) return set_error(-1, "hil_twin_joint_props", "bad handle, index or buffer");
    const tw_plant* pl = &h->plant;
    const double j_rotor = pl->kind == TW_MOTOR_DC ? pl->dc.p.j_rotor : pl->bl.p.j_rotor;
    out[0] = pl->joint.p.m_link;
    out[1] = pl->joint.p.l_com;
    out[2] = pl->joint.p.j_link;
    out[3] = pl->joint.p.ratio * pl->joint.p.ratio * j_rotor;
    out[4] = pl->joint.p.q_min;
    out[5] = pl->joint.p.q_max;
    out[6] = pl->joint.p.gravity;
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Link
 * ------------------------------------------------------------------------------------------------------------------ */

HIL_TWIN_API int hil_twin_link_tx(hil_twin* h, const uint8_t* bytes, int n)
{
    if (!ok_handle(h) || !bytes || n < 0) return set_error(-1, "hil_twin_link_tx", "bad arguments");
    return tw_byteq_push(&h->plant.to_fw, bytes, n);
}

HIL_TWIN_API int hil_twin_link_rx(hil_twin* h, uint8_t* out, int cap)
{
    if (!ok_handle(h) || !out || cap < 0) return set_error(-1, "hil_twin_link_rx", "bad arguments");
    return tw_byteq_pop(&h->plant.from_fw, out, cap);
}

HIL_TWIN_API int hil_twin_send_command(hil_twin* h, const char* name, double value)
{
    if (!ok_handle(h) || !name) return set_error(-1, "hil_twin_send_command", "bad arguments");
    uint8_t pl[HIL_LINK_MAX_PAYLOAD], frame[HIL_LINK_MAX_FRAME];
    int len = 0, type = 0;
    if (strcmp(name, "arm") == 0) {
        type = HIL_MSG_ARM;
        pl[0] = value != 0.0 ? 1 : 0;
        len = 1;
    } else if (strcmp(name, "set_pos") == 0) {
        if (!isfinite(value)) return set_error(-1, "hil_twin_send_command", "set_pos value not finite");
        type = HIL_MSG_SET_POS;
        hil_put_f32(pl, (float)value);
        len = 4;
    } else if (strcmp(name, "stop") == 0) {
        type = HIL_MSG_STOP;
    } else {
        return set_error(-1, "hil_twin_send_command: unknown command", name);
    }
    int n = hil_link_encode((uint8_t)type, pl, len, frame, (int)sizeof frame);
    if (n <= 0) return set_error(-1, "hil_twin_send_command", "encode failed");
    return tw_byteq_push(&h->plant.to_fw, frame, n) == n ? 0 : set_error(-1, "hil_twin_send_command", "link queue full");
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Named values
 * ------------------------------------------------------------------------------------------------------------------ */

HIL_TWIN_API int hil_twin_get_scalar(const hil_twin* h, const char* name, double* out)
{
    if (!ok_handle(h) || !name || !out) return set_error(-1, "hil_twin_get_scalar", "bad arguments");
    const tw_plant* pl = &h->plant;
    const double i = pl->kind == TW_MOTOR_DC ? pl->dc.i_a : pl->bl.iq;
    struct { const char* n; double v; } tab[] = {
        {"q_rad", pl->joint.q}, {"qd_rad_s", pl->joint.qd}, {"tau_motor_nm", pl->tau_motor}, {"i_motor_a", i},
        {"vbat_v", pl->batt.v_term}, {"soc", pl->batt.soc}, {"t_winding_c", pl->th.t_c[pl->n_winding]},
        {"t_case_c", pl->th.t_c[pl->n_case]}, {"t_battery_c", pl->th.t_c[pl->n_batt]}, {"ambient_c", pl->th.t_amb_c},
        {"steps", (double)pl->steps}, {"tau_stop_nm", pl->joint.tau_stop},
        {"fw.state", (double)pl->fw.state}, {"fw.fault", (double)pl->fw.fault}, {"fw.q_rad", pl->fw.q},
        {"fw.derate", pl->fw.derate}, {"fw.link_bad", (double)pl->fw.link_bad}};
    for (size_t k = 0; k < sizeof tab / sizeof tab[0]; ++k)
        if (strcmp(tab[k].n, name) == 0) {
            *out = tab[k].v;
            return 0;
        }
    return set_error(-1, "hil_twin_get_scalar: unknown name", name);
}

HIL_TWIN_API int hil_twin_set_scalar(hil_twin* h, const char* name, double value)
{
    if (!ok_handle(h) || !name || !isfinite(value)) return set_error(-1, "hil_twin_set_scalar", "bad arguments");
    tw_plant* pl = &h->plant;
    if (strcmp(name, "ambient_c") == 0) pl->th.t_amb_c = value;
    else if (strcmp(name, "soak_c") == 0) {
        /* ---- a thermal soak: ambient AND every node at the temperature (a rig left in a hot enclosure) */
        pl->th.t_amb_c = value;
        for (int i = 0; i < pl->th.n; ++i) pl->th.t_c[i] = value;
    }
    else if (strcmp(name, "ext_torque_nm") == 0) pl->ext_torque_nm = value;
    else if (strcmp(name, "fault.encoder") == 0) pl->fault_encoder = value != 0.0;
    else if (strcmp(name, "fault.ntc_open") == 0) pl->fault_ntc_open = value != 0.0;
    else return set_error(-1, "hil_twin_set_scalar: unknown name", name);
    return 0;
}

HIL_TWIN_API uint64_t hil_twin_trace_hash(const hil_twin* h) { return ok_handle(h) ? h->plant.trace.hash : 0; }
