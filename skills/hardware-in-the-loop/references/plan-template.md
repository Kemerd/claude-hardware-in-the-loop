# {{PROJECT_NAME}}: swarm plan (wave plan, written by the lead)

Mode: swarm. Started {{DATE}}. Token warning: a swarm day has burned 4-5 million tokens; most of it is waking agents
that carry 300-800k tokens of context. Keep lanes few, briefs exact, and stop lanes that have nothing left to do.

This file is the single source of truth for the run. The lead edits it; lanes read it. When the session has to stop,
the lead appends a "## Where I stopped" section at the bottom and the next session starts there.

## 0. Ground rules

`harness/swarm/AGENT_RULES.md` (binding), `CODING_RULES.md`, `REQUIREMENTS.md`. Machine limits: CPU cap {{CPU_MAX}} %,
{{RAM_RESERVE_GB}} GB RAM kept free, {{DISK_FREE_MIN_GB}} GB disk floor. Integrator: the lead (or the lane named here).

## 1. Recon (wave 1)

| Recon file | Scope | Agent | State |
|---|---|---|---|
| `harness/swarm/recon_<area>.md` | what to verify, with file:line anchors | model | open / done |

Recon agents write VERIFIED facts only, each with a file:line anchor, a command and its output, or a datasheet
section. Opinions and plans do not belong in recon.

## 2. Teams and lanes

| Lane | Branch / worktree | Owns (files) | Goal | Acceptance numbers | Model |
|---|---|---|---|---|---|

### File ownership rules

- One owner per file. A lane that needs a change in another lane's file writes a patch into
  `harness/swarm/handoff/<from>_to_<to>_<topic>.patch` with a one-line note and tells the lead.
- Shared interface files (`interfaces/`) change only through the "Interface additions" table below.

## 3. Interface additions

| # | Interface / file | Change | Producer lane | Consumer lanes | State |
|---|---|---|---|---|---|

## 4. Waves

| Wave | What | Starts when | Done when |
|---|---|---|---|
| 1 | recon | now | every recon file written and read by the lead |
| 2 | plan + briefs | recon done | this file and `briefs.md` complete; owner questions batched |
| 3 | lanes work | briefs out | every lane returned its structured report |
| 4 | skeptics | a lane reports done | skeptic verdict per lane (audit `--changed-since dev`, gates re-run) |
| 5 | integration | skeptic green or red-with-note | every lane on dev; dev CI green with 0 regressions vs main; promoted |

## 5. Owner questions (batched, recommended default first)

| # | Question | Options | Default | Answer / default taken |
|---|---|---|---|---|

## 6. Board log (milestones, newest at the bottom)

| Time | Lane | Event | SHA | Numbers |
|---|---|---|---|---|

## Where I stopped
