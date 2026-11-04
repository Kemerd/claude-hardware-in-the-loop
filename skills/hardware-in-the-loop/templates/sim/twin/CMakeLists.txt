# =====================================================================================================================
#  sim/twin - the native digital twin (C11 core, optional C++17 wrapper, optional CUDA backend loaded at run time)
#
#  Build out of tree (never into the source tree):
#      cmake -S sim/twin -B .tmp/build/twin -G Ninja -DCMAKE_BUILD_TYPE=Release
#      cmake --build .tmp/build/twin
#  Compilers: clang, gcc or MSVC. Nothing here needs the CUDA toolkit; gpu/ ships its kernel as embedded PTX.
#
#  Targets
#      hil_twin_models   static library: rigid body, contact, motors, joint, thermal, battery, sensors, params, trace
#      hil_core          static library: the example firmware core (firmware/core), compiled UNCHANGED for the host
#      hil_twin_plant    static library: the example plant (actuator rig) wired to the firmware through the hosted HAL
#      hil_twin_api      shared library (DLL / .so): the C ABI Unreal, Isaac Sim and any other host load
#      hil_twin          command-line runner: scenarios, seeds, traces, the one summary line per run
#      test_twin_models, test_twin_api, test_hil_core   host suites (hil_check.h summary lines)
# =====================================================================================================================
cmake_minimum_required(VERSION 3.20)
project(hil_twin LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

# ---------------------------------------------------------------------------------------------------------------------
#  Paths: the firmware core and the check header live outside this folder (the real firmware is linked, not copied)
# ---------------------------------------------------------------------------------------------------------------------
set(HIL_REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../.." CACHE PATH "repository root")
set(HIL_CORE_DIR "${HIL_REPO_ROOT}/firmware/core" CACHE PATH "portable firmware core")
set(HIL_CHECK_DIR "${HIL_REPO_ROOT}/firmware/host_tests" CACHE PATH "folder holding hil_check.h")
foreach(_d IN ITEMS "${HIL_CORE_DIR}/include" "${HIL_CHECK_DIR}")
  if(NOT EXISTS "${_d}")
    message(FATAL_ERROR "hil_twin: ${_d} not found (set HIL_REPO_ROOT, HIL_CORE_DIR or HIL_CHECK_DIR)")
  endif()
endforeach()

# ---------------------------------------------------------------------------------------------------------------------
#  Determinism flags: no fast-math, no fused multiply-add contraction (FMA changes the last bits differently per
#  compiler and per CPU, and the determinism gate compares traces byte for byte)
# ---------------------------------------------------------------------------------------------------------------------
if(WIN32)
  # the portable C library calls (fopen, snprintf) are what we want; the Annex K "_s" variants are not portable
  add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
endif()
if(MSVC)
  set(HIL_FP_FLAGS /fp:precise /W4 /wd4100 /wd4201)
else()
  set(HIL_FP_FLAGS -ffp-contract=off -fno-fast-math -Wall -Wextra -Wno-unused-parameter)
endif()

# ---------------------------------------------------------------------------------------------------------------------
#  Model library
# ---------------------------------------------------------------------------------------------------------------------
add_library(hil_twin_models STATIC
  src/rng.c src/rigid_body.c src/actuators.c src/thermal.c src/battery.c src/sensors.c src/params.c src/trace.c)
target_include_directories(hil_twin_models PUBLIC include)
target_compile_options(hil_twin_models PRIVATE ${HIL_FP_FLAGS})
if(UNIX)
  target_link_libraries(hil_twin_models PUBLIC m)
endif()

# ---------------------------------------------------------------------------------------------------------------------
#  Host suites
# ---------------------------------------------------------------------------------------------------------------------
add_executable(test_twin_models tests/test_twin_models.c)
target_include_directories(test_twin_models PRIVATE "${HIL_CHECK_DIR}")
target_link_libraries(test_twin_models PRIVATE hil_twin_models)
target_compile_options(test_twin_models PRIVATE ${HIL_FP_FLAGS})

# ---------------------------------------------------------------------------------------------------------------------
#  The firmware core, compiled unchanged from firmware/core (the same sources the target build uses)
# ---------------------------------------------------------------------------------------------------------------------
add_library(hil_core STATIC "${HIL_CORE_DIR}/src/ctrl.c" "${HIL_CORE_DIR}/src/link.c")
target_include_directories(hil_core PUBLIC "${HIL_CORE_DIR}/include")
target_compile_options(hil_core PRIVATE ${HIL_FP_FLAGS})
if(UNIX)
  target_link_libraries(hil_core PUBLIC m)
endif()

# ---------------------------------------------------------------------------------------------------------------------
#  The example plant and the C ABI. Built twice: a static copy for the host suites, the shared library for hosts.
# ---------------------------------------------------------------------------------------------------------------------
set(HIL_PLANT_SOURCES src/plant.c src/twin_api.c)

add_library(hil_twin_static STATIC ${HIL_PLANT_SOURCES})
target_include_directories(hil_twin_static PUBLIC include src)
target_compile_definitions(hil_twin_static PUBLIC HIL_TWIN_STATIC)
target_link_libraries(hil_twin_static PUBLIC hil_twin_models hil_core)
target_compile_options(hil_twin_static PRIVATE ${HIL_FP_FLAGS})

add_library(hil_twin_api SHARED ${HIL_PLANT_SOURCES})
target_include_directories(hil_twin_api PUBLIC include PRIVATE src)
target_compile_definitions(hil_twin_api PRIVATE HIL_TWIN_BUILD_DLL)
target_link_libraries(hil_twin_api PRIVATE hil_twin_models hil_core)
target_compile_options(hil_twin_api PRIVATE ${HIL_FP_FLAGS})
set_target_properties(hil_twin_api PROPERTIES C_VISIBILITY_PRESET hidden PREFIX "")

# ---- the runner talks to the twin only through the shared library, like any engine host
add_executable(hil_twin src/twin_main.c)
target_link_libraries(hil_twin PRIVATE hil_twin_api hil_core)
target_compile_options(hil_twin PRIVATE ${HIL_FP_FLAGS})

# ---------------------------------------------------------------------------------------------------------------------
#  Host suites of the firmware core and of the ABI
# ---------------------------------------------------------------------------------------------------------------------
add_executable(test_hil_core tests/test_hil_core.c)
target_include_directories(test_hil_core PRIVATE "${HIL_CHECK_DIR}")
target_link_libraries(test_hil_core PRIVATE hil_core)
target_compile_options(test_hil_core PRIVATE ${HIL_FP_FLAGS})

add_executable(test_twin_api tests/test_twin_api.c)
target_include_directories(test_twin_api PRIVATE "${HIL_CHECK_DIR}")
target_link_libraries(test_twin_api PRIVATE hil_twin_static)
target_compile_options(test_twin_api PRIVATE ${HIL_FP_FLAGS})
