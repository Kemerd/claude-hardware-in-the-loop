/**
 * @file twin_main.c
 * @brief hil_twin: run one scenario at one seed against the twin and judge it.
 *
 * Usage:
 *     hil_twin <params.txt> <scenario.scn> [--seed N] [--out DIR] [--no-trace]
 *
 * Output (stdout), parsed by windows/ci/twin.ps1:
 *     [twin] <scenario> seed <n>: PASS|FAIL <metric>=<value> ...
 *     [twin] <scenario> seed <n> trace fnv1a64=<16 hex> steps=<n>
 * Files: <out>/<scenario>_s<seed>.csv (trace) and <out>/<scenario>_s<seed>.json (metrics, verdict, the checks).
 * Exit: 0 PASS, 1 FAIL, 2 bad arguments or a twin that could not be built, 3 the run itself broke.
 *
 * The runner drives the twin only through the C ABI (hil_twin_api.h), exactly as an engine host does: commands
 * through the link, telemetry decoded from the link, truth from hil_twin_get_scalar.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "hil_core/link.h"
#include "hil_twin/hil_twin_api.h"

/* ------------------------------------------------------------------------------------------------------------------
 *  Scenario file
 * ------------------------------------------------------------------------------------------------------------------ */

#define MAX_EVENTS 128
#define MAX_ACCEPT 32

/** @brief One timed event: a link command or an environment / fault value. */
typedef struct {
    double t;
    int is_set;          /**< 1 = hil_twin_set_scalar(name, value); 0 = hil_twin_send_command(name, value) */
    char name[48];
    double value;
    int done;
} event;

/** @brief One acceptance check: metric op limit. */
typedef struct {
    char metric[48];
    char op[3];
    double limit;
} accept;

/** @brief The scenario. */
typedef struct {
    char name[64];
    double duration;
    event ev[MAX_EVENTS];
    int n_ev;
    accept acc[MAX_ACCEPT];
    int n_acc;
} scenario;

/** @brief Parse a scenario file; prints the problem with file:line and returns -1 on any error. */
static int load_scenario(const char* path, scenario* s)
{
    memset(s, 0, sizeof(*s));
    FILE* f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "[twin] cannot read scenario %s\n", path);
        return -1;
    }
    char line[512];
    int ln = 0;
    while (fgets(line, sizeof line, f)) {
        ++ln;
        char* hash = strchr(line, '#');
        if (hash) *hash = 0;
        char a[64] = {0}, b[64] = {0}, c[64] = {0}, d[64] = {0};
        int n = sscanf(line, "%63s %63s %63s %63s", a, b, c, d);
        if (n <= 0) continue;
        if (strcmp(a, "name") == 0 && n >= 3 && strcmp(b, "=") == 0) {
            snprintf(s->name, sizeof s->name, "%s", c);
        } else if (strcmp(a, "duration_s") == 0 && n >= 3 && strcmp(b, "=") == 0) {
            s->duration = atof(c);
        } else if (strcmp(a, "at") == 0 && n >= 3) {
            if (s->n_ev >= MAX_EVENTS) { fprintf(stderr, "[twin] %s:%d: too many events\n", path, ln); fclose(f); return -1; }
            event* e = &s->ev[s->n_ev++];
            e->t = atof(b);
            if (strcmp(c, "set") == 0) {
                if (n < 4) { fprintf(stderr, "[twin] %s:%d: 'set' needs a name and a value\n", path, ln); fclose(f); return -1; }
                e->is_set = 1;
                snprintf(e->name, sizeof e->name, "%s", d);
                char* tail = strstr(line, d) + strlen(d);
                e->value = atof(tail);
            } else {
                snprintf(e->name, sizeof e->name, "%s", c);
                e->value = n >= 4 ? atof(d) : 0.0;
            }
        } else if (strcmp(a, "accept") == 0 && n == 4) {
            if (s->n_acc >= MAX_ACCEPT) { fprintf(stderr, "[twin] %s:%d: too many checks\n", path, ln); fclose(f); return -1; }
            accept* x = &s->acc[s->n_acc++];
            snprintf(x->metric, sizeof x->metric, "%s", b);
            snprintf(x->op, sizeof x->op, "%s", c);
            x->limit = atof(d);
            if (strcmp(c, "<") && strcmp(c, ">") && strcmp(c, "<=") && strcmp(c, ">=") && strcmp(c, "==")) {
                fprintf(stderr, "[twin] %s:%d: unknown operator %s\n", path, ln, c);
                fclose(f);
                return -1;
            }
        } else {
            fprintf(stderr, "[twin] %s:%d: cannot read '%s'\n", path, ln, a);
            fclose(f);
            return -1;
        }
    }
    fclose(f);
    if (!s->name[0] || !(s->duration > 0.0) || s->n_acc == 0) {
        fprintf(stderr, "[twin] %s: needs name, duration_s > 0 and at least one accept line\n", path);
        return -1;
    }
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Metrics: from telemetry (what a ground station sees) and plant truth (what a lab instrument would measure)
 * ------------------------------------------------------------------------------------------------------------------ */

typedef struct {
    double final_err_rad, max_t_winding_c, max_t_case_c, min_vbat_v, max_abs_i_a, final_abs_i_a, min_derate;
    double homed, fault, state, link_bad, telem_frames, max_t_battery_c;
} metrics;

/** @brief Value of a metric by name; returns 0 and sets *v, or -1 when unknown. */
static int metric_value(const metrics* m, const char* name, double* v)
{
    struct { const char* n; double v; } tab[] = {
        {"final_err_rad", m->final_err_rad}, {"max_t_winding_c", m->max_t_winding_c},
        {"max_t_case_c", m->max_t_case_c}, {"max_t_battery_c", m->max_t_battery_c}, {"min_vbat_v", m->min_vbat_v},
        {"max_abs_i_a", m->max_abs_i_a}, {"final_abs_i_a", m->final_abs_i_a}, {"min_derate", m->min_derate},
        {"homed", m->homed}, {"fault", m->fault}, {"state", m->state}, {"link_bad", m->link_bad},
        {"telem_frames", m->telem_frames}};
    for (size_t i = 0; i < sizeof tab / sizeof tab[0]; ++i)
        if (strcmp(tab[i].n, name) == 0) {
            *v = tab[i].v;
            return 0;
        }
    return -1;
}

/** @brief Apply an operator. */
static int check(double v, const char* op, double lim)
{
    if (!isfinite(v)) return 0;
    if (strcmp(op, "<") == 0) return v < lim;
    if (strcmp(op, ">") == 0) return v > lim;
    if (strcmp(op, "<=") == 0) return v <= lim;
    if (strcmp(op, ">=") == 0) return v >= lim;
    return fabs(v - lim) < 1e-9;
}

/** @brief Read a plant value (the names are part of the ABI; a missing one is a programming error). */
static double truth(const hil_twin* h, const char* name)
{
    double v = NAN;
    if (hil_twin_get_scalar(h, name, &v) != 0) fprintf(stderr, "[twin] %s\n", hil_twin_last_error());
    return v;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Main
 * ------------------------------------------------------------------------------------------------------------------ */

int main(int argc, char** argv)
{
    if (argc < 3) {
        fprintf(stderr, "usage: hil_twin <params.txt> <scenario.scn> [--seed N] [--out DIR] [--no-trace]\n");
        return 2;
    }
    const char* params = argv[1];
    const char* scn_path = argv[2];
    unsigned long long seed = 1;
    const char* out = ".";
    int want_trace = 1;
    for (int i = 3; i < argc; ++i) {
        if (strcmp(argv[i], "--seed") == 0 && i + 1 < argc) seed = strtoull(argv[++i], NULL, 10);
        else if (strcmp(argv[i], "--out") == 0 && i + 1 < argc) out = argv[++i];
        else if (strcmp(argv[i], "--no-trace") == 0) want_trace = 0;
        else {
            fprintf(stderr, "[twin] unknown argument %s\n", argv[i]);
            return 2;
        }
    }

    /* ---- ABI check first: a runner built against another header must not drive this library */
    if (hil_twin_abi_version() != HIL_TWIN_ABI_VERSION) {
        fprintf(stderr, "[twin] ABI mismatch: library %d, header %d\n", hil_twin_abi_version(), HIL_TWIN_ABI_VERSION);
        return 2;
    }
    scenario s;
    if (load_scenario(scn_path, &s) != 0) return 2;

    char trace_path[1024], json_path[1024];
    snprintf(trace_path, sizeof trace_path, "%s/%s_s%llu.csv", out, s.name, seed);
    snprintf(json_path, sizeof json_path, "%s/%s_s%llu.json", out, s.name, seed);
    hil_twin* h = hil_twin_create(params, (uint64_t)seed, want_trace ? trace_path : NULL);
    if (!h) {
        fprintf(stderr, "[twin] %s\n", hil_twin_last_error());
        return 2;
    }

    /* ---- the run: events at their time, one step at a time, telemetry decoded as it arrives */
    metrics m;
    memset(&m, 0, sizeof m);
    m.min_vbat_v = 1e9;
    m.min_derate = 1.0;
    m.state = -1;
    hil_link_rx_t rx;
    memset(&rx, 0, sizeof rx);
    double last_target = truth(h, "q_rad");
    const double dt = hil_twin_dt(h);
    const long n_steps = (long)llround(s.duration / dt);
    int broke = 0;
    for (long k = 0; k < n_steps && !broke; ++k) {
        const double t = k * dt;
        for (int i = 0; i < s.n_ev; ++i) {
            event* e = &s.ev[i];
            if (e->done || t + 1e-12 < e->t) continue;
            e->done = 1;
            int rc = e->is_set ? hil_twin_set_scalar(h, e->name, e->value) : hil_twin_send_command(h, e->name, e->value);
            if (rc != 0) {
                fprintf(stderr, "[twin] event at %.3f s: %s\n", e->t, hil_twin_last_error());
                broke = 1;
            }
            if (!e->is_set && strcmp(e->name, "set_pos") == 0) last_target = e->value;
        }
        if (hil_twin_step(h, 1) != 0) {
            fprintf(stderr, "[twin] step %ld: %s\n", k, hil_twin_last_error());
            broke = 1;
            break;
        }

        /* ---- telemetry: what the station sees */
        uint8_t buf[256];
        int n = hil_twin_link_rx(h, buf, (int)sizeof buf);
        for (int i = 0; i < n; ++i) {
            uint8_t type = 0, pl[HIL_LINK_MAX_PAYLOAD];
            int len = 0;
            int r = hil_link_feed(&rx, buf[i], &type, pl, &len);
            if (r < 0) m.link_bad += 1;
            if (r == 1 && type == HIL_MSG_TELEM) {
                hil_telem_t tm;
                if (hil_telem_unpack(pl, len, &tm) == 0) {
                    m.telem_frames += 1;
                    m.state = tm.state;
                    m.fault = tm.fault;
                    if (tm.state == 2) m.homed = 1;
                    if (tm.derate < m.min_derate) m.min_derate = tm.derate;
                } else {
                    m.link_bad += 1;
                }
            }
        }

        /* ---- truth: what a lab instrument would measure */
        const double i_a = truth(h, "i_motor_a");
        const double tw = truth(h, "t_winding_c"), tc = truth(h, "t_case_c"), tb = truth(h, "t_battery_c");
        const double vb = truth(h, "vbat_v");
        if (fabs(i_a) > m.max_abs_i_a) m.max_abs_i_a = fabs(i_a);
        if (tw > m.max_t_winding_c) m.max_t_winding_c = tw;
        if (tc > m.max_t_case_c) m.max_t_case_c = tc;
        if (tb > m.max_t_battery_c) m.max_t_battery_c = tb;
        if (vb < m.min_vbat_v) m.min_vbat_v = vb;
    }
    m.final_err_rad = fabs(truth(h, "q_rad") - last_target);
    m.final_abs_i_a = fabs(truth(h, "i_motor_a"));
    const unsigned long long hash = (unsigned long long)hil_twin_trace_hash(h);
    const double rows = truth(h, "steps");
    hil_twin_destroy(h);
    if (broke) {
        printf("[twin] %s seed %llu: FAIL run broke (see stderr)\n", s.name, seed);
        return 3;
    }

    /* ---- verdict */
    int pass = 1;
    char line[2048] = {0};
    size_t off = 0;
    FILE* js = fopen(json_path, "wb");
    if (js) fprintf(js, "{\n  \"scenario\": \"%s\",\n  \"seed\": %llu,\n  \"checks\": [\n", s.name, seed);
    for (int i = 0; i < s.n_acc; ++i) {
        double v = NAN;
        const int known = metric_value(&m, s.acc[i].metric, &v) == 0;
        const int ok = known && check(v, s.acc[i].op, s.acc[i].limit);
        pass &= ok;
        int w = snprintf(line + off, sizeof line - off, " %s=%.6g%s", s.acc[i].metric, v, ok ? "" : "(!)");
        if (w > 0 && off + (size_t)w < sizeof line) off += (size_t)w;
        if (js)
            fprintf(js, "    {\"metric\": \"%s\", \"value\": %.9g, \"op\": \"%s\", \"limit\": %.9g, \"pass\": %s}%s\n",
                    s.acc[i].metric, isfinite(v) ? v : -1e300, s.acc[i].op, s.acc[i].limit, ok ? "true" : "false",
                    i + 1 < s.n_acc ? "," : "");
        if (!known) fprintf(stderr, "[twin] unknown metric '%s'\n", s.acc[i].metric);
    }
    if (js) {
        fprintf(js, "  ],\n  \"trace_fnv1a64\": \"%016llx\",\n  \"passed\": %s\n}\n", hash, pass ? "true" : "false");
        fclose(js);
    }
    printf("[twin] %s seed %llu: %s%s\n", s.name, seed, pass ? "PASS" : "FAIL", line);
    printf("[twin] %s seed %llu trace fnv1a64=%016llx steps=%.0f\n", s.name, seed, hash, rows);
    return pass ? 0 : 1;
}
