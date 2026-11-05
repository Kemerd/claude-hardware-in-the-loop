# Swarm orchestration (and the cheaper single-session alternative)

> **Cost first.** A swarm day on the project this method comes from burned **4-5 million tokens**. Most of it was not
> the work itself: it was waking lane agents that each carried 300-800k tokens of context, again and again, to read a
> message, check a log or answer a question. After that day the owner cancelled swarms, and the default became a **chain**:
> one long lead session that hands one bounded card at a time to one subagent and waits for the report (section 6). Use
> the swarm when wall-clock time matters more than tokens and the work splits into lanes that rarely need each other.

## 1. Roles and models

| Role | Model (as used by the owner) | Does |
|---|---|---|
| Lead / orchestrator | Claude Fable | owns the plan, writes briefs, batches owner questions, reads reports, decides; does little hands-on work |
| Lane lead | Opus 5.5 | owns a lane's files and acceptance numbers; spawns its own workers |
| Worker, subtle | Opus 5.5 | control laws, physics, geometry, signal integrity, anything where a plausible wrong answer is expensive |
| Worker, mechanical | Sonnet 5.5 | ports, codecs, renders, test scaffolds, docs, inventories, moving files by explicit instruction |
| Skeptic | Opus 5.5 (fresh context) | reviews one lane's deliverable from the diff and the report, re-runs gates, never sees the lead's opinion |
| Integrator | the lead, or one named lane | lands lanes on dev under MERGE_LOCK, runs the full matrix, promotes dev to main |

Give subagents the same discipline you work under: exact files, numbers, report shape. A brief you cannot write
precisely is a sign the lane is not ready.

## 2. Waves

```
 wave 1  recon      several agents, read-only, write verified-fact files with file:line anchors
 wave 2  plan       the lead writes PLAN.md (teams, ownership, interfaces, acceptance) + briefs.md; owner questions batched
 wave 3  lanes      lane agents in their worktrees; Sonnet for mechanical work, Opus for subtle work
 wave 4  skeptics   one per finished lane: audit --changed-since dev, gates re-run, three numbers spot-checked
 wave 5  integrate  every lane lands on dev (red included); full CI with -Compare; promote dev -> main when 0 regressions
```

### Wave 1: recon

Each recon agent gets a scope that does not overlap the others and returns a file `harness/swarm/recon_<area>.md`
holding VERIFIED facts only: each with a `file:line` anchor, a command and its output, or a datasheet section. No
plans, no opinions. The plan is only as good as these files; the lane agents later read them instead of re-discovering.

### Wave 2: the plan and the briefs

`PLAN.md` (template `references/plan-template.md`) holds:

- **Teams / lanes**: branch, worktree, owned files, goal, acceptance numbers with the command that proves each, model.
- **File ownership**: one owner per file. A lane that needs a change in someone else's file writes a patch to
  `harness/swarm/handoff/<from>_to_<to>_<topic>.patch` and tells the lead; the owner applies it.
- **Interface additions**: every new or changed contract (a struct, a JSON key, a pin) with its producer and consumers.
- **Owner questions**: batched, recommended default first; without an answer the default is taken and written down.

`briefs.md` holds one addendum per lane (template `references/brief-template.md`): exact files, numbers, hand-offs in
and out, what to return. The return value is the structured report (`references/structured-report.md`): branch,
commits, files, tests before / after with commands, numbers vs acceptance, open items, interface deviations, hand-offs
made, hardware-pending items, whether the worktree can be removed.

### Wave 3: lanes

Lanes work in `.claude/worktrees/<name>` on `swarm/<name>`, created from `origin/dev`. They:

- push after every green gate (`wip_backup.sh` is insurance, not a substitute),
- take a slot for every heavy job and run long chains detached with a watcher,
- append one line per milestone to the board log (PLAN section 6) with SHA and numbers,
- never touch files they do not own,
- stop and write a resume document when the handoff hook fires.

### Wave 4: skeptics

A skeptic gets the lane's report and the diff, not the lead's view. It runs
`python -I tools/audit_placeholders.py --changed-since origin/dev --fail-on-new`, the lane's CI groups, and checks
three numbers from the report against their commands. Verdict: green, or red with the failing evidence.

### Wave 5: integration

The integrator lands every lane on dev (`merge_to_dev.sh`, red lanes too, with their gate note), runs the full matrix
on dev with `-Compare <last main report> -FailOn regressions`, and promotes (`promote_dev.sh`) only with zero
regressions. Regressions become the first lane of the next wave.

## 3. Coordination files (all in `harness/swarm/`, ignored)

| File | Purpose |
|---|---|
| `PLAN.md` | the run's single source of truth; "Where I stopped" at the bottom |
| `briefs.md` | per-lane addenda |
| `recon_*.md` | verified facts |
| `AGENT_RULES.md` | binding rules + standing owner decisions |
| `BOARD.md` or PLAN section 6 | milestone log |
| `handoff/` | cross-lane patches |
| `resume_<date>/` | resume documents, named date-time first |
| `MERGE_LOCK/` | the merge lock (exists only while held) |
| `locks/` | machine slots, the queue, the meter |
| `HANDOFF_NOW` | the usage tripwire flag |
| `machine.env` | CPU cap, RAM reserve, disk floor, handoff threshold |

## 4. Usage tripwire and handoffs

`usage_statusline.py` (status line) watches the weekly utilisation and creates `HANDOFF_NOW` at the threshold.
`handoff_hook.sh` (PostToolUse) then injects the stop-and-resume instruction (`handoff_hook.json`) into every agent's
next turn. Each agent: lets nothing new start, stops its own jobs by exact PID, commits and pushes its branch, releases
slots and locks, writes `resume_<date>/RESUME_<date_time>_<lane>.md` (template `references/resume-doc.md`), returns the
path. The lead writes its own resume document with every lane's state and the relaunch order. The flag is removed by
hand after the reset. Prove the wiring before a run: `touch harness/swarm/HANDOFF_SELFTEST`, call any tool in the lead
session, see "hook is live", remove the file.

## 5. Overnight

See `references/overnight-questions.md`: a detached night chain with one slot and a timeout per step, and a batched
list of morning questions with context, options, the recommended default and what was assumed meanwhile.

## 6. The chain (the default)

One long lead session, one subagent at a time. A subagent run in sequence is not a swarm, and it does save tokens: the
file dumps, build logs and trial and error stay in its context, and the lead's context takes in only the report. On
the source project two such cards used 509k and 678k tokens of their own, and each came back to the lead as a short
summary. Run in the lead, all of that would have stayed in the context every later message pays for.

| Role | Model | Does |
|---|---|---|
| Lead | Fable or Opus | owns the plan, the decisions and the owner; does small jobs itself; writes each brief; checks each report |
| Subagent, hard card | Opus | one bounded card: CAD, routing, control, anything where a plausible wrong answer is expensive |
| Subagent, mechanical card | Sonnet | one bounded card: ports, renders, tests, docs |

Rules:

1. **One subagent at a time.** The lead waits for the notification, or works on files the card does not touch. Machine
   limits then stay simple too: at 100 % CPU a swarm's agents spent 30-40 minutes queueing for a slot.
2. **A card is bounded.** One outcome, the files to read, acceptance numbers, what to leave alone, where the report goes.
   If you cannot write the brief, you do not understand the task yet.
3. **A fresh agent per card.** Never wake an old giant to ask it a small thing. That is what burned the swarm day.
4. **The lead does a job itself when it is a handful of tool calls.** A card costs more than it looks: it knows only what
   the brief says, and you see only the summary.
5. **Check the report.** Read at least one number back from the files before the card counts as done. An agent's mistakes
   arrive in the same confident tone as its findings.
6. **Tell the owner what is running and why.** "What are those agents doing" should have a one-line answer.

Plan template: `references/chain-plan.md`.

## 6b. The single-session alternative

One session, no subagents:

1. Write the plan as numbered steps, each with the gate that proves it (`references/single-session-plan.md`).
2. Work one step at a time in one worktree; commit, push and land on dev after every green gate.
3. Every long machine job (build, CI, routing, render, sim) goes through `detach.sh` with a `watch.sh` background task,
   and the session works on the next step meanwhile.
4. Owner questions are batched at the start of the session and whenever a batch of four has piled up.
5. When the session must stop, append "## Where I stopped" (done with SHAs, running jobs with PIDs and logs, the exact
   next step). The next session reads it first.

It is slower in wall-clock time and much cheaper: one context, warmed once, doing the work itself.

## 7. Failure modes seen in practice

| Symptom | Cause | Rule |
|---|---|---|
| a night of jobs gone in the morning | `nohup` children died with the session | `detach.sh` + markers + `watch.sh` |
| lanes ended their turn on a crashed build | no watcher, so nothing woke them | every background job gets a watcher for success AND failure |
| 5 routers at 100 % CPU with 1 slot held | jobs started without the gate | every heavy job through `slot.sh`, pid recorded |
| an orphan `find /` at full CPU for hours | a search over a drive root | never search from a root; `rg --files -g` with a known parent |
| a slot queue frozen while the box idled | waiting takes whose callers had died | takes give up when their caller pid is gone |
| a waiting big job starved for an hour | small jobs kept passing it | starvation guard in the fairness queue |
| disk full at 99 worktrees | each worktree is a full checkout with build output | check disk first; remove merged, clean worktrees after archiving |
| one lane's build broke another's link | running another lane's binaries in place | copy binaries into your own `.tmp` run folder |
