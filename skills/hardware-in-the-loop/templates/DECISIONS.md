# {{PROJECT_NAME}}: decisions

Every decision the owner made, numbered and dated, so no agent re-asks a settled question and every later change
can point at the decision it builds on or overturns. Started from the onboarding interview on {{DATE}}.

## Rules

1. **One row per decision**, the next number, the date, the decision in one sentence with its numbers, and where it
   came from (interview, a question in `QUESTIONS.md` by its id, a review, a bench result).
2. **Never edit or delete a row.** A changed decision is a NEW row that says `supersedes D-nnn`; the old row gets
   `superseded by D-mmm` appended, nothing else.
3. **A default taken is a decision too.** When a question got no answer and the recommended default was used, the
   row says `default taken (Q-nnn)` so the owner can overturn it later.
4. **Agents read this file first** (after `README_AGENT.md`). A plan step that contradicts a decision stops and
   asks; it never quietly works around it.
5. Project decisions an agent must never take on its own: materials, fastening, sealing, clearances, connectors,
   voltages, the build spec (environment, standards, derating headroom), anything that costs money or changes the
   bill of materials. Those always go through `QUESTIONS.md`.

## Decisions

| # | Date | Decision |
|---|---|---|
{{DECISIONS}}

<!-- Append below the table's last row: | D-nnn | YYYY-MM-DD | <decision> (source: <interview / Q-nnn / review>) | -->
