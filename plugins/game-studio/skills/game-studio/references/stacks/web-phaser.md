# Stack profile: web-phaser

Vite + TypeScript + Phaser, Vitest for game logic, Playwright for screenshots and e2e.
Current versions at the time of writing: phaser 4.2, vite 8.3, vitest 5.0,
@playwright/test 1.64, typescript 7.0. Phaser 4 keeps the Phaser 3 API for loading,
scenes and animations. The renderer, tint, FX/masks and shaders changed, and the Canvas
renderer is deprecated.

## When to choose

- A 2D game for the browser, a Telegram Mini App or itch.io.
- You need a fast loop: everything is verified locally without installing an engine.
- The default when the kickoff decisions name no other stack.

## Install and check versions

```sh
node -v          # node 20+
npm -v
npm view phaser version
npx playwright --version   # after npm install
```

Nothing is installed globally. `npx playwright install chromium` downloads the
Playwright browser into the user cache.

## studio.json fragment

```json
{
  "stack": "web-phaser",
  "paths": {
    "art_export": "public/assets/sprites",
    "audio_export": "public/assets/audio",
    "levels_export": "public/assets/levels",
    "strings_export": "Narrative/strings",
    "code": ["src/**", "index.html", "tools/capture.mjs"],
    "unit_tests": ["tests/unit/**"],
    "acceptance_tests": ["tests/acceptance/**"],
    "project_files": ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts",
                      "vitest.config.ts", "playwright.config.ts", ".gitignore"]
  },
  "commands": {
    "setup": "npm install && npx playwright install chromium",
    "run": "npm run dev",
    "build": "npm run build",
    "test": "npm test",
    "acceptance": "npm run test:acceptance",
    "capture": "node tools/capture.mjs --scene {scene} --out {out}",
    "import_assets": ""
  }
}
```

Add `test-results` and `playwright-report` to `ignore`. Strings stay in
`Narrative/strings/<language>.json` and are imported as a JSON module, so no copy is
needed.

## Project layout

```
index.html
src/main.ts            Phaser config, parses ?scene=&seed=&capture=
src/logic/             pure logic without Phaser: rules, state, RNG — tests target this
src/scenes/            Phaser scenes: rendering, input, sound only
src/assets/atlas.ts    registers pixelart.py atlases
src/testBridge.ts      window.__game for e2e
tests/unit/            Vitest, written by developer
tests/acceptance/logic/*.test.ts   Vitest, written by qa
tests/acceptance/e2e/*.spec.ts     Playwright, written by qa
tools/capture.mjs
```

## Project skeleton

This is the developer `WORK` task in sprint 1:
1. `npm create vite@latest . -- --template vanilla-ts`. If the folder is not empty,
   scaffold in a temp folder and move the needed files.
2. `npm i phaser` and `npm i -D vitest @playwright/test`.
3. Scripts in `package.json`:
   `"dev": "vite"`, `"build": "tsc --noEmit && vite build"`,
   `"test": "vitest run tests/unit"`,
   `"test:acceptance": "vitest run tests/acceptance/logic && playwright test"`.
4. `vitest.config.ts`: `test.environment: 'node'`, `include: ['tests/**/*.test.ts']`.
5. `playwright.config.ts`: `testDir: 'tests/acceptance/e2e'`,
   `webServer: { command: 'npm run dev -- --port 5199 --strictPort', url:
   'http://localhost:5199', reuseExistingServer: !process.env.CI }`, same `use.baseURL`.
6. `src/main.ts`, a stub scene, `src/testBridge.ts`, `tools/capture.mjs` (below).
7. Check: `npm run build`, `npm test`, capture of the main scene.

Determinism. `?seed=N` seeds the RNG in `src/logic` (for example mulberry32) and
`Phaser.Math.RND.sow([String(N)])`. `Math.random()` is forbidden in logic.

## Tests

- Unit (developer): logic from `src/logic` in Node, without Phaser.
- Acceptance (qa) follows the contract in `Tech/plans/F-NNN.md` and is written before
  the code:
  - `tests/acceptance/logic/F-NNN.test.ts` imports the public logic API, for example
    `createGame({ seed })` or `step(state, input)`, and checks AC with numbers from the
    spec;
  - `tests/acceptance/e2e/F-NNN.spec.ts` opens `/?scene=<Scene>&seed=1`, waits for
    `window.__game.ready` and checks state through the bridge:

```ts
import { test, expect } from '@playwright/test';

test('AC-2: hitting an asteroid costs one life', async ({ page }) => {
  await page.goto('/?scene=Play&seed=1');
  await page.waitForFunction(() => (window as any).__game?.ready === true);
  await page.evaluate(() => (window as any).__game.debug.spawnAsteroidAtShip());
  await page.waitForFunction(() => (window as any).__game.state().lives === 2);
});
```

The shape of `window.__game` (`ready`, `state()`, `debug.*`) is part of the tech plan
contract. Tests never reach into Phaser internals.

## Screenshots

Once the target frame is drawn, the scene sets `window.__captureReady = true`, for
example in `update()` after 30 frames when the URL has `capture=1`.

`tools/capture.mjs`:

```js
import { createServer } from 'vite';
import { chromium } from 'playwright';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { parseArgs } from 'node:util';

const { values: { scene, out, seed } } = parseArgs({
  options: {
    scene: { type: 'string', default: 'Boot' },
    out: { type: 'string', default: 'QA/screenshots/capture.png' },
    seed: { type: 'string', default: '42' },
  },
});

const server = await createServer({ server: { port: 5198, strictPort: false }, logLevel: 'error' });
await server.listen();
const baseUrl = server.resolvedUrls.local[0];
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
  await page.goto(`${baseUrl}?scene=${scene}&seed=${seed}&capture=1`);
  await page.waitForFunction(() => window.__captureReady === true, null, { timeout: 15000 });
  mkdirSync(dirname(out), { recursive: true });
  await page.locator('canvas').screenshot({ path: out });
  console.log(`capture: ${out}`);
} finally {
  await browser.close();
  await server.close();
}
```

Command: `node tools/capture.mjs --scene Play --out QA/screenshots/S1/play.png`.

## Asset import

- Sprites: `pixelart.py render` writes `<name>.png` and `<name>.atlas.json` to
  `public/assets/sprites/`. Atlas:
  `{"image", "frameWidth", "frameHeight", "frames": {name: {x,y,w,h}}, "animations":
  {name: {"frames": [names], "fps"}}, "origin": [x,y]}`. Registration:

```ts
// preload: this.load.image(key, `assets/sprites/${key}.png`); this.load.json(`${key}:atlas`, `assets/sprites/${key}.atlas.json`);
export function registerAtlas(scene: Phaser.Scene, key: string): void {
  const atlas = scene.cache.json.get(`${key}:atlas`);
  const texture = scene.textures.get(key);
  for (const [name, r] of Object.entries<any>(atlas.frames)) texture.add(name, 0, r.x, r.y, r.w, r.h);
  for (const [name, anim] of Object.entries<any>(atlas.animations ?? {})) {
    scene.anims.create({
      key: `${key}:${name}`,
      frames: anim.frames.map((frame: string) => ({ key, frame })),
      frameRate: anim.fps,
      repeat: anim.loop === false ? 0 : -1,
    });
  }
}
```

  `Texture.add` exists in Phaser 3; in Phaser 4 it is (unverified). If the atlas
  format changed, check it against the actual JSON from `pixelart.py`.
- Sound: WAV from `public/assets/audio/`, `this.load.audio(key, 'assets/audio/<key>.wav')`.
- Levels: JSON from `level_preview.py export` in `public/assets/levels/`,
  `this.load.json('L01', 'assets/levels/L01.json')`. Take the schema (`grid`, `tiles`,
  `markers`) from the tool's actual output.
- Strings: `import en from '../../Narrative/strings/en.json'` (the file name is
  `studio.json → language`), a `t(key)` helper that falls back to the key.
- Pixel art: `pixelArt: true` in the game config and an integer camera zoom.

## Pitfalls

- Logic inside Phaser scenes cannot be tested. Keep everything an AC checks in
  `src/logic`.
- `Math.random()` and `Date.now()` in logic break test and screenshot determinism.
- Phaser animations are global: `anims.create` with the same key in a second scene only
  warns. Register them once in Boot/Preload.
- Sound does not play before the first user gesture (browser autoplay policy).
- A busy port 5199 breaks `--strictPort`; capture picks a free port.
- Paths under `public/` are written without `public/`: `assets/sprites/x.png`.
