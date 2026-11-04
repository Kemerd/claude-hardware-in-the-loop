# Coding rules

These rules apply to every file in this repository, written by a person or by an agent. The gates in `windows/ci.ps1`
enforce the machine-checkable ones; reviewers (human or skeptic agent) enforce the rest.

## 1. Fix the cause, not the symptom

- Find out why something fails before changing anything. A retry loop around a crash, a widened tolerance, a demoted
  DRC rule or a disabled test is not a fix.
- If the cause is outside your task, write it down in your report (file, line, evidence) instead of papering over it.
- Never lower a gate bar to get green: no new waiver, no skipped suite, no relaxed rule without a cited basis
  (a standard, a datasheet table, a fab capability page) written next to the change.

## 2. Finished work only

- No open-work markers in shipped code, no functions that report success without doing the work, no hard-coded sensor
  values, no CAD blocks named after real parts. `tools/audit_placeholders.py` finds these; new findings fail the gate.
- A value that only a bench test can settle is a *design value* with a bench register id:
  `// design value, BENCH B-PWR-03` and a row in `docs/bench/BENCH_REGISTER.md` (test method + pass range).
- Something that genuinely needs hardware that does not exist yet is listed under "hardware-pending" in the report,
  never hidden behind a stub.
- Estimates are labelled as estimates, with how they were made.

## 3. Comments

- Comment heavily: a block header for every function and every non-trivial block, a comment every few lines inside
  long functions. Explain *why*, units, frames, and where a number comes from (datasheet section, standard table,
  measurement id).
- C and C++: Doxygen on every public function, struct and macro (`@brief`, `@param`, `@return`, `@note` for units
  and threading).
- Python: a module docstring with purpose, interpreter, usage; a docstring on every function.
- Comments read like an engineer wrote them. No chat, no apologies.

## 4. Defensive code

- Every input is suspect: check pointers, lengths, ranges, NaN and infinity, enum values, file existence, JSON shape.
  Fail loudly with a message that names the value and where it came from.
- Hardware access checks every return code. A bus timeout is an error path with a test, not an assumption.
- No silent fallbacks that change behaviour: if a default is used, log it.

## 5. Scripts (Python, PowerShell, bash)

- The header states the interpreter: an `Interpreter: ...` line (or `Run: <interpreter> ...`) in the first 60 lines.
- Python reconfigures stdout and stderr to UTF-8 with `errors="replace"` so a Windows console never crashes on a part
  name; it resolves repo paths from `__file__`, never from the working directory; it runs under `python -I`.
- PowerShell targets pwsh 7 and anchors on `$PSScriptRoot`. bash anchors on `$0`.
- No absolute user-profile paths anywhere. Tool caches, temp files and per-user tool settings are pointed into the
  repo's `.tmp/` or a tools folder outside the user profile.
- `tools/check_portability.py` enforces this list.

## 5b. Languages

- **Firmware is C** (C11; C++17 only where a vendor SDK or a library demands it, without exceptions or RTTI on the
  target). No MicroPython, CircuitPython or other interpreter in shipped firmware: the control core must compile
  unchanged for the board and for the twin, run at a fixed rate with bounded memory, and be testable on the PC.
- **The twin and the simulation hosts are C / C++** (C11 core, C++17 for engine glue). Unreal is C++.
- **Python is for offline tooling** (CAD builders, converters, plots, scripts, CI helpers) and for the Isaac Sim
  harness, which only moves data between PhysX and the C twin. No physics, sensor or control model is written in
  Python; if one is needed it is written in C and called.
- **CUDA** kernels share their body with a C reference (one header, compiled by both) and are checked against it.

## 5c. Sources

- Every model, equation, constant and algorithm names its source in a comment: `[REF-nnn]` for a row in
  `CITATION.md` (verified by opening it, never from memory), a datasheet table, or a BENCH id.

## 6. Determinism

- Generators (boards, CAD, sims) produce the same output from the same input: seeded randomness, sorted iteration,
  `PYTHONHASHSEED=0` where hash order leaks into output, stable UUIDs. Two runs, one file.
- The twin: one random stream per noise source (`tw_rng`), no wall-clock time, no fast-math, no FMA contraction;
  the same seed twice gives a byte-identical trace (CI suite `twin_determinism`).
- Results that go into a report record the command, the commit and the date.

## 7. Performance and the machine

- Think about what the CPU and memory are doing. Batch work, avoid quadratic scans of large trees, stream big files.
- Heavy jobs take a slot (`harness/swarm/locks/slot.sh`), run at below-normal priority with a timeout, and never
  touch processes they did not start.
- Never block an interactive thread with an expensive operation; long chains run detached with markers.

## 8. Git

- Small commits with a message that says what changed and why. Push after every green gate.
- No attribution lines, no generated-by footers. The machine's configured git identity is used.
- Never force-push. Never commit `*.kicad_prl`, files over 50 MB, secrets, or anything private.
