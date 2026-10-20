# Stack profile: custom

Architect uses this file to describe an engine that has no ready-made profile. The
result is `Tech/STACK.md` and a filled-in `studio.json` with the same structure as the
`godot4`, `unity` and `web-phaser` profiles.

## When to choose

The user picked an engine or framework without a ready-made profile. Examples:
- **Defold**: Lua; build with `bob.jar`; Lua tests (for example telescope); collections
  are text files.
- **LÖVE**: Lua; run with `love .`; tests with busted; screenshots with
  `love.graphics.captureScreenshot`.
- **Bevy**: Rust/ECS; `cargo run` and `cargo test`; logic lives in systems that can be
  tested without a window.
- **MonoGame**: C#/.NET; `dotnet build` and `dotnet test`; MGCB builds content.

The commands in these lines are hints (unverified). Check each one against the docs
through Context7.

## What architect must find out

1. How the engine is installed and how to check its version. The user installs it.
2. How to run, build and test the game with one command from the repo root, with a
   non-zero exit code on failure.
3. How to capture a scene screenshot without a human, with `{scene}` and `{out}`
   placeholders and a fixed seed. If it is impossible without a window, say so.
4. Which files the engine generates itself: caches and import metadata. Caches go to
   `ignore`; files generated next to assets go to `shared_zones`.
5. Where the engine loads assets from, and how to load the `pixelart.py` atlas
   (`image`, `frameWidth`, `frameHeight`, `frames`, `animations`, `origin`), WAV, level
   JSON from `level_preview.py export` and `<language>.json` strings.
6. How to keep pure logic separate from the engine, so qa's acceptance tests use the
   public contract without a window.

## studio.json fragment (stub)

```json
{
  "stack": "custom",
  "paths": {
    "art_export": "<engine sprite folder>",
    "audio_export": "<sound folder>",
    "levels_export": "<levels folder>",
    "strings_export": "<strings folder>",
    "code": ["<sources>/**"],
    "unit_tests": ["<developer tests>/**"],
    "acceptance_tests": ["<qa acceptance tests>/**"],
    "project_files": ["<project manifest>", ".gitignore"]
  },
  "commands": {
    "setup": "<install project dependencies>",
    "run": "<run the game>",
    "build": "<build>",
    "test": "<unit tests, exit code != 0 on failure>",
    "acceptance": "<acceptance tests>",
    "capture": "<screenshot: {scene} → {out}, fixed seed>",
    "import_assets": "<asset import, if the engine needs it>"
  }
}
```

An empty command is allowed. Then `Tech/STACK.md` explains why it is missing and what
qa does instead.

## Project layout

Same rules as the ready-made profiles:
- logic is a separate module with no engine dependencies;
- the engine layer is thin: rendering, input, sound;
- tests live in two folders: unit (developer) and acceptance (qa);
- the screenshot script is part of the project code.

## Project skeleton

The developer `WORK` task in sprint 1, following `Tech/STACK.md`: a minimal scene, a
test runner with one passing test and the capture script. Check: run `setup`, `test`
and `capture`, attach the output and the PNG.

## Tests

qa writes acceptance tests against the tech plan contract before the code. A test
creates the logic with a fixed seed, feeds input and checks numbers from the AC. If the
engine's test runner cannot work without a window, a plain language runner tests the
logic (busted, `cargo test`, `dotnet test`).

## Screenshots

Screenshots feed the art-director review and game-designer acceptance. Options from best
to worst:
1. The engine's "save frame" API plus command-line arguments, as in `godot4`.
2. A web build captured with Playwright, as in `web-phaser`.
3. An OS window screenshot (`screencapture -l <id>` on macOS, unverified). This is the
   last resort: it is fragile.

## Asset import

In `Tech/STACK.md`, add a table "studio format → how the engine loads it" for atlases,
WAV, levels and strings. The loading code is the first `INTEGRATE` task.

## Pitfalls

- Engine-generated metadata without a `shared_zones` rule causes false zone violations.
- Engines with binary scenes suit agents poorly. Build scenes in code.
- If the engine cannot run without a window, screenshots and checks need an open
  window. Warn the user at the gate.
