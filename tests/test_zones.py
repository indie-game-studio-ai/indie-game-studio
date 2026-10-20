import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins" / "game-studio" / "skills" / "game-studio"
ZONES = SKILL / "tools" / "zones.py"
CONFIG_TEMPLATE = SKILL / "templates" / "Studio" / "studio.json"


class ZonesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve()
        (self.project / "Studio").mkdir()
        shutil.copy(CONFIG_TEMPLATE, self.project / "Studio" / "studio.json")
        self.write("src/main.ts", "export {}\n")
        self.write("Design/GDD.md", "# GDD\n")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text="x"):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def configure(self, **paths):
        config_path = self.project / "Studio" / "studio.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))
        config["paths"].update(paths)
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")

    def zones(self, *args, stdin=None, env=None, check=None):
        result = subprocess.run(
            [sys.executable, str(ZONES), *args],
            capture_output=True,
            text=True,
            input=stdin,
            env=env,
            cwd=self.project,
        )
        if check is not None:
            self.assertEqual(result.returncode, check, f"{args}\n{result.stdout}\n{result.stderr}")
        return result

    def verify_after(self, roles, change):
        self.zones("snapshot", "W1", check=0)
        change()
        return self.zones("verify", "W1", "--roles", roles)

    def test_write_inside_own_zone_passes(self):
        result = self.verify_after("artist", lambda: self.write("Art/source/ship.sprite.json", "{}"))
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("added Art/source/ship.sprite.json", result.stdout)
        self.assertIn("OK: no violations", result.stdout)

    def test_write_into_foreign_zone_is_violation(self):
        result = self.verify_after("artist", lambda: self.write("src/main.ts", "hacked\n"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("VIOLATION modified src/main.ts (zone of: developer)", result.stdout)

    def test_deleted_file_without_owner_is_reported(self):
        self.write("notes.txt")
        result = self.verify_after("developer", lambda: (self.project / "notes.txt").unlink())
        self.assertEqual(result.returncode, 1)
        self.assertIn("VIOLATION deleted notes.txt (zone of: nobody)", result.stdout)

    def test_paths_variables_expand(self):
        self.configure(art_export="public/assets/sprites", code=["src/**", "scripts/**"], project_files=[])

        self.zones("snapshot", "W1", check=0)
        self.write("public/assets/sprites/ship.png")
        self.write("scripts/build.ts")
        result = self.zones("verify", "W1", "--roles", "artist,developer", "--json", check=0)
        changes = {item["path"]: item["roles"] for item in json.loads(result.stdout)["changes"]}
        self.assertEqual(changes, {"public/assets/sprites/ship.png": ["artist"], "scripts/build.ts": ["developer"]})
        listing = self.zones("zones", "--role", "developer", check=0).stdout
        self.assertNotIn("${paths", listing)

    def test_producer_zones_and_ignored_dirs_are_skipped(self):
        def change():
            self.write("Studio/tasks/T-001.md")
            self.write("Studio/BOARD.md")
            self.write("node_modules/pkg/index.js")

        result = self.verify_after("qa", change)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("no changes", result.stdout)

    def test_shared_zone_is_per_role(self):
        result = self.verify_after("artist", lambda: self.write("Studio/reports/S1/T-001.artist.md"))
        self.assertEqual(result.returncode, 0, result.stdout)
        result = self.verify_after("artist", lambda: self.write("Studio/reports/S1/T-001.developer.md"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Studio/reports/S1/T-001.developer.md (zone of: developer)", result.stdout)

    def test_lock_detects_and_restores_acceptance_tests(self):
        test_file = self.write("tests/acceptance/ship.test.ts", "expect(1)\n")
        self.zones("lock", check=0)
        self.zones("check-lock", check=0)
        test_file.write_text("expect(true)\n", encoding="utf-8")
        extra = self.write("tests/acceptance/extra.test.ts")
        result = self.zones("check-lock", check=1)
        self.assertIn("CHANGED modified tests/acceptance/ship.test.ts", result.stdout)
        self.assertIn("CHANGED added tests/acceptance/extra.test.ts", result.stdout)
        self.zones("check-lock", "--restore", check=1)
        self.assertEqual(test_file.read_text(encoding="utf-8"), "expect(1)\n")
        self.assertFalse(extra.exists())
        self.zones("check-lock", check=0)

    def hook(self, payload, project=None):
        env = dict(os.environ)
        env["CLAUDE_PROJECT_DIR"] = str(project or self.project)
        return self.zones("hook", stdin=json.dumps(payload), env=env)

    def write_payload(self, agent_type, path, tool_name="Write"):
        return {
            "agent_type": agent_type,
            "tool_name": tool_name,
            "tool_input": {"file_path": path, "content": "x"},
            "cwd": str(self.project),
        }

    def test_hook_blocks_write_outside_role_zone(self):
        result = self.hook(self.write_payload("game-studio:artist", str(self.project / "src/x.ts")))
        self.assertEqual(result.returncode, 2)
        self.assertIn("role artist may not write src/x.ts", result.stderr)
        self.assertIn("NEW TASKS or HANDOFF", result.stderr)

    def test_hook_allows_own_zone_foreign_agents_and_outside_paths(self):
        self.assertEqual(self.hook(self.write_payload("game-studio:artist", "Art/x.png")).returncode, 0)
        self.assertEqual(self.hook(self.write_payload("studio-developer", "src/x.ts")).returncode, 0)
        self.assertEqual(self.hook(self.write_payload("general-purpose", "src/x.ts")).returncode, 0)
        self.assertEqual(self.hook(self.write_payload("", "src/x.ts")).returncode, 0)
        self.assertEqual(self.hook(self.write_payload("game-studio:artist", "/tmp/elsewhere.txt")).returncode, 0)

    def test_hook_skips_projects_without_studio(self):
        with tempfile.TemporaryDirectory() as other:
            payload = self.write_payload("game-studio:artist", str(Path(other) / "src/x.ts"))
            payload["cwd"] = other
            self.assertEqual(self.hook(payload, project=other).returncode, 0)

    def test_hook_parses_apply_patch(self):
        patch = "*** Begin Patch\n*** Add File: {path}\n+x\n*** End Patch\n"
        payload = {
            "agent_type": "studio-artist",
            "tool_name": "apply_patch",
            "tool_input": {"command": patch.format(path="src/y.ts")},
            "cwd": str(self.project),
        }
        result = self.hook(payload)
        self.assertEqual(result.returncode, 2)
        self.assertIn("src/y.ts", result.stderr)
        payload["tool_input"]["command"] = patch.format(path="Art/source/y.svg")
        self.assertEqual(self.hook(payload).returncode, 0)

    def test_hook_ignores_broken_input(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(self.project))
        self.assertEqual(self.zones("hook", stdin="not json", env=env).returncode, 0)


if __name__ == "__main__":
    unittest.main()
