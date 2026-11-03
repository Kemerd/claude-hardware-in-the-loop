# Onboarding interview

Ask these in batches through the question dialog (at most four questions per batch), each with the recommended default
first. When the owner does not answer a question, take the default and record it in the plan as "default taken".
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
| 10 | MCU family | ESP32 (ESP-IDF) / STM32 / RP2040 / nRF52 / other | the one the team already knows |
| 11 | Main supply and the worst transient on it | battery chemistry and cell count, adapter voltage, motor back-EMF | answer needed; it sets every derating row |
| 12 | Board class and coating | IPC Class 2 (commercial) / Class 3 (high reliability); conformal coat yes / no | Class 2, coated when the enclosure is vented |

## Batch 4: simulation and the machine

| # | Question | Options | Recommended default |
|---|---|---|---|
| 13 | Simulation tiers wanted | native twin only / + Isaac Sim / + Unreal visual twin | native twin only (the others are a roadmap until the twin is green) |
| 14 | GPU (for Isaac / Unreal / ML) | model and VRAM, or none | none |
| 15 | Machine limits | CPU cap for starting jobs / RAM to keep free for the system / free disk floor | 90 % / 4 GB / 50 GB |
| 16 | Archive location for big results | a large drive path (videos and sim outputs are never deleted) | a folder on the largest non-system drive |

## Batch 5: how to run the agents (state the cost before asking)

| # | Question | Options | Recommended default |
|---|---|---|---|
| 17 | Mode | **single session** (one agent works step by step, no subagents, long jobs run detached; cheapest) / **swarm** (a lead plus parallel lanes in worktrees; fastest wall-clock, a swarm day has burned 4-5 million tokens) | single session |
| 18 | Usage tripwire | weekly percentage at which every agent stops and writes a resume document | 95 % |
| 19 | Standing decisions already made | anything the agents must never re-ask (materials, connectors, voltages) | none |

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
| `sim_tiers` | text | 13 |
| `gpu` (optional) | text | 14 |
| `mode` | `"single"` or `"swarm"` | 17 |
| `archive_path` | text | 16 |
| `cpu_max_pct` | whole number 10..100 | 15 |
| `ram_reserve_gb` | whole number 1..512 | 15 |
| `disk_free_min_gb` | whole number | 15 |
| `handoff_pct` | whole number 50..101 (101 = never) | 18 |
| `decisions` (optional) | list of text | 8, 11, 12, 19 and every default taken |

Answers to questions 8, 11 and 12 go into `decisions` and into the decision table of `pcb/DESIGN_STANDARDS.md`.

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
  "sim_tiers": "native twin",
  "mode": "single",
  "archive_path": "D:/archive/bench_supply",
  "cpu_max_pct": 90,
  "ram_reserve_gb": 4,
  "disk_free_min_gb": 50,
  "handoff_pct": 95,
  "decisions": ["Clearance 1.0 mm static, 3.0 mm moving", "IPC Class 2, no coating"]
}
```
