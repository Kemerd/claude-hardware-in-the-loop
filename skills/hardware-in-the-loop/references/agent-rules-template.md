# Agent rules: how to adapt the template

The rules file every agent reads is `templates/harness/swarm/AGENT_RULES.md`; `scripts/scaffold.py` copies it to
`harness/swarm/AGENT_RULES.md` with the machine limits filled in. It is live (ignored by git), so the lead can update it
during a run without a commit, and every agent reads the current version.

## What the lead adds during a run

1. **Standing owner decisions**, numbered and dated, one row each, in the table at the end of the file. A lane that
   would re-ask a settled question reads the answer here instead.
2. **Incident rules**: when something goes wrong once (a runaway process, a lost night of jobs, a corrupted file),
   add a short dated section with the symptom in one sentence and the rule in one sentence. Examples of the shape:

   ```markdown
   ## Detached chains (<date>)
   Children started with nohup from Git Bash died when the session ended and a night of jobs was lost.
   Launch every long chain with harness/swarm/detach.sh and wait on its done / FAILED marker.
   ```

3. **Files never to touch**: the owner's untracked files, private folders, anything that must not leave the machine.

## What never goes in

- Credentials, personal data, private paths that would be wrong on another machine.
- Rules that contradict `CODING_RULES.md`: fix one of the two files instead.
- Project-wide technical decisions that belong in `REQUIREMENTS.md` or `pcb/DESIGN_STANDARDS.md` (link to them).
