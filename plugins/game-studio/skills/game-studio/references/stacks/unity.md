# Stack profile: unity

Unity 6 (LTS), C#, Unity Test Framework (NUnit), batchmode from the CLI. Agents cannot
use the Inspector. So asset references are wired in code through `Resources`, and
scenes are built by Editor scripts.

## When to choose

- The team or the user knows Unity, or console/mobile export is needed.
- 3D or complex 2D with ready-made packages from the Unity ecosystem.
- Unity is installed and licensed (Personal is enough).

## Install and check versions

```sh
ls /Applications/Unity/Hub/Editor/                    # installed versions
export UNITY_EDITOR="/Applications/Unity/Hub/Editor/<version>/Unity.app/Contents/MacOS/Unity"
"$UNITY_EDITOR" -version                              # (unverified)
```

The user installs Unity and the license through Unity Hub. Batchmode does not start
without an activated license.

## studio.json fragment

```json
{
  "stack": "unity",
  "paths": {
    "art_export": "Assets/Resources/Sprites",
    "audio_export": "Assets/Resources/Audio",
    "levels_export": "Assets/Resources/Levels",
    "strings_export": "Assets/Resources/Strings",
    "code": ["Assets/Scripts/**", "Assets/Editor/**", "Assets/Scenes/**", "Assets/Tests/Capture/**",
             "ProjectSettings/**"],
    "unit_tests": ["Assets/Tests/EditMode/**"],
    "acceptance_tests": ["Assets/Tests/Acceptance/**"],
    "project_files": ["Packages/manifest.json", "Packages/packages-lock.json", ".gitignore"]
  },
  "commands": {
    "setup": "\"$UNITY_EDITOR\" -batchmode -quit -projectPath . -logFile -",
    "run": "\"$UNITY_EDITOR\" -projectPath .",
    "build": "\"$UNITY_EDITOR\" -batchmode -quit -projectPath . -executeMethod Studio.EditorTools.BuildTool.Build -logFile -",
    "test": "\"$UNITY_EDITOR\" -batchmode -projectPath . -runTests -testPlatform EditMode -assemblyNames \"Tests.EditMode\" -testResults Logs/test-results/editmode.xml -logFile -",
    "acceptance": "\"$UNITY_EDITOR\" -batchmode -projectPath . -runTests -testPlatform EditMode -assemblyNames \"Tests.Acceptance\" -testResults Logs/test-results/acceptance.xml -logFile -",
    "capture": "CAPTURE_SCENE={scene} CAPTURE_OUT=\"$PWD/{out}\" CAPTURE_SEED=42 \"$UNITY_EDITOR\" -batchmode -projectPath . -runTests -testPlatform PlayMode -testCategory Capture -testResults Logs/test-results/capture.xml -logFile -",
    "import_assets": "\"$UNITY_EDITOR\" -batchmode -quit -projectPath . -logFile -"
  }
}
```

- Add `**/*.meta` to `shared_zones`: any Unity run creates `.meta` files, including
  qa's test runs.
- `ignore` already has `Library`, `Temp`, `Obj`, `Logs`, `UserSettings` by default.
- Do not add `-quit` to `-runTests`: the runner closes the editor itself after the run
  (unverified).
- Test results go to `Logs/` so a developer test run does not write into qa's zone.

## Project layout

```
Assets/Scripts/Logic/     plain C# (no MonoBehaviour): rules, state, RNG — Game.Logic.asmdef
Assets/Scripts/Game/      MonoBehaviour: rendering, input, sound — Game.asmdef
Assets/Editor/            BuildTool, SceneBuilder, pixel-art import
Assets/Scenes/Main.unity  created by SceneBuilder, never edited by hand
Assets/Resources/Sprites|Audio|Levels|Strings
Assets/Tests/EditMode/    Tests.EditMode.asmdef (developer)
Assets/Tests/Acceptance/  Tests.Acceptance.asmdef, [Category("Acceptance")] (qa)
Assets/Tests/Capture/     Tests.Capture.asmdef, PlayMode, [Category("Capture")]
```

## Project skeleton

This is the developer `WORK` task in sprint 1:
1. `"$UNITY_EDITOR" -batchmode -quit -createProject "$PWD/.unity-tmp"`, then move
   `Assets/`, `Packages/` and `ProjectSettings/` to the repo root. Creating the project
   directly in a non-empty folder is (unverified).
2. Add `com.unity.nuget.newtonsoft-json` to `Packages/manifest.json`: `JsonUtility`
   cannot read dictionaries, and atlases and strings are dictionaries.
   `com.unity.test-framework` is already part of the template.
3. Create asmdef files:
   - `Game.Logic`, without UnityEngine references where possible;
   - `Tests.EditMode` and `Tests.Acceptance`: `includePlatforms: ["Editor"]`,
     `references: ["Game.Logic", "UnityEngine.TestRunner", "UnityEditor.TestRunner"]`,
     `precompiledReferences: ["nunit.framework.dll"]`,
     `defineConstraints: ["UNITY_INCLUDE_TESTS"]`;
   - `Tests.Capture`: no `includePlatforms`, it is PlayMode.
4. Write `Assets/Editor/SceneBuilder.cs`, `BuildTool.cs`, the capture test and
   `Assets/Editor/PixelArtImport.cs`. The last one is an `AssetPostprocessor`: for
   `Assets/Resources/Sprites` it sets `filterMode = Point` and
   `textureCompression = Uncompressed`.
5. Check: `setup`, `test` (0 tests), `capture`.

Unity import creates `.meta` files and GUIDs. Nobody writes or copies them by hand.

## Tests

- Unit (developer): `Assets/Tests/EditMode`, tests of `Game.Logic`.
- Acceptance (qa) follows the tech plan contract and is written before the code.
  Usually EditMode: the public logic API and numbers from the AC.

```csharp
using NUnit.Framework;
using Game.Logic;

[Category("Acceptance")]
public class F001ShipTests
{
    [Test]
    public void Ac2_CollisionTakesOneLife()
    {
        var game = new GameState(seed: 1);
        game.SpawnAsteroidAt(game.ShipPosition);
        game.Step(0.016f);
        Assert.AreEqual(2, game.Lives);
    }
}
```

Scene-level AC are checked by a PlayMode test: `[UnityTest]` with `IEnumerator`,
loading the scene and waiting for frames.

## Screenshots

The PlayMode test `Assets/Tests/Capture/CaptureTest.cs` renders a camera frame into a
RenderTexture. `CAPTURE_SCENE` is a scene name from Build Settings.

```csharp
using System.Collections;
using System.IO;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

public class CaptureTest
{
    [UnityTest, Category("Capture")]
    public IEnumerator Capture()
    {
        var scene = System.Environment.GetEnvironmentVariable("CAPTURE_SCENE") ?? "Main";
        var output = System.Environment.GetEnvironmentVariable("CAPTURE_OUT") ?? "capture.png";
        Random.InitState(int.Parse(System.Environment.GetEnvironmentVariable("CAPTURE_SEED") ?? "42"));
        SceneManager.LoadScene(scene);
        for (var frame = 0; frame < 30; frame++) yield return null;
        yield return new WaitForEndOfFrame();

        var camera = Camera.main;
        var target = new RenderTexture(1280, 720, 24);
        camera.targetTexture = target;
        camera.Render();
        RenderTexture.active = target;
        var image = new Texture2D(target.width, target.height, TextureFormat.RGBA32, false);
        image.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0);
        image.Apply();
        camera.targetTexture = null;
        RenderTexture.active = null;

        Directory.CreateDirectory(Path.GetDirectoryName(output));
        File.WriteAllBytes(output, image.EncodeToPNG());
    }
}
```

- Never capture with `-nographics`: no frame is rendered at all.
- In URP, `RenderPipeline.SubmitRenderRequest` is recommended over `camera.Render()`.
  If the frame is black, check the pipeline and try without `-batchmode` (unverified).
- The scene must be in Build Settings; `SceneBuilder` adds it.

## Asset import

After copying files, developer runs `import_assets`. Unity creates `.meta` files; they
are committed.
- Sprites: the `<name>.atlas.json` atlas (`image`, `frameWidth`, `frameHeight`,
  `frames`, `animations`, `origin`). Sprites are created at runtime, not sliced in the
  importer. Unity's origin is bottom-left, so `y` is flipped:

```csharp
var texture = Resources.Load<Texture2D>("Sprites/ship");
var atlas = JsonConvert.DeserializeObject<StudioAtlas>(Resources.Load<TextAsset>("Sprites/ship.atlas").text);
foreach (var (name, r) in atlas.Frames)
    sprites[name] = Sprite.Create(texture, new Rect(r.X, texture.height - r.Y - r.H, r.W, r.H),
                                  new Vector2(0.5f, 0.5f), pixelsPerUnit: atlas.FrameWidth);
```

  `Resources.Load` finds `ship.atlas.json` by the name `"Sprites/ship.atlas"` because
  the extension is dropped.
- Sound: `Resources.Load<AudioClip>("Audio/<key>")`.
- Levels: `Resources.Load<TextAsset>("Levels/L01")` plus Newtonsoft. Take the schema
  from the actual output of `level_preview.py export`.
- Strings: `Resources.Load<TextAsset>("Strings/<language>")` →
  `Dictionary<string, string>`.

## Pitfalls

- Batchmode does not start while the same project is open in the editor: the project is
  locked.
- A lost or hand-copied `.meta` breaks references and duplicates GUIDs.
- Scenes and prefabs in YAML are never edited by hand. Only `SceneBuilder` through
  `-executeMethod`.
- `Resources` goes into the build in full. Fine for small games; bigger ones need
  Addressables (architect decides).
- The first `setup` is slow: Unity builds `Library/`.
- Logs go to stdout with `-logFile -`. `-runTests` exits non-zero on failures.
