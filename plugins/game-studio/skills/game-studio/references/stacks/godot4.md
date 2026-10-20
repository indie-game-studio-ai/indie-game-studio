# Stack profile: godot4

Godot 4.x (docs checked against 4.5), GDScript, GUT for tests, screenshots through an
autoload script. gdUnit4 is an alternative to GUT, but its CLI is not described here
(unverified).

## When to choose

- 2D or light 3D for desktop and mobile, with native export.
- The user wants an engine with an editor and no licensing.

## Install and check versions

```sh
godot --version            # or the binary: /Applications/Godot.app/Contents/MacOS/Godot
export GODOT=/Applications/Godot.app/Contents/MacOS/Godot   # if godot is not on PATH
```

The user installs the engine; agents do not. GUT is a project addon: unpack the release
from github.com/bitwes/Gut that matches the Godot version into `addons/gut/`.

## studio.json fragment

```json
{
  "stack": "godot4",
  "paths": {
    "art_export": "assets/sprites",
    "audio_export": "assets/audio",
    "levels_export": "assets/levels",
    "strings_export": "assets/strings",
    "code": ["scenes/**", "scripts/**", "autoload/**", "addons/**"],
    "unit_tests": ["tests/unit/**"],
    "acceptance_tests": ["tests/acceptance/**"],
    "project_files": ["project.godot", "export_presets.cfg", ".gutconfig.json", "icon.svg",
                      ".gitignore", "**/.gdignore"]
  },
  "commands": {
    "setup": "\"${GODOT:-godot}\" --headless --path . --import",
    "run": "\"${GODOT:-godot}\" --path .",
    "build": "\"${GODOT:-godot}\" --headless --path . --export-release \"macOS\" build/game.zip",
    "test": "\"${GODOT:-godot}\" --headless --path . --import && \"${GODOT:-godot}\" --headless --path . -s addons/gut/gut_cmdln.gd -gdir=res://tests/unit -ginclude_subdirs -gexit",
    "acceptance": "\"${GODOT:-godot}\" --headless --path . --import && \"${GODOT:-godot}\" --headless --path . -s addons/gut/gut_cmdln.gd -gdir=res://tests/acceptance -ginclude_subdirs -gexit",
    "capture": "\"${GODOT:-godot}\" --path . --resolution 1280x720 --fixed-fps 60 -- --capture-scene={scene} --capture-out={out} --capture-seed=42 --capture-frames=30",
    "import_assets": "\"${GODOT:-godot}\" --headless --path . --import"
  }
}
```

- Add `.godot`, `build` and `addons/gut` to `ignore`: the addon is third-party and
  needs no zone check.
- Add `**/*.import` and `**/*.uid` to `shared_zones`. Any engine run creates them,
  including qa's test runs. Without this rule the zone check reports a violation.
- `build` needs export templates and a "macOS" preset in `export_presets.cfg`. Without
  the preset, skip the build and say so.

## Project layout

```
project.godot
scenes/main.tscn       minimal scene: root node and script
scripts/logic/         pure logic (RefCounted, no nodes) — tests target this
scripts/game/          nodes: rendering, input, sound
autoload/capture.gd    screenshot from command-line arguments
autoload/game_config.gd  seed and shared RNG
tests/unit/, tests/acceptance/   test_*.gd, extends GutTest
assets/sprites|audio|levels|strings
```

Put an empty `.gdignore` into every non-game folder (`Studio`, `Design`, `References`,
`Tech`, `QA`, `Art`, `Audio`, `Levels`, `Narrative`). Otherwise Godot imports
screenshots and sources and litters the project with `.import` files.

## Project skeleton

This is the developer `WORK` task in sprint 1:
1. Write `project.godot` by hand; it is a short INI:
   ```ini
   config_version=5
   [application]
   config/name="Game"
   run/main_scene="res://scenes/main.tscn"
   [autoload]
   GameConfig="*res://autoload/game_config.gd"
   Capture="*res://autoload/capture.gd"
   [display]
   window/size/viewport_width=640
   window/size/viewport_height=360
   window/stretch/mode="viewport"
   [rendering]
   textures/canvas_textures/default_texture_filter=0
   ```
   `default_texture_filter=0` means Nearest, which pixel art needs.
2. `scenes/main.tscn` stays minimal: `[gd_scene format=3]`, an `ext_resource` with the
   script and a root `Node2D`. The rest of the node tree is built in `_ready()` in code.
3. Unpack GUT into `addons/gut/` and create `.gutconfig.json`:
   `{"dirs": ["res://tests/unit/"], "include_subdirs": true, "prefix": "test_",
   "suffix": ".gd", "should_exit": true}`.
4. `.gdignore` in non-game folders, `.gitignore` with `.godot/` and `build/`.
5. Check: `setup`, then `test` (0 tests is not an error), then `capture`.

## Tests

GUT in Godot 4 runs without a GUI, so `--headless` works. Exit code: 0 means all tests
passed, 1 means failures.

- Unit (developer): logic from `scripts/logic/`.
- Acceptance (qa) follows the tech plan contract and is written before the code. The
  test creates the logic object and checks AC with numbers:

```gdscript
extends GutTest

func test_ac2_collision_takes_one_life():
	var game := preload("res://scripts/logic/game_state.gd").new(1)  # seed from the contract
	game.spawn_asteroid_at(game.ship_position)
	game.step(0.016)
	assert_eq(game.lives, 2)
```

JUnit XML: `"junit_xml_file": "user://results.xml"` in `.gutconfig.json`.

## Screenshots

`autoload/capture.gd` acts only when `--capture-out` is passed:

```gdscript
extends Node

func _ready() -> void:
	var options := _user_options()
	if not options.has("capture-out"):
		return
	seed(int(options.get("capture-seed", "42")))
	if options.has("capture-scene"):
		var scene: String = options["capture-scene"]
		if not scene.begins_with("res://"):
			scene = "res://scenes/%s.tscn" % scene
		get_tree().change_scene_to_file.call_deferred(scene)
	for i in int(options.get("capture-frames", "30")):
		await get_tree().process_frame
	await RenderingServer.frame_post_draw
	var out: String = options["capture-out"]
	if not out.is_absolute_path():
		out = ProjectSettings.globalize_path("res://" + out)
	DirAccess.make_dir_recursive_absolute(out.get_base_dir())
	var error := get_viewport().get_texture().get_image().save_png(out)
	get_tree().quit(0 if error == OK else 1)

func _user_options() -> Dictionary:
	var options := {}
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--") and "=" in arg:
			var pair := arg.substr(2).split("=", true, 1)
			options[pair[0]] = pair[1]
	return options
```

Capturing needs rendering, so the command has no `--headless`: rendering is disabled
there. On macOS a window opens for a second. Without a display (Linux CI), use
`xvfb-run` (unverified). The game takes its seed from `GameConfig`, not from the global
`randi()`.

## Asset import

After copying PNG, WAV or JSON, developer runs `import_assets`. `.import` and `.uid`
files are committed, `.godot/` is not.
- Sprites: the `<name>.atlas.json` atlas (`image`, `frameWidth`, `frameHeight`,
  `frames`, `animations`, `origin`) becomes `SpriteFrames` in code:

```gdscript
static func build_frames(atlas_path: String) -> SpriteFrames:
	var atlas: Dictionary = JSON.parse_string(FileAccess.get_file_as_string(atlas_path))
	var texture: Texture2D = load(atlas_path.get_base_dir().path_join(atlas["image"]))
	var frames := SpriteFrames.new()
	for name in atlas.get("animations", {}):
		var anim: Dictionary = atlas["animations"][name]
		frames.add_animation(name)
		frames.set_animation_speed(name, anim.get("fps", 8))
		frames.set_animation_loop(name, anim.get("loop", true))
		for frame_name in anim["frames"]:
			var r: Dictionary = atlas["frames"][frame_name]
			var region := AtlasTexture.new()
			region.atlas = texture
			region.region = Rect2(r["x"], r["y"], r["w"], r["h"])
			frames.add_frame(name, region)
	return frames
```

- Sound: `load("res://assets/audio/<key>.wav")` → `AudioStreamPlayer`.
- Levels: JSON from `level_preview.py export`, read with `FileAccess` and
  `JSON.parse_string`. Take the schema from the tool's actual output.
- Strings: `assets/strings/<language>.json` → a dictionary and a `tr_key(key)` helper.
  `Narrative/` is under `.gdignore`, so narrative-designer copies strings to
  `assets/strings`.

## Pitfalls

- Do not hand-edit `.tscn` beyond the minimum: resource ids and `uid` break. Build the
  node tree in code.
- A `.uid` file sits next to each script (Godot 4.4+). Move it together with the
  script, or `uid://` references break.
- Assets the editor has not seen are not imported. `load()` in a test fails until
  `--import` has run.
- `get_texture()` returns a black image in the first frame. Wait for `frame_post_draw`.
- An open editor and a CLI import on the same project race for `.godot/`.
