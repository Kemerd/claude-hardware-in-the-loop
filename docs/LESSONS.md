# Hard-won lessons

Each lesson is one sentence of symptom and one sentence of rule. Every one of them cost at least an afternoon.

## Machine and processes

1. **A night of jobs vanished.** `nohup ... & disown` children started from Git Bash die when the agent session ends, and Git Bash has no `setsid`. **Rule:** launch long chains with `detach.sh` (PowerShell `Start-Process -WindowStyle Hidden`) and wait on `done` / `FAILED` markers.
2. **Lanes ended their turn on a crashed build and nobody noticed for hours.** Nothing woke them on failure. **Rule:** every background job gets a watcher that wakes the agent on success AND on failure (Traceback, assert, non-zero exit, timeout, no log growth).
3. **The machine sat at 100 % CPU for four hours.** An orphaned `find /` searched the whole drive (and `find.exe` was in every crash dump that week). **Rule:** never search from a drive root or across a whole repo; use `rg --files -g <name> <dir>`, `git ls-files`, or a listing of a known parent, with a timeout.
4. **Five routers ran at full CPU while only one held a slot.** Jobs started without passing the gate. **Rule:** every heavy job takes a slot, records its pid, and releases it in a trap.
5. **Twelve sims passed the gate together and pinned the box.** They all read the same idle CPU figure. **Rule:** one successful take per start gap, and RAM promised to recent takes counts as used.
6. **The slot queue stood still while the box idled at 13 % CPU.** Every waiting take belonged to an agent whose tool call had timed out. **Rule:** a waiting take re-checks its caller pid and gives up when the caller is gone.
7. **A big job waited over an hour while small jobs kept passing it.** Fairness alone favours what fits now. **Rule:** a ticket older than the starvation limit holds younger takes back until it fits (bounded by size so an impossible ticket cannot stall everyone).
8. **A second Unreal started while the first was "held".** The slot recorded the pid of a subshell that had already exited. **Rule:** record the caller's long-lived pid (or the chain's own `$$`), mapped to a Windows pid when running under Git Bash.
9. **The disk filled up.** Ninety-nine worktrees at several GB each, plus their build output. **Rule:** check free space before builds and new worktrees; archive and remove merged, clean worktrees by name.
10. **A background CI job hung forever inside PowerShell `Start-Job`.** The child process inherited the job's protocol pipe as stdin and blocked at start-up. **Rule:** CI runners start children with stdin redirected and closed.
11. **One lane's build broke another lane's link step.** It ran the other lane's binary in place and the file stayed locked. **Rule:** copy binaries into your own `.tmp` run folder or build your own.
12. **Two simulated stations died at start-up.** A run used a private port registry and grabbed a port another run held. **Rule:** all parallel runs share one slot registry for ports.
13. **A process the owner was using got killed.** An agent cleaned up "stray" processes by name. **Rule:** never kill a process you did not start; stop your own by exact PID only.

## KiCad and boards

14. **`FootprintLoad` started returning bare `SwigPyObject`s halfway through a run.** `Remove()` had freed a C++ object under a live SWIG wrapper and corrupted KiCad's heap. **Rule:** never `Remove()` footprint items; move unwanted graphics to a fab layer, and do router-remnant clean-up as the last pcbnew mutation after every footprint load.
15. **A headless board run hung without output.** A KiCad assert dialog (a via width queried without its layer) waited for a click nobody could give. **Rule:** pass the layer argument to layer-dependent getters, and treat 10 minutes without log growth as hung.
16. **The same generator gave a different board on every run.** Python's hash seed changed set order, and KiCad's UUIDs were random. **Rule:** re-exec with `PYTHONHASHSEED=0`, seed the KIID generator, use uuid5 for schematic UUIDs.
17. **Freerouting gave a different (equally clean) route each run, even on one thread.** The router is not repeatable. **Rule:** freeze a good route (`<board>_route.ses`, committed) and re-route only on purpose.
18. **Gerber hashes changed on every export of an identical board.** KiCad writes the export time into the headers. **Rule:** record both the raw SHA-256 and a content hash that skips the time-stamp lines.
19. **The assembler placed the wrong part.** A hand-typed distributor code looked right but resolved to a different MPN (a suggested 0402 resistor code was in fact an 0603 part). **Rule:** verify every purchased line live: the code must resolve to the MPN the schematic names, a mismatch fails the build, stock 0 is a warning.
20. **A board passed DRC with a broken rule.** Someone had demoted the rule's severity in the project file. **Rule:** DRC runs at KiCad's default severities; a rule is fixed in copper or relaxed with a cited basis, never demoted.
21. **DRC flagged parity errors no one could see in the GUI.** Pin names in the symbol did not match pad numbers in a stock footprint (two tabs both numbered "MP"). **Rule:** generate symbols from the datasheet pin table AND check them against the footprint's real pad numbers.
22. **A custom footprint fit the reduced test board but not the real part.** It was drawn from a stand-in with fewer pins. **Rule:** custom footprints come from the vendor's land-pattern drawing of the exact part.
23. **The auto-placer looped for hours without converging.** Critical parts need judgement it does not have. **Rule:** the agent places power stages, connectors, fine-pitch fan-outs and current paths by hand with reasoned coordinates in the generator, then lets the tools finish.
24. **Routing kept failing on a dense 4-layer board.** The fix was two more layers, which also cost money. **Rule:** more layers are an allowed cause-level fix after hand placement; report the fab price delta with it.
25. **Every commit churned KiCad files nobody changed.** `*.kicad_prl` holds per-user view state. **Rule:** never commit `*.kicad_prl`.
26. **A blown fuse looked like a dead bus to the firmware.** One-time fuses hide their state. **Rule:** no one-time fuses; an eFuse with FLT / PG read by firmware, else a PTC with a sense line.

## Agents, tokens and process

27. **A swarm day burned 4-5 million tokens.** Waking 300-800k-context agents again and again cost more than the work. **Rule:** default to one session with no subagents and detached jobs; use the swarm deliberately.
28. **The next session re-did work the last one finished.** There was no record of where it stopped. **Rule:** append "Where I stopped" to the plan and write resume documents sorted by date; read them first.
29. **The weekly usage limit cut lanes off mid-edit.** Nothing told them to save. **Rule:** a status-line tripwire creates `HANDOFF_NOW`; a PostToolUse hook tells every agent to commit, push and write its resume document.
30. **Commits existed only on one machine when it crashed.** Lanes committed but did not push. **Rule:** push after every green gate; snapshot uncommitted work every 20 minutes to backup branches.
31. **A lane's work was lost in a stash.** The stash stack is shared by every worktree. **Rule:** never `git stash` in the shared checkout; never edit the owner's main checkout.
32. **Lanes kept asking the owner the same question.** Decisions lived in chat history. **Rule:** settled decisions go into the standing-decisions table of `AGENT_RULES.md`; open ones are batched with the recommended default first.
33. **A gate turned green because the test had been disabled.** Pressure to finish. **Rule:** never lower a bar, add a waiver, or disable a test to get green; waivers need a reason and an owner and are reviewed.
34. **A suite looked green after it crashed.** The runner trusted exit code 0. **Rule:** a pass needs exit 0 AND the suite's summary line AND zero failures; an empty suite is a failure.
35. **A "verified" claim in a lane report was wrong.** The reviewing agent had been told the lane's conclusion. **Rule:** skeptics get the diff and the report, not the lead's opinion, and re-run the commands.
36. **A script crashed only on the owner's console.** A part name with a non-ASCII character hit cp1252. **Rule:** every Python script reconfigures stdout to UTF-8 with `errors="replace"`.
37. **A script worked only from one directory.** It used paths relative to the working directory. **Rule:** resolve repo paths from `__file__` / `$0` / `$PSScriptRoot`; the portability check enforces it.
38. **Root folder clutter hid real files.** Stray traces and exports piled up at the root. **Rule:** only the seven root files; everything else is filed into a tree, scratch in `.tmp/`, results in `docs/data/`.
39. **A protected board was not protected.** The example's TVS (6.67 V breakdown, 10.3 V clamp) sat in front of an LDO rated 6.5 V absolute maximum; every gate was green because a written rule ("clamp below the downstream absolute maximum") had no machine check. Pointed out by a reader. **Rule:** a rule marked "machine" has a check in the pipeline or is marked roadmap; parts declare their datasheet ratings, and TVS-02 fails any pin behind a clamp that it cannot survive.
40. **The sensor was on the wrong part.** The first twin run of a five-minute overload: the firmware derated on the motor-case NTC and the winding still reached 132 degC (class B is 130). **Rule:** protect the hottest point, not the measured one; estimate what you cannot measure (a winding observer from the datasheet thermal resistance) and let a twin scenario prove the limit holds.

41. **An auto-router ran for an hour getting worse.** Conflicts fell from 500 to 189, then rose to 521, and the count gave no reason. One render of the board on a grid showed connectors in the middle of it. **Rule:** set a stop rule before routing, and when a router stalls stop it, render the board, read the picture, and place by hand. Details in PCB.md, "When the router gets stuck".
42. **"No subagents" threw away a real saving.** The single-session rule was a reaction to swarms, and the lead then carried every log and file dump itself. One subagent at a time, on a bounded card, keeps that out of the lead's context. **Rule:** the default is a chain: one long lead, one fresh subagent per card, in sequence. The swarm is the expensive part, not the subagent.
