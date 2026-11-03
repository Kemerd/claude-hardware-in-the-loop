# Firmware: out-of-tree builds, host suites, firmware in the loop, bench rigs

The rule: the code that flies, drives or regulates on the hardware is the same code that runs in CI. It is compiled for
the PC, fed by models of the sensors and actuators, and checked by thousands of assertions on every change. What
cannot be closed without hardware is listed as hardware-pending, never stubbed.

## 1. Layout

```
firmware/
  <target>/                  one folder per microcontroller target (main/, components/, sdkconfig.defaults, flash.ps1)
  <target>/components/core/  the portable control code: estimators, controllers, state machines, codecs. No vendor
                             SDK headers here; hardware access goes through a small HAL interface (hal_*.h)
  host_tests/                hil_check.h + one test_*.c per module; built and run on the PC by CI
  bench/<rig>/               bench rig firmware and its logging host script
sim/twin/                    the native digital twin (section 4)
```

Keeping vendor headers out of the core is what makes host compilation possible: the HAL is a header with function
pointers or link-time implementations, one for the target, one for the host (fed by the twin's device models).

## 2. Out-of-tree builds

Build output never lands in the source tree or the user profile.

- ESP-IDF: `idf.py -B <repo>/.tmp/build/<target> -D SDKCONFIG=<repo>/.tmp/build/<target>/sdkconfig build`. The
  tracked file is `sdkconfig.defaults`; the generated `sdkconfig` lives in scratch, so two worktrees never fight over it.
  Set `IDF_TOOLS_PATH` and the component-manager cache to a tools folder outside the profile, turn ccache off in CI or
  point it at `.tmp`.
- CMake targets (STM32, RP2040, nRF): `cmake -S firmware/<target> -B .tmp/build/<target> -G Ninja` with a pinned
  toolchain file.
- One build directory per worktree per target. Never run another lane's binaries in place (the file stays locked and
  breaks that lane's next link); copy them into your own `.tmp` run folder.

## 3. Host-compiled test suites

`templates/firmware/host_tests/hil_check.h` is a header-only check library (C99 / C++11):

```c
#define HIL_CHECK_IMPLEMENTATION
#include "hil_check.h"
int main(void) {
    HIL_CHECK_NEAR(pid_step(&pid, 0.1f), 0.42, 1e-4);
    HIL_CHECK_EQ_INT(sm_next(&sm, EV_ARM), STATE_ARMED);
    return hil_check_summary("controller");      // "[hil] controller: N/N checks passed, 0 failed"
}
```

- Every suite ends with exactly one summary line; the CI runner parses it. Exit 0 without the line is a failure, and an
  empty suite is a failure, so a crashed or truncated run never looks green.
- NaN and infinity always fail a tolerance check.
- Suites grow with the code: a bug found on the bench becomes a check before it is fixed. Thousands of checks are
  normal and run in seconds.
- Add a CI group for them: a runner that builds `.tmp/build/host` with the host compiler, runs each `test_*` binary, and
  records `passed / total` from the summary lines (see `windows/ci/audit.ps1` for the runner shape).

Self-test of the header: `gcc -std=c99 -Wall -Wextra -O2 -o .tmp/test_hil_check test_hil_check.c -lm` (C) and the same
file compiled as C++11 both print `[hil] hil_check: 5/5 checks passed, 0 failed`.

## 4. Firmware in the loop: the native twin

A C++ program (`sim/twin/`) that links the REAL control code and replaces the hardware with models:

| Piece | What it is |
|---|---|
| physics | rigid-body (or circuit, or thermal) model of the product, parameters generated from the CAD export (`sim/twin/out/params.txt` written by a script that reads `cad/<asm>/parts.json`: mass, CG, inertia, articulation) |
| device models | each sensor and actuator the HAL talks to: an IMU with noise and bias from its datasheet, a motor with its constants, a bus device with its register map; errors are injectable |
| hosted HAL | the host implementation of `hal_*.h`, backed by the device models |
| the station API | scenarios drive the twin through the same interface the real base station or host app uses (UDP / serial protocol), never by poking internals |
| scenarios | JSON missions (`missions/*.json`): initial state, disturbances, commands, acceptance numbers |

Rules:

- **Real logic in the loop**: a scenario that passes in the twin must be runnable on the hardware unchanged. Scripted
  motion or canned sensor data is not a scenario.
- **Seed sweeps**: every stochastic scenario runs over N seeds (CI: a handful; nightly: 40); the result is the
  distribution, not the best seed.
- **Determinism check**: the same seed twice gives a byte-identical trace (hash it); a different hash means hidden state
  (uninitialised memory, wall-clock time, hash-order iteration).
- **Regression guards**: a reference path that must stay byte-identical (for example a simpler configuration that is
  already proven) is hashed in the CI report; `-Compare` flags any change.
- **Station slots**: when several twins run in parallel, each takes a port slot from a shared registry so two stations
  never bind the same port.

## 5. Bench rigs

- A rig is firmware + a host logging script + a procedure in `docs/bench/<rig>.md`. Logs are CSV with a header that
  names units, the rig revision, the firmware commit and the date.
- Every constant only a bench can settle is a row in `docs/bench/BENCH_REGISTER.md` (`B-<SUB>-<nn>`): parameter and
  design value, where it is used, test method, pass range. The code says `design value, BENCH B-PWR-03`, and the audit
  accepts that instead of an admitted guess.
- Results go to `docs/data/bench/<id>/<date>/` with the usual README.

## 6. Flashing

One script per target (`firmware/<target>/flash.ps1`): it builds out of tree, checks the connected device (chip id,
flash size) before writing, writes, verifies, and prints the commit it flashed. It never flashes a dirty tree without
saying so in its output.

## 7. Hardware-pending

Reports carry a "hardware-pending" list: what is implemented and host-tested but needs the real board to close (a
latency on the target CPU, an analog front-end calibration, an EMC behaviour), each with the bench procedure or BENCH
id that will close it.
