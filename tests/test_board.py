import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools" / "board.py"


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project = Path(self.temp.name)
        (self.project / "Studio" / "tasks").mkdir(parents=True)
        (self.project / "Studio" / "ledger.md").write_text("# Ledger\n\n- Step: sprint\n- Sprint: 1\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def board(self, *args, check=True):
        result = subprocess.run(
            [sys.executable, str(BOARD), "--project", str(self.project), *args],
            capture_output=True,
            text=True,
        )
        if check and result.returncode != 0:
            self.fail(f"board {args} → {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result

    def new(self, *args):
        return self.board("new", *args).stdout.strip()

    def task_text(self, task_id):
        return (self.project / "Studio" / "tasks" / f"{task_id}.md").read_text(encoding="utf-8")

    def next_items(self):
        return json.loads(self.board("next", "--json").stdout)

    def test_full_review_cycle(self):
        task_id = self.new(
            "--owner", "artist", "--mode", "WORK", "--title", "Ship sprite",
            "--feature", "F-001", "--reviewers", "art-director",
        )
        self.assertEqual(task_id, "T-001")
        self.assertIn("sprint: 1", self.task_text(task_id))
        self.assertEqual([(item["kind"], item["role"], item["mode"]) for item in self.next_items()],
                         [("RUN", "artist", "WORK")])

        self.board("start", task_id)
        self.board("finish", task_id)
        self.assertIn("status: review", self.task_text(task_id))
        self.assertEqual([(item["kind"], item["role"], item["mode"]) for item in self.next_items()],
                         [("REVIEW", "art-director", "REVIEW")])

        review = self.project / "Studio" / "reviews" / "art" / "T-001-r1.md"
        review.parent.mkdir(parents=True)
        review.write_text("R-1 🟡 thicker outline\n", encoding="utf-8")
        self.board("review", task_id, "art-director", "changes")
        text = self.task_text(task_id)
        self.assertIn("status: changes", text)
        self.assertIn("rework: 1", text)

        [item] = self.next_items()
        self.assertEqual((item["kind"], item["role"], item["mode"]), ("RUN", "artist", "FIX"))
        self.assertEqual(item["context"], ["Studio/reviews/art/T-001-r1.md"])

        self.board("start", task_id)
        self.board("finish", task_id)
        self.board("review", task_id, "art-director", "approve")
        text = self.task_text(task_id)
        self.assertIn("status: done", text)
        self.assertIn("approvals: [art-director]", text)
        self.assertIn("CHANGES_REQUESTED", text)
        self.assertIn("- S1 producer: created", text)
        self.assertIn("in progress (WORK)", text)
        self.assertIn("in progress (FIX)", text)
        self.assertEqual(self.next_items(), [])

    def test_all_reviewers_must_approve(self):
        task_id = self.new("--owner", "developer", "--mode", "WORK", "--title", "Movement",
                           "--reviewers", "architect,qa")
        self.board("finish", task_id)
        modes = sorted((item["role"], item["mode"]) for item in self.next_items())
        self.assertEqual(modes, [("architect", "CODE_REVIEW"), ("qa", "REVIEW")])
        self.board("review", task_id, "architect", "approve")
        self.assertIn("status: review", self.task_text(task_id))
        self.assertEqual([item["role"] for item in self.next_items()], ["qa"])
        self.board("review", task_id, "qa", "approve")
        self.assertIn("status: done", self.task_text(task_id))

    def test_next_waits_for_deps(self):
        plan = self.new("--owner", "architect", "--mode", "PLAN", "--title", "Tech plan")
        work = self.new("--owner", "developer", "--mode", "WORK", "--title", "Code", "--deps", plan)
        self.assertEqual([item["id"] for item in self.next_items()], [plan])
        self.board("finish", plan)
        self.assertEqual([item["id"] for item in self.next_items()], [work])

    def test_next_orders_reviews_and_unblocking_modes_first(self):
        work = self.new("--owner", "artist", "--mode", "WORK", "--title", "Art")
        tests = self.new("--owner", "qa", "--mode", "WRITE_TESTS", "--title", "Tests")
        plan = self.new("--owner", "architect", "--mode", "PLAN", "--title", "Plan")
        accept = self.new("--owner", "game-designer", "--mode", "ACCEPT", "--title", "Acceptance")
        reviewed = self.new("--owner", "narrative-designer", "--mode", "WORK", "--title", "Texts",
                            "--reviewers", "game-designer")
        self.board("finish", reviewed)
        self.assertEqual([item["id"] for item in self.next_items()], [reviewed, accept, plan, tests, work])

    def test_set_updates_lists_and_counters(self):
        first = self.new("--owner", "qa", "--mode", "VERIFY", "--title", "Verify")
        fix = self.new("--owner", "developer", "--mode", "FIX", "--title", "Fix")
        self.board("set", first, f"deps=+{fix}", "rework=+1", "status=todo")
        text = self.task_text(first)
        self.assertIn(f"deps: [{fix}]", text)
        self.assertIn("rework: 1", text)
        self.board("set", first, f"deps=-{fix}")
        self.assertIn("deps: []", self.task_text(first))
        self.assertNotEqual(self.board("set", first, "status=finished", check=False).returncode, 0)

    def test_block_unblock_and_log_write_journal(self):
        task_id = self.new("--owner", "level-designer", "--mode", "WORK", "--title", "Level 1")
        self.board("block", task_id, "waiting for the difficulty answer")
        self.assertIn("status: blocked", self.task_text(task_id))
        self.board("unblock", task_id)
        self.board("log", task_id, "S1 level-designer → game-designer: L01 ready")
        journal = self.task_text(task_id).split("## Log", 1)[1]
        self.assertIn("blocked: waiting for the difficulty answer", journal)
        self.assertIn("status blocked → todo", journal)
        self.assertIn("- S1 level-designer → game-designer: L01 ready", journal)

    def test_validate_catches_cycle_and_self_review(self):
        first = self.new("--owner", "architect", "--mode", "PLAN", "--title", "A")
        second = self.new("--owner", "developer", "--mode", "WORK", "--title", "B", "--deps", first)
        self.board("set", first, f"deps={second}")
        self.new("--owner", "artist", "--mode", "WORK", "--title", "C")
        self.board("set", "T-003", "reviewers=artist")
        result = self.board("validate", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("dependency cycle", result.stdout)
        self.assertIn("artist reviews its own work", result.stdout)

    def test_validate_ok_and_mode_check(self):
        self.new("--owner", "qa", "--mode", "FIX", "--title", "Fix the test")
        self.assertIn("OK: 1 tasks", self.board("validate").stdout)
        rejected = self.board("new", "--owner", "artist", "--mode", "PLAN", "--title", "Wrong mode", check=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("does not fit role artist", rejected.stderr)

    def test_render_and_summary(self):
        plan = self.new("--owner", "architect", "--mode", "PLAN", "--title", "Plan | v1", "--feature", "F-001")
        accept = self.new("--owner", "game-designer", "--mode", "ACCEPT", "--title", "Acceptance", "--feature", "F-001",
                          "--deps", plan)
        self.board("finish", plan)
        self.board("finish", accept)
        self.board("render")
        board = (self.project / "Studio" / "BOARD.md").read_text(encoding="utf-8")
        self.assertIn("## Sprint 1", board)
        self.assertIn("| T-001 | F-001 | Plan \\| v1 | architect | PLAN | ✅ done |", board)
        summary = json.loads(self.board("summary", "--sprint", "1", "--json").stdout)
        self.assertEqual(summary, [{"feature": "F-001", "done": 2, "total": 2, "accepted": True,
                                    "rework": 0, "blocked": []}])


if __name__ == "__main__":
    unittest.main()
