# {{PROJECT_NAME}}

{{PROJECT_SUMMARY}}

## Goals

{{GOALS}}

## How this repository works

- Hardware is generated, not hand-drawn: boards come from Python generators run under KiCad 10's python
  (`pcb/<board>/gen_<board>.py`), mechanical parts from parametric CadQuery builders (`cad/<assembly>/build_*.py`).
  Edit the generator, never its output.
- The firmware control code runs on the PC against a digital twin before it runs on hardware; the same scenarios run
  on the bench rigs.
- Every change passes the gates in `windows/ci.ps1` (audit, portability, ERC / DRC, host tests, twin scenarios).
- Work happens on branches: every lane lands on `dev`; `main` moves only when `dev` is green with zero regressions.

## Where to start

| You are | Read |
|---|---|
| a person | this file, then `REQUIREMENTS.md` and `PROJECT_STRUCTURE.md` |
| an agent | `README_AGENT.md` |
| writing code | `CODING_RULES.md` |
| designing a board | `pcb/DESIGN_STANDARDS.md` |

## Tools

KiCad 10 (with its bundled python and `kicad-cli`), Java 21 + Freerouting 2.1, a CadQuery virtual environment, Python 3
for the tooling, PowerShell 7 for CI. Install or verify them with the setup script of the hardware-in-the-loop skill
(`scripts/setup_tools.ps1`). No tool writes into the user profile; caches live under `.tmp/` or the tools folder.

## Licence

Choose your own licence for this project; nothing copied in here imposes one, and your boards, CAD, firmware and
products are yours to sell on any terms. The tooling the hardware-in-the-loop skill copied in (the scripts under
`harness/`, `tools/`, `windows/`, `pcb/tools/`, `cad/` and `firmware/host_tests/`) stays under HIL-ASA-1.0,
Copyright (c) 2026 Kemerd, full text at https://github.com/Kemerd/claude-hardware-in-the-loop/blob/main/LICENSE. Keep
this section (or move it to a NOTICE file) so that credit stays with it:

```
Based on claude-hardware-in-the-loop by Kemerd (https://github.com/Kemerd/claude-hardware-in-the-loop)
```
