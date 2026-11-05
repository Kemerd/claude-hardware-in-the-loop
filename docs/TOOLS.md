# Tools: what to install, why, where it lives

Install or verify everything with the skill's setup script:

```
pwsh -File skills\hardware-in-the-loop\scripts\setup_tools.ps1 -ToolsRoot D:\hiltools            # Windows
pwsh -File skills\hardware-in-the-loop\scripts\setup_tools.ps1 -ToolsRoot D:\hiltools -VerifyOnly
bash skills/hardware-in-the-loop/scripts/setup_tools.sh --tools-root /opt/hiltools                 # Linux
```

It is idempotent (a second run only verifies), refuses a tools folder inside the user profile, checks pinned sizes and
SHA-256 hashes before using a download, and writes `<ToolsRoot>\hil_tools.env` with the `HIL_*` variables below.

## 1. The toolchain

| Tool | Version pinned | Why | Where it lives | Install |
|---|---|---|---|---|
| KiCad | 10.x (tested 10.0.6) | schematic / board files, `kicad-cli` (ERC, DRC, Gerbers, drill, pos, STEP), and its bundled python with `pcbnew` that the board pipeline runs under | Program Files (machine-wide) | `winget install KiCad.KiCad`; Linux: the KiCad 10 PPA |
| Java | 21 (Temurin JRE) | Freerouting 2.x needs Java 21 | Program Files | `winget install EclipseAdoptium.Temurin.21.JRE` |
| Freerouting | 2.1.0 jar, 66,978,394 bytes, SHA-256 `2c07d58f75dac03782664081e7a58b41c25400d871a9fcf166a2ea6fe60d5def` | autorouter (Specctra DSN in, SES out), run headless | `<ToolsRoot>\freerouting\` | downloaded from the GitHub release; upstream publishes no checksum, so the pin is the hash of the release asset computed on 2026-11-03 |
| OSS CAD Suite | build 2026-10-24, Windows x64, 604,518,009 bytes, SHA-256 `6b38c1a57a17d8432b3d58f63beaa4e7919dcb6c19f9fcb6ca043fe8cb1b2cf7` | open FPGA flow: yosys, nextpnr, Icarus Verilog, Verilator, openFPGALoader (only when the project has an FPGA) | `<ToolsRoot>\oss-cad-suite-20261024\` | `https://github.com/YosysHQ/oss-cad-suite-build/releases/download/2026-10-24/oss-cad-suite-windows-x64-20261024.tgz`, hash checked before unpacking |
| CadQuery venv | cadquery 2.x + trimesh, matplotlib, numpy, pillow, scipy, shapely | parametric CAD builders, STEP / STL export, renders; also a python the negotiated PCB router can use | `<ToolsRoot>\cqv\` | `python -m venv` + pip, pip cache in `<ToolsRoot>\cache\pip` |
| Python | 3.10+ | tooling: audit, portability, renders, scaffold, viewer packer | system install | python.org or winget |
| PowerShell | 7 (pwsh) | CI entry point and runners | Program Files | `winget install Microsoft.PowerShell` |
| Git + Git Bash | current | worktrees, the harness scripts (bash) | Program Files | `winget install Git.Git` |
| ESP-IDF | the release you pin (optional) | ESP32 targets | `<ToolsRoot>\esp-idf`, tools in `<ToolsRoot>\espressif` (`IDF_TOOLS_PATH`) | Espressif installer or a `git clone --recursive` of the pinned tag; the setup script prints the steps with `-WithEspIdf` |
| CMake, a C11 / C++17 compiler, Ninja | CMake >= 3.20; clang 18+ (tested 20.1), MSVC or gcc | the native twin and the firmware host suites | Program Files | `winget install Kitware.CMake LLVM.LLVM Ninja-build.Ninja` (Visual Studio ships Ninja too) |
| Unreal Engine | 5.8.4 or newer (tested 5.8.4) | the recommended twin host (Kinematic replay, Chaos coupling, rendering) | a large non-system drive | Epic Games Launcher > Unreal Engine > Library; or a source build; plus the Visual Studio version its release notes name with "Game development with C++" |
| Isaac Sim | the release you pin (harness written against 6.2) | alternative twin host on PhysX | a tools drive, its own venv with the Python version that release requires | `pip install "isaacsim[all,extscache]==<version>" --extra-index-url https://pypi.nvidia.com`; GPU gated (see SIM.md) |
| NVIDIA driver / CUDA toolkit | driver: current; toolkit 12.x+ only to regenerate PTX | the twin's optional GPU Monte Carlo | Program Files | the driver installer; the toolkit from developer.nvidia.com |

`skills/hardware-in-the-loop/scripts/check_twin_tools.ps1` finds all of the twin's tools and prints what is
missing; it never installs anything.

The OSS CAD Suite components are used as external tools only; nothing of them is linked into or shipped with a
bitstream. Check each component's licence in the suite's `license/` folder against your project's licence rules.

## 2. Environment variables the scripts read

| Variable | Read by | Meaning |
|---|---|---|
| `HIL_KICAD_CLI`, `HIL_KICAD_ROOT` | pipeline, CI | override the KiCad discovery (newest under Program Files otherwise) |
| `HIL_KICAD_HOME` | `kicad_env.py` | KiCad's per-user folders (default `<repo>/.tmp/kicad_home`) |
| `HIL_FREEROUTING_JAR` | pipeline | the Freerouting jar; without it the pipeline stops before routing with a clear message |
| `HIL_JAVA` | pipeline | Java 21 executable (falls back to `JAVA_HOME`, then `PATH`) |
| `HIL_KICAD_SITE` | `place_labels.py`, `route_board.py`, `pf_route.py` | folder with the packages KiCad's python lacks (`pip install --target`): shapely for the labels, scipy and shapely for the router |
| `HIL_ROUTER_PYTHON` | `route_board.py` | a python with numpy, scipy and shapely for the router's search (falls back to KiCad's python with `HIL_KICAD_SITE`, then `HIL_CQ_PYTHON`) |
| `HIL_ROUTER_CACHE` | `pf_route.py` | where the compiled `astar.c` is kept (default `<repo>/.tmp/astar`) |
| `CC` | `pf_route.py` | the C compiler to try first (then clang, gcc, cc, and cl on Windows) |
| `HIL_CQ_PYTHON` | CI, docs | the CadQuery venv python |
| `HIL_PYTHON` | CI | the python for the audit / portability suites |
| `HIL_RAM_RESERVE_GB`, `HIL_CPU_MAX`, `HIL_START_GAP` | `slot.sh` | machine gate (defaults 4 GB, 90 %, 15 s; also from `harness/swarm/machine.env`) |
| `HIL_HANDOFF_PCT`, `HIL_HANDOFF_5H_PCT` | `usage_statusline.py` | usage tripwire thresholds (defaults 95 and off) |

## 3. Home-directory isolation

Tools love to write into the user profile: KiCad's settings and caches, pip and npm caches, matplotlib's font cache,
Hugging Face and torch model caches, Java's temp files, ESP-IDF's tools and component cache. On a shared workstation
that means gigabytes in the wrong place and settings that leak between runs. The method redirects all of them:

- `kicad_env.isolate()` sets `KICAD_CONFIG_HOME`, `KICAD_DOCUMENTS_HOME`, `KICAD_CACHE_HOME` (and `TEMP` / `TMP` for
  wx) to the scratch home BEFORE `pcbnew` is imported, seeds minimal library tables (only the libraries the project
  references, so `kicad-cli` starts fast) and turns KiCad's git integration off (`kicad_common.json` `git.enableGit`).
- Freerouting runs with `-Djava.io.tmpdir` and `-Duser.home` pointed at its work folder, with a pre-written
  `freerouting.json` that disables the GUI, telemetry and the update check.
- `windows/ci/ci_common.psm1` `Initialize-CiEnvironment` points `TEMP`, pip / npm / matplotlib / HF / torch / XDG caches
  and KiCad's folders at the CI output folder, and hides the GPU.
- Every script refuses an output path inside the user profile.

## 4. Machine prerequisites

- A drive with room: worktrees are full checkouts, CI output and router work folders are large; keep the disk floor
  from the interview (default 50 GB free) and check before big builds.
- An archive drive (or folder) for videos and sim outputs, linked into the main checkout as `archive/` (directory
  junction on Windows: `New-Item -ItemType Junction -Path archive -Target D:\archive\<project>`).
- PowerShell 7 and Git Bash on PATH; `winget` for the installs.
