# Firmware that runs on the board AND in the twin: C first, ESP32 / ESP32-P4 / ARM targets

## 1. The language rule (say it to the owner during the interview)

- **Firmware is C.** C11, with C++17 only where an SDK or a library needs it (no exceptions, no RTTI on the
  target). The control core compiles unchanged for the microcontroller and for the PC, where the twin links it.
- **No interpreter in shipped firmware.** MicroPython / CircuitPython are fine for a bench toy, but they cannot
  run inside the twin, cannot hold a fixed 1 kHz loop with bounded memory, and cannot be host-tested with the
  same sources. If the owner still wants one, record it in `DECISIONS.md` together with what it costs: the twin then
  drives a model of the firmware, not the firmware.
- **Python is for the PC side**: CAD builders, converters, plots, CI helpers, the Isaac Sim harness glue.
- **Engine glue is C++** (the Unreal plugin); the twin core is C so every host can load it.

## 2. The shape of the code

```
firmware/
  core/                    portable control code: C11, no vendor headers, no heap, no stdio
    include/hil_core/hal.h the HAL table (function pointers + ctx) and the board constants
    src/*.c                estimators, controllers, state machines, protocol codecs: one *_step() at a fixed rate
  <target>/                one folder per MCU target: the vendor project, the HAL implementation, main
  host_tests/              hil_check.h suites of the core (built and run on the PC by CI)
sim/twin/src/plant.c       the HAL implementation for the PC: raw values from the plant models
```

Rules: the HAL returns RAW values (counts, codes, bytes); the core converts them with board constants it owns
(`hil_core_board_t`), so a wrong divider or LSB in firmware shows up in the twin. Every HAL call returns an error
code the core handles. One `hil_core_step()` per tick, called by whatever owns time: a FreeRTOS task, a timer ISR,
a superloop, or the twin's lockstep loop.

## 3. ESP32 family (ESP-IDF 5.x / 6.x)

The core runs in one pinned FreeRTOS task at 1 kHz; the HAL table maps to ESP-IDF drivers. Names below are the
ESP-IDF driver APIs (checked against an ESP-IDF tree; confirm against the version the project pins).

```c
/* firmware/<target>/main/app_main.c (sketch of the wiring; every handle created and checked in board_init) */
static hil_core_t s_core;

static void ctrl_task(void* arg)
{
    TickType_t wake = xTaskGetTickCount();
    for (;;) {
        (void)hil_core_step(&s_core);                 /* errors are handled inside: fault state, motor off */
        xTaskDelayUntil(&wake, pdMS_TO_TICKS(1));     /* needs CONFIG_FREERTOS_HZ=1000 */
    }
}

void app_main(void)
{
    static const hal_t hal = { &g_board, esp_time_us, esp_encoder_read, esp_adc_read, esp_motor_write,
                               esp_link_recv, esp_link_send };
    ESP_ERROR_CHECK(board_init(&g_board));            /* creates every driver handle below, checks each return */
    if (hil_core_init(&s_core, &hal, &k_board, &k_tuning) != 0) abort();
    xTaskCreatePinnedToCore(ctrl_task, "ctrl", 4096, NULL, configMAX_PRIORITIES - 2, NULL, 1);
}
```

| HAL entry | ESP-IDF driver | Notes |
|---|---|---|
| `time_us` | `esp_timer_get_time()` | 64-bit microseconds since boot |
| `encoder_read` | PCNT: `pcnt_new_unit`, channels with edge / level actions for x4 decoding, `pcnt_unit_get_count` | extend to 32 bits in software (the hardware counter is 16-bit; use the watch-point / overflow callback) |
| `adc_read` | `adc_oneshot_new_unit`, `adc_oneshot_config_channel`, `adc_oneshot_read` | raw codes to the core; calibration (`adc_cali_*`) only if the board constants say the core expects calibrated mV |
| `motor_write` (brushed) | LEDC: `ledc_set_duty` + `ledc_update_duty` on two channels or one channel plus a direction GPIO, 20-25 kHz | an H-bridge (TB6612, DRV8833, DRV8871, VNH5019) |
| `motor_write` (brushless) | UART / CAN to a FOC drive in torque mode (ODrive, VESC, a SimpleFOC board), or MCPWM (`mcpwm_comparator_set_compare_value`) when the ESP32 runs FOC itself | running FOC on the ESP32 means a faster loop for the current: step the twin at the PWM rate for that part |
| `link_recv` / `link_send` | `uart_read_bytes` with a 0 timeout, `uart_write_bytes`; or USB CDC / TCP | the core decodes frames itself (`hil_link_feed`) |
| fixed-rate tick (alternative) | `gptimer_new_timer` + `gptimer_register_event_callbacks` notifying the task | when jitter below one FreeRTOS tick matters |

ESP32 specifics worth knowing:
- **Classic ESP32 / S3**: the ADC is non-linear near the rails and noisy; budget it in the twin (`adc_noise_lsb`,
  a code-dependent error table if the accuracy matters), keep the readings away from 0 V and full scale.
- **ESP32-P4**: no radio on chip (a companion such as an ESP32-C6 provides it, so the link is a UART / SDIO hop the
  twin should model with its latency), RISC-V cores with an FPU, MIPI-CSI camera and H.264 blocks. Its peripherals
  have hard limits that only show when every driver is up together: read the `soc_caps.h` of the pinned IDF for the
  number of SPI hardware chip selects and bus devices per host, the RMT / MCPWM / PCNT channel counts, and check the
  pin plan against them before the board is drawn (a hosted register-level device model in the twin catches this
  too; see section 6).
- `idf.py -B .tmp/build/<target>` keeps builds out of tree; `sdkconfig.defaults` is tracked, the generated
  `sdkconfig` lives in the build folder (`docs/FIRMWARE.md` of the method repository).
- Use the shared core from the ESP-IDF project without moving it: a component whose `CMakeLists.txt` is
  `idf_component_register(SRCS ../../core/src/ctrl.c ../../core/src/link.c INCLUDE_DIRS ../../core/include)` (paths
  relative to `firmware/<target>/components/hil_core/`), so the twin and the board build the same files.
- Count the peripherals before the pin plan: PCNT has 8 units on the classic ESP32 but 4 on the ESP32-S3 and the
  ESP32-P4 (`SOC_PCNT_UNITS_PER_GROUP` in `soc_caps.h`); a 6-axis arm with quadrature encoders on an S3 needs
  another plan (magnetic absolute encoders on SPI / I2C, or encoder counting on the motor drives).
- ESP-IDF has a Linux host target for some components (`idf.py --preview set-target linux`); useful for unit tests
  of IDF-dependent code, but it does not model the peripherals: the HAL split is still the way to run in the twin.

## 4. ARM Cortex-M (STM32, RP2040 / RP2350, nRF, others)

- **STM32**: STM32CubeMX for the clock tree and pin plan (generate into `firmware/<target>/`, never edit generated
  sections), HAL or LL drivers behind `hal_t`. Encoder: a timer in encoder mode (`TIMx->CNT`, 16 or 32 bit). PWM:
  a timer's compare registers (advanced timers with dead time for half bridges). ADC: DMA in circular mode,
  the latest sample per channel read by `adc_read`. Tick: a timer update interrupt that sets a flag or notifies a
  task; the main loop or a FreeRTOS / Zephyr thread calls `hil_core_step()`.
- **RP2040 / RP2350** (Pico SDK): PIO quadrature encoder program (the SDK example), `pwm_set_chan_level`, the ADC
  with its round-robin FIFO, `add_repeating_timer_us` or a core-1 loop for the tick.
- **nRF52 / nRF54** (nRF Connect SDK, Zephyr): QDEC for encoders, the PWM driver, SAADC; Zephyr threads with
  `k_timer` for the tick. Zephyr's `native_sim` board can also run Zephyr applications on the PC.
- Toolchain: `arm-none-eabi-gcc` with a pinned CMake toolchain file, `-ffunction-sections -fdata-sections`,
  `-Wall -Wextra -Werror` on the core. Build the core for the host with the same warnings.
- Floating point: the example core uses `float`; on a Cortex-M0 / M0+ (no FPU) port it to fixed point, keep the
  float version as the host reference, and compare them in a host test.

## 5. Arduino and PlatformIO

Fine for the bench and for the board support of a product, provided the control core stays in plain C files
(`firmware/core`) that the Arduino sketch calls from `loop()` at a fixed rate (a hardware timer flag, not
`delay()`). `analogRead`, `ledcWrite`, an encoder library and `Serial` map to the HAL table the same way.

## 6. How far to host the firmware in the twin (pick the level per project)

| Level | What runs in the twin | When |
|---|---|---|
| 1. Core only (the template) | `firmware/core` linked into the twin; the plant implements `hal_t` | always: it is cheap and catches control, state-machine, protocol and board-constant bugs |
| 2. Hosted drivers | the REAL target drivers compiled for the PC against device models at register / wire level (an I2C expander's register file, a SPI IMU's register map and FIFO, a UART with wire timing), wired by net as the schematic wires them | when the drivers are where the bugs are: bus contention, chip-select limits, start-up sequences, invalid-data windows |
| 3. The whole application | the unmodified `main.c` and its RTOS tasks on a lockstep scheduler (one context per task, one baton, the twin owns the tick: fibers / ucontext, or threads with a mutex baton) with a shim of the RTOS and SDK headers it includes | when task timing, priorities and inter-task protocols must be in the loop |
| 4. Instruction level | an emulator (Renode, QEMU) runs the target binary; the twin provides the outside world | when the binary itself (timing on the real core, an interrupt storm) must be checked |

Each level up costs more to build and maintain; most projects need level 1 everywhere and level 2 for the two or
three peripherals that cause trouble. Whatever the level: the firmware only sees what the hardware would show, the
twin owns time, and determinism holds (same seed, same trace).

## 7. Bench, then compare

A model is not validated by passing its own gates. For every constant that matters, a bench run (a
`docs/bench/BENCH_REGISTER.md` row) measures it on the real part, and the same scenario is run on the bench and in
the twin with the bench's numbers: the report shows both curves and the difference, and the twin's params take
the bench value with its BENCH id.
