# Rules for agents in this repository

This repository is the studio plugin, not a game. `CLAUDE.md` is a symlink to this file.

## Where things are

- `plugins/game-studio/skills/game-studio/SKILL.md` — the producer (orchestrator).
- `plugins/game-studio/skills/game-studio/references/protocol.md` — the contract between
  the producer and the roles: tasks, modes, summaries, zones, working language.
- `roles/*.md` — the source of truth for the roles. `roles/_common.md` is appended to
  every role.
- `plugins/game-studio/agents/*.md` and `codex/agents/*.toml` are **generated** by
  `scripts/build.py`. Never edit them by hand.
- `plugins/game-studio/skills/game-studio/tools/` — tools, Python stdlib only.
- `plugins/game-studio/skills/game-studio/templates/` — what `studio_init.py` puts into
  a game project.

## How to change things

1. To change a role, edit `roles/<role>.md`, then run `python3 scripts/build.py`.
2. To change the protocol (modes, statuses, task or summary format), update
   `protocol.md`, `SKILL.md`, the roles and `board.py`/`zones.py` together. Otherwise
   the roles and the producer drift apart.
3. Tools are Python 3.9+, stdlib only, with English `--help`. New behaviour comes with a
   test in `tests/`.
4. Before committing: `python3 scripts/build.py --check` and
   `python3 -m unittest discover -s tests -t .`.

## Style

- Everything in the repository is in English. The studio's working language is chosen
  at runtime from the user's language (`studio.json → language`); do not hard-code
  another language into prompts or tools.
- Short sentences, one thought each.
- A code comment explains why, never restates the code.
- Do not commit or push unless the owner asks. No Co-Authored-By trailers.
