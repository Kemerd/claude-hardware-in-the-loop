/**
 * @file gpu_sweep.c
 * @brief CPU reference and CUDA driver-API backend of the thermal sweep (see gpu_sweep.h).
 */
#include "gpu_sweep.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(_WIN32)
#include <windows.h>
#else
#include <dlfcn.h>
#endif

#include "thermal_sweep_ptx.inc"

/* ------------------------------------------------------------------------------------------------------------------
 *  The slice of the CUDA driver API used here, declared locally (no CUDA headers needed to build). The signatures
 *  follow cuda.h; CUdeviceptr is 64-bit on every 64-bit platform the driver supports.
 * ------------------------------------------------------------------------------------------------------------------ */
typedef int CUresult;
typedef int CUdevice;
typedef void* CUcontext;
typedef void* CUmodule;
typedef void* CUfunction;
typedef unsigned long long CUdeviceptr;

typedef struct {
    CUresult (*Init)(unsigned);
    CUresult (*DeviceGetCount)(int*);
    CUresult (*DeviceGet)(CUdevice*, int);
    CUresult (*DeviceGetName)(char*, int, CUdevice);
    CUresult (*PrimaryCtxRetain)(CUcontext*, CUdevice);
    CUresult (*PrimaryCtxRelease)(CUdevice);
    CUresult (*CtxSetCurrent)(CUcontext);
    CUresult (*CtxSynchronize)(void);
    CUresult (*ModuleLoadData)(CUmodule*, const void*);
    CUresult (*ModuleUnload)(CUmodule);
    CUresult (*ModuleGetFunction)(CUfunction*, CUmodule, const char*);
    CUresult (*MemAlloc)(CUdeviceptr*, size_t);
    CUresult (*MemFree)(CUdeviceptr);
    CUresult (*MemcpyHtoD)(CUdeviceptr, const void*, size_t);
    CUresult (*MemcpyDtoH)(void*, CUdeviceptr, size_t);
    CUresult (*LaunchKernel)(CUfunction, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, unsigned, void*,
                             void**, void**);
} cu_api;

/** @brief Process-wide driver state: loaded once, disabled for good after the first failure. */
static struct {
    int tried, ok;
    cu_api f;
    CUdevice dev;
    CUcontext ctx;
    CUmodule mod;
    CUfunction fn;
    char name[128];
    char why[256];
} g_cu;

/** @brief Write a message into the caller's buffer. */
static void say(char* why, size_t len, const char* msg)
{
    if (why && len) snprintf(why, len, "%s", msg);
}

/** @brief Load the driver and the module once. Returns 1 when the GPU path is usable. */
static int cu_open(void)
{
    if (g_cu.tried) return g_cu.ok;
    g_cu.tried = 1;

    /* ---- the opt-in: CI and every job that did not pass the gate run on the CPU */
    const char* opt = getenv("HIL_GPU");
    if (!opt || strcmp(opt, "1") != 0) {
        snprintf(g_cu.why, sizeof g_cu.why, "HIL_GPU is not 1 (GPU jobs are opt-in, after sim/isaac/gpu_gate.py)");
        return 0;
    }

    /* ---- the driver library: part of the NVIDIA driver, not of the toolkit */
#if defined(_WIN32)
    HMODULE lib = LoadLibraryA("nvcuda.dll");
#define SYM(n) ((void*)GetProcAddress(lib, n))
#else
    void* lib = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
#define SYM(n) dlsym(lib, n)
#endif
    if (!lib) {
        snprintf(g_cu.why, sizeof g_cu.why, "no NVIDIA driver library (nvcuda.dll / libcuda.so.1)");
        return 0;
    }
    void* p[16] = {SYM("cuInit"), SYM("cuDeviceGetCount"), SYM("cuDeviceGet"), SYM("cuDeviceGetName"),
                   SYM("cuDevicePrimaryCtxRetain"), SYM("cuDevicePrimaryCtxRelease_v2"), SYM("cuCtxSetCurrent"),
                   SYM("cuCtxSynchronize"), SYM("cuModuleLoadData"), SYM("cuModuleUnload"), SYM("cuModuleGetFunction"),
                   SYM("cuMemAlloc_v2"), SYM("cuMemFree_v2"), SYM("cuMemcpyHtoD_v2"), SYM("cuMemcpyDtoH_v2"),
                   SYM("cuLaunchKernel")};
#undef SYM
    for (int i = 0; i < 16; ++i)
        if (!p[i]) {
            snprintf(g_cu.why, sizeof g_cu.why, "driver lacks entry point #%d (driver too old?)", i);
            return 0;
        }
    memcpy(&g_cu.f, p, sizeof p);

    /* ---- device 0, its primary context, the embedded module */
    int count = 0;
    if (g_cu.f.Init(0) != 0 || g_cu.f.DeviceGetCount(&count) != 0 || count < 1 || g_cu.f.DeviceGet(&g_cu.dev, 0) != 0) {
        snprintf(g_cu.why, sizeof g_cu.why, "cuInit / no CUDA device");
        return 0;
    }
    g_cu.f.DeviceGetName(g_cu.name, (int)sizeof g_cu.name, g_cu.dev);
    if (g_cu.f.PrimaryCtxRetain(&g_cu.ctx, g_cu.dev) != 0 || g_cu.f.CtxSetCurrent(g_cu.ctx) != 0) {
        snprintf(g_cu.why, sizeof g_cu.why, "cannot retain the primary context on %s", g_cu.name);
        return 0;
    }
    CUresult r = g_cu.f.ModuleLoadData(&g_cu.mod, tw_thermal_sweep_ptx);
    if (r != 0 || g_cu.f.ModuleGetFunction(&g_cu.fn, g_cu.mod, "tw_thermal_sweep") != 0) {
        snprintf(g_cu.why, sizeof g_cu.why, "PTX JIT / kernel lookup failed (CUresult %d) on %s", r, g_cu.name);
        g_cu.f.PrimaryCtxRelease(g_cu.dev);
        return 0;
    }
    g_cu.ok = 1;
    snprintf(g_cu.why, sizeof g_cu.why, "CUDA on %s", g_cu.name);
    return 1;
}

/** @brief GPU run; returns 0 ok, -1 on any driver error (the caller falls back). */
static int run_gpu(const tw_sweep_case* cases, int n, double* peak)
{
    CUdeviceptr dc = 0, dp = 0;
    const size_t bc = sizeof(tw_sweep_case) * (size_t)n, bp = sizeof(double) * (size_t)n;
    int rc = -1;
    if (g_cu.f.CtxSetCurrent(g_cu.ctx) != 0) return -1;
    if (g_cu.f.MemAlloc(&dc, bc) != 0 || g_cu.f.MemAlloc(&dp, bp) != 0) goto done;
    if (g_cu.f.MemcpyHtoD(dc, cases, bc) != 0) goto done;
    {
        int nn = n;
        void* args[3] = {&dc, &dp, &nn};
        const unsigned block = 128, grid = (unsigned)((n + 127) / 128);
        if (g_cu.f.LaunchKernel(g_cu.fn, grid, 1, 1, block, 1, 1, 0, NULL, args, NULL) != 0) goto done;
    }
    if (g_cu.f.CtxSynchronize() != 0 || g_cu.f.MemcpyDtoH(peak, dp, bp) != 0) goto done;
    rc = 0;
done:
    if (dc) g_cu.f.MemFree(dc);
    if (dp) g_cu.f.MemFree(dp);
    return rc;
}

int tw_sweep_run(const tw_sweep_case* cases, int n, double* peak, tw_backend want, int* used_gpu, char* why,
                 size_t whylen)
{
    if (!cases || !peak || n < 1) return -1;
    if (used_gpu) *used_gpu = 0;

    /* ---- GPU when asked for (or AUTO) and usable; a failure disables it and falls through to the CPU */
    if (want != TW_BACKEND_CPU && cu_open()) {
        if (run_gpu(cases, n, peak) == 0) {
            if (used_gpu) *used_gpu = 1;
            say(why, whylen, g_cu.why);
            return 0;
        }
        g_cu.ok = 0;
        snprintf(g_cu.why, sizeof g_cu.why, "a CUDA call failed on %s; GPU disabled for this process", g_cu.name);
    }
    say(why, whylen, want == TW_BACKEND_CPU ? "CPU requested" : g_cu.why);

    /* ---- CPU reference */
    for (int i = 0; i < n; ++i) peak[i] = tw_sweep_case_peak(&cases[i]);
    return 0;
}
