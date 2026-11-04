# Onboarding interview

Ask these in batches through the question dialog (at most four questions per batch), each with the recommended default
first. Seven batches: the project, mechanics, electronics and firmware, simulation and the machine, how to run the
agents, the build spec, how the owner likes to work. When the owner does not answer a question, take the default and record it in the plan as "default taken".
The answers become `answers.json`, which `scripts/scaffold.py` turns into the project skeleton.

## Batch 1: the project

| # | Question | Why it matters | Recommended default |
|---|---|---|---|
| 1 | What are you building, in one paragraph? What does "done" look like? | `REQUIREMENTS.md` section 0, every brief | none: this one needs an answer |
| 2 | Which three to five goals matter most, in order? | lane priorities, acceptance list | none |
| 3 | Budget for prototypes (boards, parts, printing, machining)? | layer count, assembly service, material choices | state a number; "under 1,000 USD for the first build" if unsure |
| 4 | How many units: 1 prototype, 5-10 pilot units, 100+? | fab vendor, assembly, DFM rules, part selection (stock depth) | 5 |

## Batch 2: mechanical decisions (each one is a PROJECT decision, never a silent default)

| # | Question | Options | Recommended default |
|---|---|---|---|
| 5 | Default material for printed parts | PA12 or PA612 carbon-fibre (stiff, heat resistant, needs a hardened nozzle or a service) / PETG (cheap, tough) / PLA (prototype only, creeps under load and heat) / machined 6061 aluminium (stiff, expensive) | PETG for first prototypes, carbon-fibre nylon for flight or load-bearing parts |
| 6 | Fastening into plastic | brass heat-set inserts with thread locker (strong, re-usable) / thread-forming screws (cheap, fewer cycles) / through-bolts with nuts | heat-set inserts at every repeat-service joint |
| 7 | Enclosure | sealed (gasket in a lip, lid inside the lip on a ledge; needs a thermal path) / vented (simpler cooling, dust and water get in) / open frame | sealed when it lives outdoors or near fluids, else vented |
| 8 | Mechanical clearance rule | the minimum gap between parts that are not designed to touch | 1.0 mm static, 3.0 mm for moving parts and cables |

## Batch 3: electronics and firmware

| # | Question | Options | Recommended default |
|---|---|---|---|
| 9 | PCB fab and assembly vendor | JLCPCB (cheap, LCSC parts catalogue, live verification supported) / PCBWay / a local house | JLCPCB |
| 10 | MCU family | ESP32 / ESP32-S3 / ESP32-P4 (ESP-IDF) / STM32 (HAL, LL, Zephyr) / RP2040, RP2350 (Pico SDK) / nRF52, nRF54 (Zephyr) / other | the one the team already knows |
| 10b | Firmware language | **C11** (C++17 only where an SDK needs it) / C++17 / an interpreter (MicroPython, CircuitPython) | C11. Say why: the control core must compile unchanged for the board AND for the digital twin, run at a fixed rate with bounded memory, and be host-tested; an interpreter on the target can do none of that. Python stays for tools, CAD and the Isaac Sim harness. Respect an informed "I want MicroPython" and record that the twin then cannot run the firmware itself |
| 11 | Main supply and the worst transient on it | battery chemistry and cell count, adapter voltage, motor back-EMF | answer needed; it sets every derating row |
| 12 | Board class and coating | IPC Class 2 (commercial) / Class 3 (high reliability); conformal coat yes / no | Class 2, coated when the enclosure is vented |

## Batch 4: simulation and the machine

| # | Question | Options | Recommended default |
|---|---|---|---|
| 13 | Simulation tiers wanted | **native twin + Unreal** / native twin only / native twin + Isaac Sim / all three | native twin + Unreal when there is a GPU (Unreal 5.8.4 or newer: deterministic replay, Chaos coupling, the best visuals, a mature C++ host); native twin only without one; Isaac Sim when the team already knows it or needs its robot and sensor libraries. The native twin always comes first and stays the CI tier |
| 14 | GPU (for Isaac / Unreal / ML) | model and VRAM, or none | none |
| 14b | Is the GPU shared (other people's jobs, an ML job, games)? | no / yes, and whose jobs have priority | yes, other jobs have priority (the gate then refuses to start while it sees them) |
| 15 | Machine limits | CPU cap for starting jobs / RAM to keep free for the system / free disk floor | 90 % / 4 GB / 50 GB |
| 16 | Archive location for big results | a large drive path (videos and sim outputs are never deleted) | a folder on the largest non-system drive |
| 16b | Storage layout | **repo on an NVMe / SSD, `.archive` a junction or symlink to the big disk** / everything on one disk | the recommended layout: builds, CAD and the engines are I/O bound, and a complex project collects hundreds of GB of traces, renders, videos and datasets in a few weeks. Make the link at once: `mklink /J .archive "<archive path>"` (Windows cmd) or `ln -s <archive path> .archive` |

## Batch 5: how to run the agents (state the cost before asking)

| # | Question | Options | Recommended default |
|---|---|---|---|
| 17 | Mode | **single session** (one agent works step by step, no subagents, long jobs run detached; cheapest) / **swarm** (a lead plus parallel lanes in worktrees; fastest wall-clock, a swarm day has burned 4-5 million tokens) | single session |
| 18 | Usage tripwire | weekly percentage at which every agent stops and writes a resume document | 95 % |
| 19 | Standing decisions already made | anything the agents must never re-ask (materials, connectors, voltages) | none |

## Batch 6: the build spec (what the hardware must survive; each answer is a PROJECT decision)

| # | Question | Options | Recommended default |
|---|---|---|---|
| 20 | Environment | indoor lab / indoor product / outdoor / marine (salt spray, ASTM B117) / vehicle / airborne | indoor product |
| 21 | Temperature range, operating and storage | 0..40 degC / -20..60 / -40..85 / -55..125 | 0..40 operating, -20..60 storage |
| 22 | Ingress and cooling | open / IP54 splash / IP67 immersion / IP68 (IEC 60529); cooling: passive vented, forced air, sealed with a conduction path, liquid | IP54, passive vented, for an indoor product |
| 23 | Standards it is built to | commercial / IPC-A-610 class 2 or 3 / MIL-STD-810H (environment) / MIL-STD-461G (EMI) / automotive (AEC-Q parts) / medical / per subsystem | IPC class 2, commercial |
| 24 | Wire and harness | PVC hook-up / silicone / SAE AS22759 fluoropolymer ("Tefzel", former MIL-W-22759) / UL 1007, 1015; crimp workmanship IPC/WHMA-A-620 | silicone for moving harnesses, PVC elsewhere; AS22759 when the spec says MIL or high temperature |
| 25 | EMI / EMC | none required / shielded cables and a ground plan / an FCC part 15 or CISPR 32 class / MIL-STD-461G | shielded motor and long sensor cables, a single-point ground plan |
| 26 | Vibration and shock | bench only / handled and dropped / vehicle / rotating machinery | handled and dropped (1 m onto concrete) |
| 27 | Design headroom (derating) | margins on voltage, current, power and temperature for every part | 50 % voltage, 70 % current, 50 % power, junctions 20 K below their maximum at the hottest ambient |

These feed `DECISIONS.md`, the derating table of `pcb/DESIGN_STANDARDS.md`, the enclosure rules and the twin's
thermal limits and ambient range (the `hot_ambient` style scenarios run at the top of row 21).

## Batch 7: how the owner likes to work (simulations, the machine, overnight)

| # | Question | Options | Recommended default |
|---|---|---|---|
| 28 | Long simulations (seed sweeps, Unreal or Isaac runs, Monte Carlo) | **detached in the background at below-normal priority, with a slot and a timeout** / only overnight / only when I start them | background, detached, below normal, one heavy job at a time: the PC stays usable and a crashed job never takes the session down |
| 29 | Overnight runs | **yes: a night chain (40-seed matrix, sweeps, renders) plus morning questions** / no | yes, proposed each evening; the night runs on recommended defaults and records every default taken |
| 30 | Engines while you are at the machine | Unreal / Isaac may start any time / only when I say / only overnight | only when you say, or overnight: an engine launch takes the GPU and many GB of RAM |
| 31 | How results reach you | in the session / a short morning report / a notification on failure | in the session, plus a morning report after a night chain |
| 32 | Questions while you are away | batch them in `QUESTIONS.md` and take the recommended default at a deadline / stop and wait | batch with a deadline; defaults become decisions |

## answers.json

`scripts/scaffold.py` validates this shape; every key without "(optional)" is required.

| Key | Type | From question |
|---|---|---|
| `project_name` | text | 1 |
| `project_summary` | text, one paragraph | 1 |
| `goals` | list of text | 2 |
| `acceptance` | list of text, each a check a gate or bench can run | 1, 2 |
| `budget` | text | 3 |
| `quantity` | text | 4 |
| `material_default` | text | 5 |
| `fastening` | text | 6 |
| `enclosure` | text | 7 |
| `fab_vendor` | text | 9 |
| `mcu_family` | text | 10 |
| `sim_tiers` | text; name `Unreal` and / or `Isaac` literally to get `sim/unreal` / `sim/isaac` (the scaffold matches those words and prints the tier folders it skipped) | 13 |
| `gpu` (optional) | text | 14 |
| `mode` | `"single"` or `"swarm"` | 17 |
| `archive_path` | text | 16 |
| `cpu_max_pct` | whole number 10..100 | 15 |
| `ram_reserve_gb` | whole number 1..512 | 15 |
| `disk_free_min_gb` | whole number | 15 |
| `handoff_pct` | whole number 50..101 (101 = never) | 18 |
| `decisions` (optional) | list of text | 8, 11, 12, 19 and every default taken |
| `firmware_language` (optional) | text | 10b |
| `build_spec` (optional) | list of text, one per answered row | 20-27 |
| `work_style` (optional) | list of text | 14b, 28-32 |
| `storage` (optional) | text | 16b |

Answers to questions 8, 11 and 12 go into `decisions` and into the decision table of `pcb/DESIGN_STANDARDS.md`.
`scripts/scaffold.py` turns `decisions`, `build_spec`, `work_style`, `firmware_language` and `storage` into the
numbered rows (`D-001` ...) of `DECISIONS.md`; the build spec also fills `REQUIREMENTS.md` section 1b.

The shape, shown with an illustrative project (a bench power supply), not a real one:

```json
{
  "project_name": "Bench supply",
  "project_summary": "A two-channel 0-30 V / 3 A bench supply with USB control and a sealed front panel.",
  "goals": ["Both channels regulate within 10 mV under a 0-3 A step", "USB control with a documented protocol"],
  "acceptance": ["Host suite green in CI", "Twin step-load scenario within 10 mV", "Board ERC 0/0, DRC 0/0/0/0"],
  "budget": "800 USD for the first build",
  "quantity": "5",
  "material_default": "PETG",
  "fastening": "brass heat-set inserts with thread locker",
  "enclosure": "vented",
  "fab_vendor": "JLCPCB",
  "mcu_family": "STM32G4",
  "sim_tiers": "native twin + Unreal",
  "mode": "single",
  "archive_path": "D:/archive/bench_supply",
  "cpu_max_pct": 90,
  "ram_reserve_gb": 4,
  "disk_free_min_gb": 50,
  "handoff_pct": 95,
  "decisions": ["Clearance 1.0 mm static, 3.0 mm moving", "IPC Class 2, no coating"],
  "firmware_language": "C11 (STM32 LL)",
  "build_spec": ["Indoor product, 0..40 degC operating", "IP54 vented, passive cooling", "Derating 50 % V, 70 % I"],
  "work_style": ["Sweeps detached in the background, below normal; overnight chain proposed each evening"],
  "storage": "repo on the NVMe, .archive junction to D:/archive/bench_supply"
}
```
