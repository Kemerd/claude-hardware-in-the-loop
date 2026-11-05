/**
 * @file astar.c
 * @brief Grid A* core of the negotiated PCB router (pf_route.py), compiled to a shared library and called through ctypes.
 *
 * 8-way moves on each routable layer, a via (layer change) where the via mask allows it, an optional soft "halo" surcharge
 * per step, octile heuristic toward one target cell (or none: uniform cost, nearest goal first).
 *
 * Why C: the router builds its rule masks in numpy, which is fast, but a search in Python spends a minute on every connection
 * that exhausts a few million cells, and a negotiation runs thousands of searches. Only the search lives here.
 *
 * Build: pf_route.py compiles this file on first use, into a cache folder, under a name that holds a hash of the source.
 *        By hand: clang -O2 -shared -o astar.dll astar.c       (Windows, clang or MinGW gcc)
 *                 cc -O2 -shared -fPIC -o libastar.so astar.c  (Linux)
 *                 cc -O2 -dynamiclib -o libastar.dylib astar.c (macOS)
 *                 cl /O2 /LD astar.c /Fe:astar.dll             (Windows, MSVC)
 *
 * Memory layout: every mask is a C-contiguous uint8 array [nL][H][W]; a cell id is (l * H + i) * W + j.
 * Threading: no global state, one call owns its buffers, so calls from several threads do not interact.
 */
#include <math.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* Symbol export: the one thing that differs between the toolchains. */
#ifdef _WIN32
#define GRID_ASTAR_API __declspec(dllexport)
#else
#define GRID_ASTAR_API __attribute__((visibility("default")))
#endif

/** One open-list entry: priority f, path cost g, cell id, insertion order. */
typedef struct {
    double f;
    double g;
    int64_t id;
    int64_t seq;
} node_t;

/** Binary min-heap on (f, g). */
typedef struct {
    node_t *a;
    int64_t n, cap;
} heap_t;

/**
 * @brief Push a node; grows the heap storage geometrically.
 * @return 0 on success, -1 when memory runs out (the caller aborts the search).
 */
static int heap_push(heap_t *h, node_t v)
{
    if (h->n == h->cap) {
        int64_t nc = h->cap ? h->cap * 2 : 1 << 16;
        node_t *na = (node_t *)realloc(h->a, (size_t)nc * sizeof(node_t));
        if (!na) return -1;
        h->a = na;
        h->cap = nc;
    }
    int64_t i = h->n++;
    while (i > 0) {                                   /* sift up */
        int64_t p = (i - 1) / 2;
        const node_t *q = &h->a[p];
        if (q->f < v.f || (q->f == v.f && q->g <= v.g)) break;
        h->a[i] = *q;
        i = p;
    }
    h->a[i] = v;
    return 0;
}

/** @brief Pop the smallest node into *out; returns 0 when empty. */
static int heap_pop(heap_t *h, node_t *out)
{
    if (h->n == 0) return 0;
    *out = h->a[0];
    node_t last = h->a[--h->n];
    int64_t i = 0;
    for (;;) {                                        /* sift down */
        int64_t c = 2 * i + 1;
        if (c >= h->n) break;
        if (c + 1 < h->n && (h->a[c + 1].f < h->a[c].f || (h->a[c + 1].f == h->a[c].f && h->a[c + 1].g < h->a[c].g))) c++;
        if (last.f < h->a[c].f || (last.f == h->a[c].f && last.g <= h->a[c].g)) break;
        h->a[i] = h->a[c];
        i = c;
    }
    h->a[i] = last;
    return 1;
}

/** @brief Octile distance (in cells) from (i, j) to (ti, tj); 0 when the heuristic is off. */
static double octile(int i, int j, int ti, int tj, int use)
{
    if (!use) return 0.0;
    int di = abs(i - ti), dj = abs(j - tj);
    int mn = di < dj ? di : dj, mx = di < dj ? dj : di;
    return (double)(mx - mn) + 1.41421356 * (double)mn;
}

/**
 * @brief A* over the rule masks.
 *
 * Corner cutting: with corner_cut = 1 a diagonal step may pass between two blocked cells. A 45 degree alley between two via
 * drills is free along one diagonal of cells only: both orthogonal neighbours of every step lie inside a neighbour's
 * clearance, so the no-corner-cutting rule shuts a way that is legal on the board. The two end cells of the step each keep the
 * full clearance, and the middle of a 0.07 mm step gives away about 2 um of it, which the router's margin covers. Obstacles in
 * the masks are always wider than a cell, so no track can step through one.
 *
 * @param free_      [nL][H][W] 1 = a track centre may sit on this cell on this layer
 * @param via_ok     [H][W]     0 = no via on this cell; 1 = a via may sit here (every layer clear); n > 1 = a via may sit here at
 *                              an extra (n - 1) * halo_cost (a negotiated via site)
 * @param halo       [nL][H][W] soft keep-away weight per cell: a step into it costs halo[cell] * halo_cost extra
 *                   (0 = none, 1 = pad halo, larger = escape band of a fine-pitch pin row); may be NULL
 * @param goal       [nL][H][W] 1 = goal cell
 * @param starts     flattened start cell ids (g = 0 each)
 * @param nstart     number of start cells
 * @param via_cost   cost of one layer change (cells)
 * @param halo_cost  extra cost per step per halo weight unit
 * @param use_heur   1 = octile heuristic toward (ti, tj), 0 = uniform cost (nearest goal first)
 * @param corner_cut 1 = a diagonal step may pass between two blocked cells, 0 = it may not
 * @param max_expand expansion budget (cells expanded before the search gives up)
 * @param path_out   buffer for the path (cell ids, start .. goal)
 * @param path_cap   capacity of path_out
 * @param nexp_out   number of expanded cells (always written)
 * @return path length (>= 1), 0 = no path within the budget, -1 = bad input, -2 = out of memory, -3 = path_cap too small
 */
GRID_ASTAR_API int64_t grid_astar(const uint8_t *free_, const uint8_t *via_ok, const uint8_t *halo, const uint8_t *goal,
                                  int32_t nL, int32_t H, int32_t W, const int64_t *starts, int64_t nstart,
                                  double via_cost, double halo_cost, int32_t use_heur, int32_t ti, int32_t tj, int32_t corner_cut,
                                  int64_t max_expand, int64_t *path_out, int64_t path_cap, int64_t *nexp_out)
{
    if (nexp_out) *nexp_out = 0;
    if (!free_ || !via_ok || !goal || !starts || !path_out || nL <= 0 || H <= 0 || W <= 0 || nstart <= 0 || path_cap <= 0) return -1;
    const int64_t N = (int64_t)nL * H * W, HW = (int64_t)H * W;
    double *gbest = (double *)malloc((size_t)N * sizeof(double));
    int64_t *came = (int64_t *)malloc((size_t)N * sizeof(int64_t));
    uint8_t *closed = (uint8_t *)calloc((size_t)N, 1);
    heap_t hp = {0};
    int64_t ret = 0, nexp = 0, seq = 0, goal_id = -1;
    if (!gbest || !came || !closed) { ret = -2; goto done; }
    for (int64_t k = 0; k < N; k++) gbest[k] = INFINITY;
    for (int64_t s = 0; s < nstart; s++) {
        int64_t id = starts[s];
        if (id < 0 || id >= N) { ret = -1; goto done; }
        int i = (int)((id % HW) / W), j = (int)(id % W);
        gbest[id] = 0.0;
        came[id] = -1;
        node_t v = {octile(i, j, ti, tj, use_heur), 0.0, id, seq++};
        if (heap_push(&hp, v)) { ret = -2; goto done; }
    }
    static const int DI[8] = {-1, 1, 0, 0, -1, -1, 1, 1}, DJ[8] = {0, 0, -1, 1, -1, 1, -1, 1};
    static const double DC[8] = {1.0, 1.0, 1.0, 1.0, 1.41421356, 1.41421356, 1.41421356, 1.41421356};
    node_t cur;
    while (heap_pop(&hp, &cur)) {
        int64_t id = cur.id;
        if (closed[id]) continue;
        if (cur.g > gbest[id]) continue;                  /* stale entry */
        closed[id] = 1;
        nexp++;
        if (goal[id]) { goal_id = id; break; }
        if (nexp > max_expand) break;
        int l = (int)(id / HW), i = (int)((id % HW) / W), j = (int)(id % W);
        const int64_t lbase = (int64_t)l * HW;
        for (int d = 0; d < 8; d++) {                     /* 8-way moves on this layer, no corner cutting unless asked for */
            int a = i + DI[d], b = j + DJ[d];
            if (a < 0 || a >= H || b < 0 || b >= W) continue;
            int64_t nid = lbase + (int64_t)a * W + b;
            if (!free_[nid]) continue;
            if (!corner_cut && DI[d] && DJ[d] && !(free_[lbase + (int64_t)i * W + b] && free_[lbase + (int64_t)a * W + j])) continue;
            double ng = cur.g + DC[d] + (halo ? halo_cost * (double)halo[nid] : 0.0);   /* weighted soft cost */
            if (ng < gbest[nid]) {
                gbest[nid] = ng;
                came[nid] = id;
                node_t v = {ng + octile(a, b, ti, tj, use_heur), ng, nid, seq++};
                if (heap_push(&hp, v)) { ret = -2; goto done; }
            }
        }
        if (via_ok[(int64_t)i * W + j]) {                 /* layer changes through a via on this cell */
            for (int l2 = 0; l2 < nL; l2++) {
                if (l2 == l) continue;
                int64_t nid = (int64_t)l2 * HW + (int64_t)i * W + j;
                if (!free_[nid]) continue;
                double ng = cur.g + via_cost + (halo ? halo_cost * (double)(via_ok[(int64_t)i * W + j] - 1) : 0.0);
                if (ng < gbest[nid]) {
                    gbest[nid] = ng;
                    came[nid] = id;
                    node_t v = {ng + octile(i, j, ti, tj, use_heur), ng, nid, seq++};
                    if (heap_push(&hp, v)) { ret = -2; goto done; }
                }
            }
        }
    }
    if (goal_id < 0) { ret = 0; goto done; }
    {                                                     /* walk back, then reverse into path_out */
        int64_t n = 0;
        for (int64_t k = goal_id; k >= 0; k = came[k]) {
            if (n >= path_cap) { ret = -3; goto done; }
            path_out[n++] = k;
        }
        for (int64_t a = 0, b = n - 1; a < b; a++, b--) { int64_t t = path_out[a]; path_out[a] = path_out[b]; path_out[b] = t; }
        ret = n;
    }
done:
    if (nexp_out) *nexp_out = nexp;
    free(gbest);
    free(came);
    free(closed);
    free(hp.a);
    return ret;
}
