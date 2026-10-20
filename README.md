# Indie Game Studio

A skill for **Claude Code** and **Codex** that runs a team of subagents organized like an
indie studio. The team builds a game in sprints until the features are done.

You bring the idea. The producer grills you in rounds (grill-me style) and records the
decisions. Then the roles work on their own. The game designer writes features and
acceptance criteria. The artist draws sprites and keeps `References/Art TODO.md`; the
art director reviews the art. The architect writes the tech plan, QA writes tests before
the code, the developer implements. The game designer accepts the result. The producer
calls you at kickoff and after every sprint.

The studio works in **your language**. The producer detects it from your first message
and stores it in `Studio/studio.json → language`. Every document, report, review and
gate summary is written in that language. Prompts, templates and tools are in English.

```mermaid
flowchart TD
    U([Idea / brief]) --> G[Grill-me interview<br/>decision tree → decisions.md]
    G --> P[Pre-production<br/>GDD · stack · Art Bible · style probe]
    P --> G1{GATE 1<br/>user}
    G1 -->|approve| M[Sprint meeting<br/>specs → role replies → decisions]
    M --> B[(Task board<br/>Studio/tasks)]
    B --> W[Agent waves<br/>art · sound · text · levels · tech plan · tests · code]
    W --> R[Reviews<br/>art director · architect · game designer]
    R -->|changes| W
    R --> Q[QA: tests, build, screenshots]
    Q -->|bugs| W
    Q --> A[Feature acceptance<br/>game designer]
    A -->|rework| W
    A --> G2{Sprint GATE<br/>report + grilling on open questions}
    G2 -->|continue| M
    G2 -->|all MVP features accepted| F([Playable build])
```

## The team

| Role | Does | Checked by |
|---|---|---|
| Producer (the skill, main session) | interview, task board, agent waves, gates | you |
| Game designer | GDD, backlog, feature specs with AC, feature acceptance | you at the gates |
| Artist | mockups, pixel art and SVG, UI, `References/Art TODO.md` | art director |
| Art director | Art Bible, palette, review of art and screenshots (looks at the PNGs) | — |
| Architect | stack, `studio.json`, tech plan with a contract, code review | — |
| Developer | code from the tech plan, asset integration | architect, QA |
| QA | acceptance tests before code, verification, screenshots, bugs | — |
| Sound designer | procedural SFX and chiptune, `References/Audio TODO.md` | game designer |
| Narrative designer | story, dialogue, UI strings | game designer |
| Level designer | ASCII levels → JSON, previews, reachability | game designer |

## How roles hand work over

Roles never talk to each other directly. Everything goes through files in the game
project:

- **The board** — `Studio/tasks/T-NNN.md`. A task has an owner, dependencies, reviewers
  and a handoff log, for example `S1 artist → art-director: hero.png ready for review`.
  Only the producer writes it, through `board.py`.
- **Agent summary** — the lines `STATUS`, `HANDOFF: → role: what`, `NEW TASKS`,
  `QUESTIONS`. The producer moves them onto the board and decides who works next.
- **Meeting** — at the start of a sprint the game designer posts the specs. The other
  roles reply in parallel: what they need, risks, questions, tasks. The game designer
  answers; the producer writes the minutes and the tasks.
- **Personal TODO lists.** The artist keeps `References/Art TODO.md`: what to draw and
  redo from mockups and reviews. The sound designer keeps `References/Audio TODO.md`.

Guarantees:
- every role has its own write zone; after every wave the producer checks it with
  `zones.py verify`, and in Claude Code a hook also blocks writes outside the zone;
- QA's acceptance tests are locked by hashes;
- nobody reviews their own work.

The full contract is [`protocol.md`](plugins/game-studio/skills/game-studio/references/protocol.md).

## Installation

The repository is private. You need git access to it, for example via `gh auth login`.

### Claude Code

```
/plugin marketplace add lireirelico/indie-game-studio
/plugin install game-studio@indie-game-studio
```

Run it inside the game folder: `/game-studio:game-studio <game idea>`.

### Codex

```sh
git clone git@github.com:lireirelico/indie-game-studio.git ~/indie-game-studio
~/indie-game-studio/scripts/install-codex.sh --user            # for all projects
# or
~/indie-game-studio/scripts/install-codex.sh --project ~/my-game
```

The script symlinks the `game-studio` and `grill-me` skills into `.agents/skills/` and the
`studio-*.toml` agents into `.codex/agents/`. Update with `git pull`. Run it in a new Codex
session: `$game-studio <game idea>`.

## Running

```
/game-studio:game-studio A space shooter: a ship dodges asteroids
/game-studio:game-studio brief=docs/brief.md
/game-studio:game-studio brief=docs/brief.md autopilot
```

- Without arguments the producer resumes from the step recorded in `Studio/ledger.md`.
- `brief=<file>` — the interview asks only about what the brief does not cover.
- `autopilot` — gates accept the recommendations themselves; blockers still stop the
  work. For unattended runs.

## What appears in the game project

```
Studio/      board, producer log, decisions, meetings, reports, reviews, tools
Design/      GDD, backlog, feature specs
References/  Art Bible, palette.json, Art TODO, Audio TODO, mockups
Art/ Audio/ Narrative/ Levels/   asset sources and exports
Tech/        stack, architecture, tech plans
QA/          test plans, bugs, screenshots
```

The game code lives wherever the engine wants it; the architect sets the paths in
`Studio/studio.json`.

## Stack

The studio is not tied to an engine. The architect picks a profile at kickoff:

- **Godot 4** — GUT, headless import, viewport screenshots;
- **Unity** — Test Framework in batchmode, Editor scripts, RenderTexture capture;
- **web-phaser** — Vite + TypeScript + Phaser, Vitest, Playwright;
- **custom** — describe your own engine with the same structure.

The profiles live in [`references/stacks/`](plugins/game-studio/skills/game-studio/references/stacks/).

## Role tools

Plain Python 3 stdlib, nothing to install. They are copied into `Studio/tools/` at start:

| Tool | Purpose |
|---|---|
| `pixelart.py` | palette + ASCII frames → PNG, sprite sheet, atlas, ×N preview, palette check |
| `svg2png.py` | SVG → PNG through whatever renderer is found (resvg, rsvg-convert, inkscape, Chrome) |
| `contact_sheet.py` | a labelled grid of PNGs for review |
| `sfx.py` | procedural SFX (sfxr-like presets) → WAV, peak and clipping analysis |
| `chiptune.py` | text score → looping WAV |
| `level_preview.py` | ASCII level → PNG preview, marker and reachability check, JSON export |
| `board.py` | task board: create, statuses, reviews, ready tasks, BOARD.md |
| `zones.py` | role write zones, snapshots, test lock, hook |
| `studio_init.py` | lay out templates and tools in the project |

## Limitations

- Art is procedural: pixel art and SVG, no image generation. The Art Bible and the art
  director's reviews keep the style consistent.
- Nobody listens to the sound. It is checked by its parameters and WAV analysis.
- In Codex, write zones rely on instructions and the `zones.py verify` check after each
  wave. The Codex hook does not know which role is writing.
- The studio spends a lot of tokens. Limits: summaries of at most 15 lines, paths instead
  of contents, at most 4 agents per wave, 2 features per sprint, 3 rework rounds.

## Developing the studio

- Roles live in `roles/*.md`. `python3 scripts/build.py` generates the agents for Claude
  (`plugins/game-studio/agents/`) and Codex (`codex/agents/`); never edit those by hand.
- Tests: `python3 -m unittest discover -s tests -t .`.
- Rules for agents changing this repository are in [`AGENTS.md`](AGENTS.md).

MIT license. `grill-me` is adapted from the `grilling` skill in
[mattpocock/skills](https://github.com/mattpocock/skills) (MIT); see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).
