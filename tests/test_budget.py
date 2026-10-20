import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools"
TEMPLATE = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "templates" / "Studio" / "studio.json"
sys.path.insert(0, str(TOOLS))

import budget  # noqa: E402


class RouteTest(unittest.TestCase):
    def test_decisions_get_the_strong_model_only_outside_lean(self):
        self.assertEqual(budget.route("standard", "game-designer", "SPEC"), ("opus", "high"))
        self.assertEqual(budget.route("standard", "architect", "PLAN"), ("opus", "high"))
        self.assertEqual(budget.route("lean", "architect", "PLAN"), ("sonnet", "medium"))

    def test_cheap_work_gets_the_cheap_model(self):
        self.assertEqual(budget.route("standard", "qa", "MEETING"), ("haiku", "low"))
        self.assertEqual(budget.route("lean", "sound-designer", "WORK"), ("haiku", "low"))
        self.assertEqual(budget.route("standard", "developer", "WORK"), ("sonnet", "medium"))
        self.assertEqual(budget.route("standard", "art-director", "REVIEW"), ("sonnet", "medium"))

    def test_escalation_overrides_the_profile(self):
        self.assertEqual(budget.route("lean", "developer", "FIX", escalate=True), ("opus", "high"))

    def test_unknown_profile_or_role_is_an_error(self):
        with self.assertRaises(budget.BudgetError):
            budget.route("cheap", "developer", "WORK")
        with self.assertRaises(budget.BudgetError):
            budget.route("lean", "intern", "WORK")


class BudgetCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "Studio").mkdir()
        (self.project / "Studio" / "studio.json").write_text(TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8")
        (self.project / "Studio" / "ledger.md").write_text("- Sprint: 2\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def budget(self, *args):
        return subprocess.run(
            [sys.executable, str(TOOLS / "budget.py"), "--project", str(self.project), *args],
            capture_output=True, text=True,
        )

    def config(self):
        return json.loads((self.project / "Studio" / "studio.json").read_text(encoding="utf-8"))

    def test_template_starts_on_the_standard_profile(self):
        self.assertEqual(self.config()["budget"]["profile"], "standard")
        self.assertIn("claude=opus", self.budget("route", "game-designer", "ACCEPT").stdout)

    def test_profile_rewrites_limits_and_budget(self):
        result = self.budget("profile", "lean")
        self.assertEqual(result.returncode, 0, result.stderr)
        config = self.config()
        self.assertEqual(config["budget"]["profile"], "lean")
        self.assertEqual(config["budget"]["meeting"], "none")
        self.assertEqual(config["limits"]["features_per_sprint"], 1)
        self.assertEqual(config["limits"]["rework_cycles"], 2)
        self.assertEqual((config["budget"]["verify"], config["budget"]["tests_first"]), ("sprint-end", False))
        self.assertIn("claude=sonnet", self.budget("route", "game-designer", "ACCEPT").stdout)

    def test_check_stops_the_sprint_at_the_cap(self):
        self.budget("profile", "lean")
        for _ in range(13):
            self.budget("log", "--role", "developer", "--mode", "WORK", "--tokens", "10000")
        self.assertEqual(self.budget("check", "--next", "1").returncode, 0)
        self.assertIn("WARN", self.budget("check", "--next", "1").stdout)
        over = self.budget("check", "--next", "3")
        self.assertEqual(over.returncode, 1)
        self.assertIn("OVER BUDGET", over.stdout)

    def test_check_stops_on_tokens_and_counts_only_the_sprint(self):
        self.budget("profile", "lean")
        self.budget("log", "--role", "artist", "--mode", "WORK", "--tokens", "900000", "--sprint", "1")
        self.assertEqual(self.budget("check").returncode, 0)
        self.budget("log", "--role", "artist", "--mode", "WORK", "--tokens", "1300000")
        self.assertEqual(self.budget("check").returncode, 1)

    def test_report_sums_runs_and_tokens_by_role(self):
        self.budget("log", "--role", "developer", "--mode", "WORK", "--tokens", "50000")
        self.budget("log", "--role", "developer", "--mode", "FIX", "--tokens", "20000")
        self.budget("log", "--role", "qa", "--mode", "VERIFY")
        report = json.loads(self.budget("report", "--json").stdout)
        self.assertEqual((report["sprint"], report["runs"], report["tokens"], report["runs_without_tokens"]), (2, 3, 70000, 1))
        self.assertEqual(report["by_role"]["developer"], {"runs": 2, "tokens": 70000})


if __name__ == "__main__":
    unittest.main()
