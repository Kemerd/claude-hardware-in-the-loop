/**
 * @file params.c
 * @brief "key = value" parameter loader with includes, duplicate detection and unused-key reporting.
 */
#include "hil_twin/params.h"

#include <ctype.h>
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------------------------------------------------------
 *  Storage: a flat array of entries (parameter files hold tens to a few hundred keys; a linear scan is fine and
 *  keeps the code obvious)
 * ------------------------------------------------------------------------------------------------------------------ */

#define KEY_MAX 96
#define VAL_MAX 512
#define ORIGIN_MAX 260
#define MAX_DEPTH 8

typedef struct {
    char key[KEY_MAX];
    char val[VAL_MAX];
    char origin[ORIGIN_MAX];
    int line;
    int used;
} entry;

struct tw_params {
    entry* e;
    int n, cap;
};

/** @brief printf into the error buffer when there is one. */
static void set_err(char* err, size_t errlen, const char* fmt, const char* a, int line, const char* b)
{
    if (err && errlen) snprintf(err, errlen, fmt, a ? a : "", line, b ? b : "");
}

/** @brief Find an entry by key, or NULL. */
static entry* find(const tw_params* p, const char* key)
{
    if (!p || !key) return NULL;
    for (int i = 0; i < p->n; ++i)
        if (strcmp(p->e[i].key, key) == 0) return &p->e[i];
    return NULL;
}

/** @brief Strip leading and trailing whitespace in place; returns the new start. */
static char* trim(char* s)
{
    while (*s && isspace((unsigned char)*s)) ++s;
    char* end = s + strlen(s);
    while (end > s && isspace((unsigned char)end[-1])) *--end = 0;
    return s;
}

/** @brief Directory part of a path (with the trailing separator), or "" when there is none. */
static void dir_of(const char* path, char* out, size_t cap)
{
    out[0] = 0;
    if (!path) return;
    const char* a = strrchr(path, '/');
    const char* b = strrchr(path, '\\');
    const char* s = a > b ? a : b;
    if (!s) return;
    size_t n = (size_t)(s - path) + 1;
    if (n >= cap) n = cap - 1;
    memcpy(out, path, n);
    out[n] = 0;
}

/** @brief Read a whole file into a new NUL-terminated buffer (caller frees), or NULL. */
static char* slurp(const char* path)
{
    FILE* f = fopen(path, "rb");
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    long sz = ftell(f);
    if (sz < 0 || sz > 16L * 1024 * 1024) { fclose(f); return NULL; }
    rewind(f);
    char* buf = (char*)malloc((size_t)sz + 1);
    if (!buf) { fclose(f); return NULL; }
    size_t got = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    buf[got] = 0;
    return buf;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Parsing
 * ------------------------------------------------------------------------------------------------------------------ */

static int parse_into(tw_params* p, const char* text, const char* origin, int depth, char* err, size_t errlen);

/** @brief Handle "include = <path>" relative to the including file. */
static int do_include(tw_params* p, const char* rel, const char* origin, int line, int depth, char* err,
                      size_t errlen)
{
    if (!origin) {
        set_err(err, errlen, "%s:%d: include not allowed in an in-memory text%s", "<text>", line, NULL);
        return -1;
    }
    if (depth >= MAX_DEPTH) {
        set_err(err, errlen, "%s:%d: includes nest deeper than 8 (a cycle?) at %s", origin, line, rel);
        return -1;
    }
    char path[ORIGIN_MAX * 2];
    /* ---- absolute paths stay as written; relative ones hang off the including file's directory */
    if (rel[0] == '/' || rel[0] == '\\' || (rel[0] && rel[1] == ':')) {
        snprintf(path, sizeof path, "%s", rel);
    } else {
        char dir[ORIGIN_MAX];
        dir_of(origin, dir, sizeof dir);
        snprintf(path, sizeof path, "%s%s", dir, rel);
    }
    char* text = slurp(path);
    if (!text) {
        set_err(err, errlen, "%s:%d: cannot read include %s", origin, line, path);
        return -1;
    }
    int rc = parse_into(p, text, path, depth + 1, err, errlen);
    free(text);
    return rc;
}

/** @brief Parse a text into the set. */
static int parse_into(tw_params* p, const char* text, const char* origin, int depth, char* err, size_t errlen)
{
    const char* name = origin ? origin : "<text>";
    int line_no = 0;
    const char* cur = text;
    while (*cur) {
        /* ---- one line, without its newline */
        const char* nl = strchr(cur, '\n');
        size_t len = nl ? (size_t)(nl - cur) : strlen(cur);
        ++line_no;
        char buf[KEY_MAX + VAL_MAX + 64];
        if (len >= sizeof buf) {
            set_err(err, errlen, "%s:%d: line too long%s", name, line_no, NULL);
            return -1;
        }
        memcpy(buf, cur, len);
        buf[len] = 0;
        cur = nl ? nl + 1 : cur + len;

        /* ---- comments and blank lines */
        char* hash = strchr(buf, '#');
        if (hash) *hash = 0;
        char* s = trim(buf);
        if (!*s) continue;

        /* ---- key = value */
        char* eq = strchr(s, '=');
        if (!eq) {
            set_err(err, errlen, "%s:%d: expected 'key = value', got '%s'", name, line_no, s);
            return -1;
        }
        *eq = 0;
        char* key = trim(s);
        char* val = trim(eq + 1);
        if (!*key || strlen(key) >= KEY_MAX || strlen(val) >= VAL_MAX) {
            set_err(err, errlen, "%s:%d: empty or oversized key / value%s", name, line_no, NULL);
            return -1;
        }
        if (strcmp(key, "include") == 0) {
            if (do_include(p, val, origin, line_no, depth, err, errlen) != 0) return -1;
            continue;
        }

        /* ---- duplicates are errors that name both places */
        entry* prev = find(p, key);
        if (prev) {
            char where[ORIGIN_MAX + 32];
            snprintf(where, sizeof where, "%s:%d", prev->origin, prev->line);
            set_err(err, errlen, "%s:%d: key defined twice, first at %s", name, line_no, where);
            return -1;
        }
        if (p->n == p->cap) {
            int nc = p->cap ? p->cap * 2 : 64;
            entry* ne = (entry*)realloc(p->e, sizeof(entry) * (size_t)nc);
            if (!ne) {
                set_err(err, errlen, "%s:%d: out of memory%s", name, line_no, NULL);
                return -1;
            }
            p->e = ne;
            p->cap = nc;
        }
        entry* e = &p->e[p->n++];
        snprintf(e->key, sizeof e->key, "%s", key);
        snprintf(e->val, sizeof e->val, "%s", val);
        snprintf(e->origin, sizeof e->origin, "%s", name);
        e->line = line_no;
        e->used = 0;
    }
    return 0;
}

/* ------------------------------------------------------------------------------------------------------------------
 *  Public API
 * ------------------------------------------------------------------------------------------------------------------ */

tw_params* tw_params_parse(const char* text, const char* origin, char* err, size_t errlen)
{
    if (!text) {
        set_err(err, errlen, "%s:%d: no text%s", "<text>", 0, NULL);
        return NULL;
    }
    tw_params* p = (tw_params*)calloc(1, sizeof(*p));
    if (!p) return NULL;
    if (parse_into(p, text, origin, 0, err, errlen) != 0) {
        tw_params_free(p);
        return NULL;
    }
    return p;
}

tw_params* tw_params_load(const char* path, char* err, size_t errlen)
{
    if (!path || !*path) {
        set_err(err, errlen, "%s:%d: empty params path%s", "<args>", 0, NULL);
        return NULL;
    }
    char* text = slurp(path);
    if (!text) {
        set_err(err, errlen, "%s:%d: cannot read the params file%s", path, 0, NULL);
        return NULL;
    }
    tw_params* p = tw_params_parse(text, path, err, errlen);
    free(text);
    return p;
}

void tw_params_free(tw_params* p)
{
    if (!p) return;
    free(p->e);
    free(p);
}

int tw_params_has(const tw_params* p, const char* key) { return find(p, key) != NULL; }

int tw_params_vec(tw_params* p, const char* key, double* out, int n)
{
    entry* e = find(p, key);
    if (!e) return -1;
    e->used = 1;
    if (!out || n < 1) return -2;
    /* ---- exactly n numbers separated by whitespace or commas, nothing else */
    const char* s = e->val;
    for (int i = 0; i < n; ++i) {
        while (*s && (isspace((unsigned char)*s) || *s == ',')) ++s;
        if (!*s) return -2;
        char* end = NULL;
        errno = 0;
        double v = strtod(s, &end);
        if (end == s || errno == ERANGE || !isfinite(v)) return -2;
        out[i] = v;
        s = end;
    }
    while (*s && (isspace((unsigned char)*s) || *s == ',')) ++s;
    return *s ? -2 : 0;
}

int tw_params_num(tw_params* p, const char* key, double* out) { return tw_params_vec(p, key, out, 1); }

int tw_params_num_or(tw_params* p, const char* key, double def, double* out)
{
    if (!out) return -2;
    int rc = tw_params_vec(p, key, out, 1);
    if (rc == -1) {
        *out = def;
        return 0;
    }
    return rc;
}

const char* tw_params_str(tw_params* p, const char* key)
{
    entry* e = find(p, key);
    if (!e) return NULL;
    e->used = 1;
    return e->val;
}

int tw_params_report_unused(const tw_params* p, char* buf, size_t buflen)
{
    if (!p) return 0;
    int count = 0;
    size_t off = 0;
    if (buf && buflen) buf[0] = 0;
    for (int i = 0; i < p->n; ++i) {
        if (p->e[i].used) continue;
        ++count;
        if (buf && off + 1 < buflen) {
            int w = snprintf(buf + off, buflen - off, "%s:%d %s\n", p->e[i].origin, p->e[i].line, p->e[i].key);
            if (w > 0) off += (size_t)w;
            if (off >= buflen) off = buflen - 1;
        }
    }
    return count;
}
