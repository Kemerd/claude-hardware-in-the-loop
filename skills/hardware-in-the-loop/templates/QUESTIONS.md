# Questions for the owner

Open questions live here, not in chat scrollback. An agent that needs a project decision writes the question here,
asks it through the question dialog (batched, at most four at a time, the recommended default first), and carries on
with other work. When the answer comes, or when the deadline passes and the default is taken, the question closes
and a row goes into `DECISIONS.md`.

## How to write a question

- **Id** `Q-nnn` (next free number, never reused).
- **The question** in one sentence a person can answer without opening the code.
- **Options**, with the recommended one first and marked, each with its consequence in numbers (cost, mass, time,
  margin): "PETG (recommended): 0 USD, creeps above 60 degC; PA12-CF: +40 USD per set, holds 120 degC".
- **Why it matters / what it blocks**: the plan step or lane that waits on it.
- **Deadline / default rule**: when the agent will take the default if nobody answers (overnight runs take it at
  once and record it).
- **Status**: `open` -> `answered` (with the answer) or `default taken`, and the `D-nnn` it became.

## Open

| Id | Asked | Question | Options (recommended first) | Blocks | Default by | Status |
|---|---|---|---|---|---|---|

## Closed

| Id | Asked | Question | Answer | Decision | Closed |
|---|---|---|---|---|---|
