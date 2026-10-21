import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INIT = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools" / "studio_init.py"


class StudioInitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def init(self, *args):
        result = subprocess.run(
            [sys.executable, str(INIT), str(self.project), "--json", *args],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_init_lays_out_studio_and_tools(self):
        report = self.init("--game", "Asteroids", "--language", "ru")
        self.assertEqual(report["mode"], "init")
        self.assertIn("Studio/studio.json", report["created"])
        self.assertTrue((self.project / "Studio" / "PROTOCOL.md").is_file())
        board = self.project / "Studio" / "tools" / "board.py"
        self.assertTrue(board.is_file())
        self.assertTrue(os.access(board, os.X_OK))
        self.assertTrue((self.project / "Studio" / "tools" / "zones.py").is_file())
        config = json.loads((self.project / "Studio" / "studio.json").read_text(encoding="utf-8"))
        self.assertEqual(config["game"], "Asteroids")
        self.assertEqual(config["language"], "ru")
        self.assertEqual(config["docs_language"], "en")
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8"), "Studio/.state/\n")

    def test_repeat_resumes_without_overwriting(self):
        self.init("--language", "ru")
        ledger = self.project / "Studio" / "ledger.md"
        ledger.write_text("# my ledger\n", encoding="utf-8")
        tool = self.project / "Studio" / "tools" / "board.py"
        tool.write_text("# edit\n", encoding="utf-8")
        report = self.init("--language", "en")
        self.assertEqual(report["mode"], "resume")
        self.assertFalse(report["language_set"])
        self.assertEqual(report["created"], [])
        self.assertEqual(report["tools_updated"], [])
        self.assertEqual(ledger.read_text(encoding="utf-8"), "# my ledger\n")
        self.assertEqual(tool.read_text(encoding="utf-8"), "# edit\n")
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8").count("Studio/.state/"), 1)

    def test_update_tools_refreshes_tools_only(self):
        self.init()
        ledger = self.project / "Studio" / "ledger.md"
        ledger.write_text("# my ledger\n", encoding="utf-8")
        tool = self.project / "Studio" / "tools" / "board.py"
        tool.write_text("# edit\n", encoding="utf-8")
        report = self.init("--update-tools")
        self.assertIn("Studio/tools/board.py", report["tools_updated"])
        self.assertIn("Studio/PROTOCOL.md", report["tools_updated"])
        self.assertNotEqual(tool.read_text(encoding="utf-8"), "# edit\n")
        self.assertEqual(ledger.read_text(encoding="utf-8"), "# my ledger\n")

    def test_existing_gitignore_is_appended_once(self):
        (self.project / ".gitignore").write_text("node_modules", encoding="utf-8")
        self.init()
        self.init()
        self.assertEqual((self.project / ".gitignore").read_text(encoding="utf-8"), "node_modules\nStudio/.state/\n")


if __name__ == "__main__":
    unittest.main()
