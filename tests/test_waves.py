import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools"
sys.path.insert(0, str(TOOLS))

import board  # noqa: E402
import tokens  # noqa: E402
import waves  # noqa: E402

SPEC = """---
id: F-001
title: Ship
---

# F-001: Ship

## Player story
As a player I fly the ship.

## Mechanics and numbers
Speed 120 px/s.

## AC
- AC-1: ← moves the ship left at 120 px/s.

## Assets
- ship.png, 16×16, 2 frames

## Sounds
- thrust → SOUND-MARKER

## Roles needed
artist
"""


class SummaryTest(unittest.TestCase):
    def test_single_task_summary(self):
        summary = waves.parse_summary(
            "## Summary\n```\nSTATUS: DONE_WITH_CONCERNS — palette is tight\nTASK: T-007\nFILES: a.png, b.png\n"
            "CHECK: pixelart.py → OK\nHANDOFF: → art-director: review (a.png)\nNEW TASKS: artist/FIX: Outline [deps: T-007]\n"
            "- qa/VERIFY: Check [later]\nQUESTIONS: Use 3 frames? Recommended: yes\n```\n\n## Work\nSTATUS: ignored\n"
        )
        self.assertEqual((summary["status"], summary["note"]), ("DONE_WITH_CONCERNS", "palette is tight"))
        self.assertEqual(summary["tasks"], ["T-007"])
        self.assertEqual(summary["files"], ["a.png", "b.png"])
        self.assertEqual(summary["new_tasks"], ["artist/FIX: Outline [deps: T-007]", "qa/VERIFY: Check [later]"])
        self.assertEqual(summary["questions"], ["Use 3 frames? Recommended: yes"])
        self.assertEqual(waves.task_statuses(summary, ["T-007"]), {"T-007": ("DONE_WITH_CONCERNS", "palette is tight")})

    def test_batch_summary_and_missing_summary(self):
        summary = waves.parse_summary("## Summary\n- T-007: APPROVE\n- T-008: CHANGES_REQUESTED — R-2, R-3\n")
        self.assertEqual(waves.task_statuses(summary, ["T-007", "T-008", "T-009"]), {
            "T-007": ("APPROVE", ""), "T-008": ("CHANGES_REQUESTED", "R-2, R-3"), "T-009": (None, ""),
        })
        self.assertIsNone(waves.parse_summary("# Report\nSTATUS: DONE\n"))


class WaveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name).resolve()
        self.project = base / "game"
        self.project.mkdir()
        self.env = dict(os.environ, CLAUDE_CONFIG_DIR=str(base / "claude"))
        result = subprocess.run(
            [sys.executable, str(TOOLS / "studio_init.py"), str(self.project), "--json", "--language", "en"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        ledger = self.project / "Studio" / "ledger.md"
        ledger.write_text(ledger.read_text(encoding="utf-8").replace("- Sprint: 0", "- Sprint: 1"), encoding="utf-8")
        self.write("Design/features/F-001-ship.md", SPEC)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative, text):
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def read(self, relative):
        return (self.project / relative).read_text(encoding="utf-8")

    def tool(self, name, *args, code=0):
        result = subprocess.run(
            [sys.executable, str(TOOLS / name), "--project", str(self.project), *args],
            capture_output=True, text=True, env=self.env,
        )
        if code is not None and result.returncode != code:
            self.fail(f"{name} {args} → {result.returncode}\n{result.stdout}\n{result.stderr}")
        return result.stdout

    def wave(self, *args, code=0):
        return self.tool("waves.py", *args, code=code)

    def tasks(self, lines):
        self.write("Studio/meetings/S1-planning/minutes.md", "# Minutes\n\n## Tasks\n" + "".join(f"- {line}\n" for line in lines))
        return self.tool("board.py", "import", "Studio/meetings/S1-planning/minutes.md")

    def status(self, task_id):
        return board.Board(self.project).find(task_id).get("status")

    def report(self, path, *lines):
        self.write(path, "## Summary\n" + "".join(f"{line}\n" for line in lines) + "\n## Details\nshort\n")

    def config(self):
        return json.loads(self.read("Studio/studio.json"))

    def save_config(self, config):
        self.write("Studio/studio.json", json.dumps(config, indent=2))

    def test_a_wave_moves_the_board_from_the_summaries(self):
        self.tasks([
            "architect/PLAN: Tech plan F-001 [id: plan] [feature: F-001]",
            "qa/WRITE_TESTS: Acceptance tests F-001 [id: tests] [deps: plan] [feature: F-001]",
            "artist/WORK: Ship sprite [reviewers: art-director] [feature: F-001] "
            "[inputs: Design/features/F-001-ship.md, References/Art Bible.md]",
            "developer/WORK: Build F-001 [deps: plan, tests] [reviewers: architect] [feature: F-001]",
        ])
        out = self.wave("start")
        self.assertIn("S1-W1: 2 runs", out)
        self.assertIn("architect PLAN T-001 — model=opus effort=high — new agent", out)
        self.assertIn("artist WORK T-003 — model=sonnet effort=medium — new agent", out)
        self.assertIn("PACK=Studio/.state/packs/S1-W1-artist.md", out)
        self.assertIn("REPORT=Studio/reports/S1/T-003.artist.md", out)
        self.assertEqual((self.status("T-001"), self.status("T-003")), ("doing", "doing"))

        artist_pack = self.read("Studio/.state/packs/S1-W1-artist.md")
        self.assertIn("AC-1", artist_pack)
        self.assertIn("ship.png, 16×16", artist_pack)
        self.assertNotIn("SOUND-MARKER", artist_pack)
        self.assertIn("- References/Art Bible.md", artist_pack)
        self.assertNotIn("- Design/features/F-001-ship.md", artist_pack)
        self.assertIn("SOUND-MARKER", self.read("Studio/.state/packs/S1-W1-architect.md"))

        self.write("Tech/plans/F-001.md", "# Plan\n")
        self.write("Art/export/ship.png", "png")
        self.report(
            "Studio/reports/S1/T-001.architect.md",
            "STATUS: DONE", "TASK: T-001", "FILES: Tech/plans/F-001.md", "CHECK: plan → 40 lines",
            "NEW TASKS: developer/WORK: Wire input [deps: T-001] [reviewers: architect]",
            "- narrative-designer/WORK: Intro text [later]",
        )
        self.report(
            "Studio/reports/S1/T-003.artist.md",
            "STATUS: DONE", "FILES: Art/export/ship.png", "CHECK: pixelart.py → OK",
            "HANDOFF: → art-director: review the ship (Art/export/ship.png)",
        )
        out = self.wave("finish", "--agent", "architect=a1", "--agent", "artist=a2")
        self.assertIn("S1-W1 finished", out)
        self.assertIn("T-001 architect PLAN: DONE → done", out)
        self.assertIn("T-003 artist WORK: DONE → review", out)
        self.assertIn("created T-005 developer/WORK: Wire input [deps T-001]", out)
        self.assertIn("proposals.md: +1", out)
        self.assertEqual(board.Board(self.project).find("T-005").get("feature"), "F-001")
        self.assertIn("Intro text", self.read("Studio/proposals.md"))
        self.assertIn("S1 artist → art-director: review the ship", self.read("Studio/tasks/T-003.md"))
        self.assertIn("S1 artist: DONE (Studio/reports/S1/T-003.artist.md)", self.read("Studio/tasks/T-003.md"))
        ledger = self.read("Studio/ledger.md")
        self.assertIn("- Wave: 1", ledger)
        self.assertIn("- S1-W1: architect PLAN T-001; artist WORK T-003", ledger)
        self.assertIn("| T-003 |", self.read("Studio/BOARD.md"))
        spend = [json.loads(line) for line in self.read("Studio/.state/budget.jsonl").splitlines()]
        self.assertEqual([(entry["role"], entry["tokens"]) for entry in spend], [("architect", None), ("artist", None)])

        out = self.wave("start")
        self.assertIn("S1-W2: 3 runs", out)
        self.assertIn("art-director REVIEW T-003", out)
        self.assertIn("REPORT=Studio/reviews/art/T-003-r1.md", out)
        self.assertIn("qa WRITE_TESTS T-002", out)
        self.assertIn("developer WORK T-005", out)

    def test_review_findings_reach_the_fix_pack(self):
        self.tasks(["artist/WORK: Ship sprite [reviewers: art-director] [feature: F-001]"])
        self.wave("start")
        self.report("Studio/reports/S1/T-001.artist.md", "STATUS: DONE", "FILES: Art/export/ship.png")
        self.wave("finish", "--agent", "artist=a1")
        self.wave("start")
        self.write(
            "Studio/reviews/art/T-001-r1.md",
            "## Summary\nSTATUS: CHANGES_REQUESTED — R-1\nTASK: T-001\n\n## T-001\n- R-1 🟡 thicker outline\n",
        )
        out = self.wave("finish", "--agent", "art-director=r1")
        self.assertIn("T-001 art-director REVIEW: CHANGES_REQUESTED — R-1 → changes (rework 1)", out)
        out = self.wave("start")
        self.assertIn("artist FIX T-001", out)
        self.assertIn("CONTEXT: Studio/reviews/art/T-001-r1.md", out)
        self.assertIn("thicker outline", self.read("Studio/.state/packs/S1-W3-artist.md"))

    def test_a_zone_violation_applies_nothing_until_the_round_is_voided(self):
        self.tasks(["artist/WORK: Ship sprite [feature: F-001]"])
        self.wave("start")
        self.write("src/hack.ts", "export {}\n")
        self.report("Studio/reports/S1/T-001.artist.md", "STATUS: DONE", "FILES: src/hack.ts")
        out = self.wave("finish", "--agent", "artist=a1", code=1)
        self.assertIn("VIOLATION added src/hack.ts", out)
        self.assertEqual(self.status("T-001"), "doing")

        (self.project / "src" / "hack.ts").unlink()
        out = self.wave("finish", "--agent", "artist=a1", "--void", "artist")
        self.assertIn("round void", out)
        self.assertEqual(self.status("T-001"), "todo")

    def test_questions_block_and_a_missing_summary_keeps_the_wave_open(self):
        self.tasks([
            "narrative-designer/WORK: Intro text [feature: F-001]",
            "sound-designer/WORK: Thrust sound [feature: F-001]",
        ])
        self.wave("start")
        self.report(
            "Studio/reports/S1/T-001.narrative-designer.md",
            "STATUS: NEEDS_INPUT", "QUESTIONS: Is the pilot named? Recommended: no",
        )
        out = self.wave("finish", "--agent", "narrative-designer=n1", "--agent", "sound-designer=s1")
        self.assertIn("stays open", out)
        self.assertIn("Is the pilot named?", out)
        self.assertIn("Waiting for: sound-designer", out)
        self.assertEqual(self.status("T-001"), "blocked")
        self.assertIn("Is the pilot named? Recommended: no", self.read("Studio/questions.md"))

        self.report("Studio/reports/S1/T-002.sound-designer.md", "STATUS: DONE", "CHECK: sfx.py analyze → no clipping")
        out = self.wave("finish", "--agent", "sound-designer=s1")
        self.assertIn("S1-W1 finished", out)
        self.assertEqual(self.status("T-002"), "done")
        runs = [json.loads(line)["role"] for line in self.read("Studio/.state/budget.jsonl").splitlines()]
        self.assertEqual(runs, ["narrative-designer", "sound-designer"])

    def test_done_without_proof_goes_back_to_the_agent(self):
        self.tasks(["level-designer/WORK: Level 1 [feature: F-001]"])
        self.wave("start")
        self.report("Studio/reports/S1/T-001.level-designer.md", "STATUS: DONE")
        out = self.wave("finish", "--agent", "level-designer=l1")
        self.assertIn("no CHECK and no FILES", out)
        self.assertIn("stays open", out)
        self.assertEqual(self.status("T-001"), "doing")

        self.report("Studio/reports/S1/T-001.level-designer.md", "STATUS: DONE", "CHECK: level_preview.py check → OK")
        self.assertIn("finished", self.wave("finish", "--agent", "level-designer=l1"))
        self.assertEqual(self.status("T-001"), "done")

    def test_locked_tests_void_the_developer_round(self):
        self.tasks([
            "qa/WRITE_TESTS: Acceptance tests [id: tests] [feature: F-001]",
            "developer/WORK: Build F-001 [deps: tests] [feature: F-001]",
        ])
        self.wave("start")
        self.write("tests/acceptance/ship.test.ts", "expect(1)\n")
        self.report("Studio/reports/S1/T-001.qa.md", "STATUS: DONE", "CHECK: vitest → 1 red")
        self.assertIn("locked acceptance tests: 1 files", self.wave("finish", "--agent", "qa=q1"))

        self.wave("start")
        self.write("tests/acceptance/ship.test.ts", "expect(true)\n")
        self.write("src/main.ts", "export {}\n")
        self.report("Studio/reports/S1/T-002.developer.md", "STATUS: DONE", "CHECK: vitest → green")
        self.assertIn("VIOLATION modified tests/acceptance/ship.test.ts",
                      self.wave("finish", "--agent", "developer=d1", code=1))
        out = self.wave("finish", "--agent", "developer=d1", "--void", "developer")
        self.assertIn("round void", out)
        self.assertEqual(self.read("tests/acceptance/ship.test.ts"), "expect(1)\n")
        self.assertEqual(self.status("T-002"), "todo")

    def test_the_budget_cap_stops_a_wave_before_it_starts(self):
        config = self.config()
        config["budget"]["max_agent_runs_per_sprint"] = 1
        self.save_config(config)
        self.tasks(["artist/WORK: Ship [feature: F-001]", "sound-designer/WORK: Thrust [feature: F-001]"])
        self.assertIn("OVER BUDGET", self.wave("start", code=1))
        self.assertEqual(self.status("T-001"), "todo")
        self.assertIn("No open wave", self.wave("status"))

    def test_spend_comes_from_transcripts_and_drives_reuse(self):
        folder = Path(self.env["CLAUDE_CONFIG_DIR"]) / "projects" / tokens.project_slug(self.project)

        def transcript(path, *requests):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(json.dumps({"message": {
                "id": f"{path.stem}-{index}", "role": "assistant", "content": [],
                "usage": {"input_tokens": 0, "cache_creation_input_tokens": write,
                          "cache_read_input_tokens": read, "output_tokens": 0},
            }}) + "\n" for index, (write, read) in enumerate(requests)), encoding="utf-8")

        transcript(folder / "producer.jsonl", (20000, 0))
        transcript(folder / "producer" / "subagents" / "agent-art1.jsonl", (8000, 0), (2000, 8000))
        self.tasks(["artist/WORK: Ship sprite [reviewers: art-director] [feature: F-001]"])
        self.wave("start")
        self.report("Studio/reports/S1/T-001.artist.md", "STATUS: DONE", "FILES: Art/export/ship.png")
        out = self.wave("finish", "--agent", "artist=art1")
        self.assertIn("spend: agents 13.3K, producer 25.0K", out)
        spend = [json.loads(line) for line in self.read("Studio/.state/budget.jsonl").splitlines()]
        self.assertEqual([(entry["role"], entry["tokens"]) for entry in spend],
                         [("artist", 13300), ("producer", 25000)])
        agents = json.loads(self.read("Studio/.state/agents.json"))
        self.assertEqual((agents["artist"]["agent"], agents["artist"]["context"]), ("art1", 10000))

        self.wave("start")
        self.write("Studio/reviews/art/T-001-r1.md", "## Summary\nSTATUS: CHANGES_REQUESTED\n")
        self.wave("finish", "--agent", "art-director=r1")
        self.assertIn("artist FIX T-001 — model=sonnet effort=medium — continue agent art1 (context 10.0K)",
                      self.wave("start"))

        # The continued agent's transcript holds both runs; only the new part is logged.
        transcript(folder / "producer" / "subagents" / "agent-art1.jsonl", (8000, 0), (2000, 8000), (1000, 10000))
        self.report("Studio/reports/S1/T-001.artist.md", "STATUS: DONE", "FILES: Art/export/ship.png")
        self.wave("finish", "--agent", "artist=art1")
        last_artist = [json.loads(line) for line in self.read("Studio/.state/budget.jsonl").splitlines()
                       if json.loads(line)["role"] == "artist"][-1]
        self.assertEqual(last_artist["tokens"], 1250 + 1000)


if __name__ == "__main__":
    unittest.main()
