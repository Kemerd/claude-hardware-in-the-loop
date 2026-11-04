/**
 * @file thermal_sweep.cu
 * @brief GPU side of the thermal sweep: one thread per case, the shared kernel body from thermal_sweep_kernel.h.
 *
 * Not compiled by the twin's CMake build. gpu/build_ptx.ps1 turns it into PTX for a virtual architecture
 * (the driver JIT-compiles it for whatever GPU is installed) and embeds the text in thermal_sweep_ptx.inc, which
 * gpu_sweep.c hands to the CUDA driver at run time. Building the twin therefore needs no CUDA toolkit, and a machine
 * without a GPU runs the CPU path.
 */
#include "thermal_sweep_kernel.h"

extern "C" __global__ void tw_thermal_sweep(const tw_sweep_case* cases, double* peak, int n)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i < n) peak[i] = tw_sweep_case_peak(&cases[i]);
}
