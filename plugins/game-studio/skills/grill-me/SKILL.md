---
name: grill-me
description: A relentless round-based interview that takes an idea, plan or design to a shared understanding before any work starts. Questions follow a decision tree and every question carries a recommended answer. Use when the user asks to be grilled or to stress-test a plan, and when the game-studio skill runs its kickoff or a sprint gate.
---

# grill-me

Adapted from the `grilling` skill in [mattpocock/skills](https://github.com/mattpocock/skills)
(MIT, © Matt Pocock). The license is in the repository's `THIRD_PARTY_NOTICES.md`.

Interview the user in the user's language until you reach a shared understanding. Keep
a **decision tree** in mind: every decision branches into the decisions that hang off
it.

## Rounds

Work in rounds. The **frontier** is every decision whose prerequisites are already
settled: the questions you can ask now without guessing answers you have not heard yet.
One round asks the whole frontier at once. Number the questions and give a recommended
answer for each. Then wait for the answers.

Round format:

```
❓ **Q1** — **<question title>**: <the question, with options if needed>

➡️ <recommended answer and why, in one or two sentences>

---

❓ **Q2** — …
```

Word each question so that "yes" accepts the recommendation. "Yes to all" accepts every
recommendation in the round.

Each answer reshapes the tree. Settled decisions push the frontier outward and unblock
the questions that depended on them. Recompute the frontier and ask the next round. A
question that depends on another question still open in this round belongs to a later
round.

## Facts and decisions

Finding facts is your job, never the user's. When a frontier question needs a fact
about the environment (files, installed programs, versions), find it yourself or send a
subagent. Do not block on it: an open fact is an unsettled prerequisite, so only the
questions that depend on it wait; ask the rest of the frontier now.

Decisions belong to the user. Put each one to the user and wait for the answer.

## Recording

Inside `game-studio`, record every settled decision in `Studio/decisions.md`:

```
- D-<N>: <decision> (user | brief | recommendation | autopilot)
```

Tree nodes come from `Studio/kickoff/design-tree.*.md`. At a sprint gate the questions
come from `Studio/questions.md`; remove a question from it once it is settled.

Outside the studio the interview writes nothing. Its result is a clear decision in the
user's head.

## Ending

The interview is over when the frontier is empty: every branch has been visited and
nothing was assumed silently. Do not start the work until the user confirms the shared
understanding.

If a round needs something tangible ("what will it look like?"), say so and offer a
throwaway prototype or a style probe before the next round.
