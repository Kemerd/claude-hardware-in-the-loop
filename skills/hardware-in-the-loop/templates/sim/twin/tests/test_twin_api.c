/**
 * @file test_twin_api.c
 * @brief Host suite of the C ABI with the example plant: lifetime, refusals, determinism, independence of
 *        handles, the closed loop (homing, a move), the external (host physics) mode.
 *
 * Usage: test_twin_api <params dir, absolute> [<scratch dir>]   (CI passes the absolute sim/twin/params and the run
 *        folder; the typo check writes a file into the scratch dir that includes a params file by absolute path)
 * Prints exactly one "[hil] twin_api: N/N checks passed, 0 failed" line.
 */
#define HIL_CHECK_IMPLEMENTATION
#include "hil_check.h"

#include <math.h>
#include <stdio.h>
#include <string.h>

#include "hil_twin/hil_twin_api.h"

/** @brief Run a fixed command script on a fresh twin and return its trace hash (0 on failure). */
static uint64_t scripted_hash(const char* params, uint64_t seed)
{
    hil_twin* h = hil_twin_create(params, seed, NULL);
    if (!h) return 0;
    hil_twin_send_command(h, "arm", 1);
    hil_twin_step(h, 4000);
    hil_twin_send_command(h, "set_pos", 0.3);
    hil_twin_step(h, 2000);
    uint64_t hash = hil_twin_trace_hash(h);
    hil_twin_destroy(h);
    return hash;
}

/** @brief Home, move to a target, return the final angle error (or a large number on failure). */
static double home_and_move(const char* params, double target, double* state_out)
{
    hil_twin* h = hil_twin_create(params, 3, NULL);
    if (!h) {
        fprintf(stderr, "%s\n", hil_twin_last_error());
        return 1e9;
    }
    hil_twin_send_command(h, "arm", 1);
    hil_twin_step(h, 5000);
    hil_twin_send_command(h, "set_pos", target);
    hil_twin_step(h, 3000);
    double q = 0, st = 0;
    hil_twin_get_scalar(h, "q_rad", &q);
    hil_twin_get_scalar(h, "fw.state", &st);
    if (state_out) *state_out = st;
    hil_twin_destroy(h);
    return fabs(q - target);
}

int main(int argc, char** argv)
{
    const char* dir = argc > 1 ? argv[1] : "params";
    const char* scratch = argc > 2 ? argv[2] : ".";
    char dc[1024], bl[1024];
    snprintf(dc, sizeof dc, "%s/example_rig.txt", dir);
    snprintf(bl, sizeof bl, "%s/example_rig_bldc.txt", dir);

    /* ---- ABI and refusals: bad paths, bad handles and bad names give errors, never crashes */
    HIL_CHECK_EQ_INT(hil_twin_abi_version(), HIL_TWIN_ABI_VERSION);
    HIL_CHECK(hil_twin_create("does/not/exist.txt", 1, NULL) == NULL);
    HIL_CHECK(strlen(hil_twin_last_error()) > 0);
    HIL_CHECK_EQ_INT(hil_twin_step(NULL, 1), -1);
    HIL_CHECK_EQ_INT(hil_twin_send_command(NULL, "arm", 1), -1);
    hil_twin_destroy(NULL);

    /* ---- an unread key (a typo) is refused at create */
    char typo[1024];
    snprintf(typo, sizeof typo, "%s/tw_api_typo.txt", scratch);
    FILE* f = fopen(typo, "wb");
    HIL_CHECK(f != NULL);
    if (f) {
        fprintf(f, "include = %s\nmotor.dc.r20_ohms = 2.0\n", dc);
        fclose(f);
    }
    HIL_CHECK(hil_twin_create(typo, 1, NULL) == NULL);
    HIL_CHECK(strstr(hil_twin_last_error(), "r20_ohms") != NULL);
    remove(typo);

    /* ---- lifetime and names */
    hil_twin* h = hil_twin_create(dc, 1, NULL);
    HIL_CHECK(h != NULL);
    if (!h) {
        fprintf(stderr, "%s\n", hil_twin_last_error());
        return hil_check_summary("twin_api");
    }
    HIL_CHECK_NEAR(hil_twin_dt(h), 0.001, 0.0);
    HIL_CHECK_EQ_INT(hil_twin_joint_count(h), 1);
    double v = 0;
    HIL_CHECK_EQ_INT(hil_twin_get_scalar(h, "no.such.value", &v), -1);
    HIL_CHECK_EQ_INT(hil_twin_send_command(h, "launch", 1), -1);
    HIL_CHECK_EQ_INT(hil_twin_set_scalar(h, "ambient_c", NAN), -1);
    double props[7];
    HIL_CHECK_EQ_INT(hil_twin_joint_props(h, 0, props), 0);
    HIL_CHECK_NEAR(props[0], 0.3, 1e-12);
    HIL_CHECK_NEAR(props[3], 50.0 * 50.0 * 2.0e-6, 1e-12);

    /* ---- unarmed, the link hangs and the motor stays off; telemetry flows */
    HIL_CHECK_EQ_INT(hil_twin_step(h, 1000), 0);
    hil_twin_get_scalar(h, "q_rad", &v);
    HIL_CHECK_NEAR(v, -1.5708, 0.01);
    uint8_t buf[512];
    HIL_CHECK(hil_twin_link_rx(h, buf, (int)sizeof buf) > 0);
    hil_twin_destroy(h);

    /* ---- determinism: same seed same hash; another seed another hash; two live handles do not interfere */
    const uint64_t a = scripted_hash(dc, 7), b = scripted_hash(dc, 7), c = scripted_hash(dc, 8);
    HIL_CHECK(a != 0 && a == b);
    HIL_CHECK(a != c);
    hil_twin* h1 = hil_twin_create(dc, 7, NULL);
    hil_twin* h2 = hil_twin_create(dc, 7, NULL);
    HIL_CHECK(h1 && h2);
    if (h1 && h2) {
        hil_twin_send_command(h1, "arm", 1);
        hil_twin_send_command(h2, "arm", 1);
        for (int i = 0; i < 4000; ++i) {
            hil_twin_step(h1, 1);
            hil_twin_step(h2, 1);
        }
        hil_twin_send_command(h1, "set_pos", 0.3);
        hil_twin_send_command(h2, "set_pos", 0.3);
        hil_twin_step(h1, 2000);
        hil_twin_step(h2, 2000);
        HIL_CHECK(hil_twin_trace_hash(h1) == a);
        HIL_CHECK(hil_twin_trace_hash(h2) == a);
    }
    hil_twin_destroy(h1);
    hil_twin_destroy(h2);

    /* ---- closed loop, brushed and brushless: homes, then settles on the target */
    double st = 0;
    HIL_CHECK(home_and_move(dc, 0.6, &st) < 0.02);
    HIL_CHECK_NEAR(st, 2.0, 0.0);
    HIL_CHECK(home_and_move(bl, -0.4, &st) < 0.02);
    HIL_CHECK_NEAR(st, 2.0, 0.0);

    /* ---- external mode: the host owns the joint; at rest with the motor off the actuator torque is zero, and a
     *      host-integrated pendulum stays where gravity puts it */
    h = hil_twin_create(dc, 1, NULL);
    HIL_CHECK(h != NULL);
    if (h) {
        double q = -1.5708, qd = 0.0, tau = 1.0;
        HIL_CHECK_EQ_INT(hil_twin_step_external(h, &q, &qd, &tau, 1), 0);
        HIL_CHECK_NEAR(tau, 0.0, 1e-9);
        HIL_CHECK_EQ_INT(hil_twin_step_external(h, &q, &qd, &tau, 2), -1);
        hil_twin_destroy(h);
    }
    return hil_check_summary("twin_api");
}
