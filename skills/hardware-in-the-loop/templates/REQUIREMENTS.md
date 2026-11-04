# {{PROJECT_NAME}}: requirements and definition of done

Status: living document, written from the onboarding interview on {{DATE}}. The single source of truth for "what must
be true for this project to be done". Every lane works against it; the lead reconciles lane output against it.

## 0. The goal

{{PROJECT_SUMMARY}}

{{GOALS}}

## 1. Constraints

| Item | Value |
|---|---|
| Budget | {{BUDGET}} |
| Build quantity | {{QUANTITY}} |
| Default material for printed parts | {{MATERIAL_DEFAULT}} |
| Fastening | {{FASTENING}} |
| Enclosure | {{ENCLOSURE}} |
| PCB fab and assembly | {{FAB_VENDOR}} |
| MCU family | {{MCU_FAMILY}} |
| Simulation tiers | {{SIM_TIERS}} |
| Languages | {{FIRMWARE_LANGUAGE}} |

## 1b. Build spec (environment, standards, headroom)

{{BUILD_SPEC}}

Each line is a decision in `DECISIONS.md`; the derating table of `pcb/DESIGN_STANDARDS.md`, the thermal limits
of the twin's scenarios and the enclosure rules all derive from it.

## 2. Hard rules (binding for every lane)

- Real logic in the loop: a scenario that passes in the twin runs the real firmware control code through the real
  interface; anything that cannot be closed without hardware is listed as hardware-pending.
- 1:1 simulation: the twin and every sim read the CAD exports; no hand-copied geometry or mass.
- No unfinished work presented as finished (audit gate), no fake data, no stand-in geometry named as a part.
- Protection: no one-time fuses; an eFuse with its fault and power-good lines read by firmware, else a PTC with a sense
  line; two contacts per power and ground net on every connector; TVS on connector pins; components derated
  (`pcb/DESIGN_STANDARDS.md`).
- Every number in a report comes with the command, commit and date that produced it.
- Every model, constant and algorithm cites a verified source (`CITATION.md`, `[REF-nnn]` in the code).
- Firmware is C (or C++ where an SDK needs it), never an interpreter on the target: the same sources compile for
  the board and for the twin.

## 3. Acceptance checklist

The interview answers set the first version of this list; every line is a check a gate or a bench can run.

{{ACCEPTANCE}}
