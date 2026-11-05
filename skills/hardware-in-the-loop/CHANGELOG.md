# Changelog

Newest first. Every change to `skills/hardware-in-the-loop/` that a scaffolded project would want bumps `VERSION` and
adds a section here, written for the project owner: what gets better for them, in plain words, one bullet per item.
`templates/tools/hil_update.py` shows these sections to the owner when upstream is ahead of their project.

## 1.1.0 - 2026-11-05
- **Stay current.** Projects now carry a version stamp (`harness/TEMPLATE_VERSION.json`) and a `tools/hil_update.py`.
  At session start the agent checks the method repository, tells you what is new, and on a yes pulls it in: new files
  are added, files you never touched are refreshed, files you customised are merged by hand with a diff, never
  overwritten.
- Agent orientation page gains a section on updating (README_AGENT.md, section 7).

## 1.0.0 - 2026-10-24
- First stamped release: generator-first KiCad boards with stage layout, parametric CadQuery CAD, C firmware host-tested
  against a native digital twin (Unreal / Isaac hosts), web CAD viewer, CI matrix with audits, swarm / chain / single
  modes with handoffs.
