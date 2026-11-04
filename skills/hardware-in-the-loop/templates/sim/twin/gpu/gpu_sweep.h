/**
 * @file gpu_sweep.h
 * @brief Thermal Monte Carlo sweep on the CPU or, when allowed and present, on an NVIDIA GPU through the CUDA
 *        DRIVER API loaded at run time (nvcuda.dll / libcuda.so.1). No CUDA toolkit or runtime is linked: the kernel
 *        is embedded PTX (thermal_sweep_ptx.inc) and the driver JIT-compiles it for the installed GPU.
 *
 * Rules (the GPU is a shared machine resource, never a silent default):
 *   - the GPU path runs only with HIL_GPU=1 in the environment (CI sets HIL_GPU=0) and after sim/isaac/gpu_gate.py
 *     said yes for the job;
 *   - any driver error disables the GPU for the rest of the process and the caller gets the CPU result, with the
 *     reason in *why;
 *   - the CPU path is the reference; --check compares the two.
 */
#ifndef HIL_TWIN_GPU_SWEEP_H
#define HIL_TWIN_GPU_SWEEP_H

#include <stddef.h>

#include "thermal_sweep_kernel.h"

#ifdef __cplusplus
extern "C" {
#endif

/** @brief Backend choice. */
typedef enum { TW_BACKEND_CPU = 0, TW_BACKEND_GPU = 1, TW_BACKEND_AUTO = 2 } tw_backend;

/**
 * @brief Run every case.
 * @param cases     n cases
 * @param n         case count (>= 1)
 * @param peak      n outputs (peak winding temperature, degC; negative = invalid case)
 * @param want      CPU, GPU (fails over to CPU with a reason) or AUTO (GPU when allowed and present)
 * @param used_gpu  set to 1 when the GPU produced the result
 * @param why       message buffer: why the GPU was not used, or which device was (may be NULL)
 * @return 0 ok, -1 bad arguments
 */
int tw_sweep_run(const tw_sweep_case* cases, int n, double* peak, tw_backend want, int* used_gpu, char* why,
                 size_t whylen);

#ifdef __cplusplus
}
#endif

#endif /* HIL_TWIN_GPU_SWEEP_H */
