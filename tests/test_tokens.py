import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "plugins" / "game-studio" / "skills" / "game-studio" / "tools"
sys.path.insert(0, str(TOOLS))

import tokens  # noqa: E402


def assistant(message_id, usage, content):
    return {"type": "assistant", "message": {"id": message_id, "role": "assistant", "model": "claude-sonnet-5-5",
                                             "usage": usage, "content": content}}


def user(text):
    return {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}


def usage(input_tokens=0, write=0, read=0, output=0, write_1h=0):
    return {"input_tokens": input_tokens, "cache_creation_input_tokens": write, "cache_read_input_tokens": read,
            "output_tokens": output, "cache_creation": {"ephemeral_1h_input_tokens": write_1h}}


def write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


class TokensTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.project = base / "game"
        self.project.mkdir()
        self.config_dir = base / "claude"
        self.previous = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        self.folder = self.config_dir / "projects" / tokens.project_slug(self.project.resolve())

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.previous
        self.temp.cleanup()

    def write_session(self):
        write_jsonl(self.folder / "s1.jsonl", [
            user("make a game"),
            assistant("m1", usage(write=30000, output=10), [{"type": "text", "text": "ok"}]),
            assistant("m2", usage(read=30000, write=500, output=5),
                      [{"type": "tool_use", "name": "Agent", "input": {"prompt": "x"}}]),
        ])
        agent = self.folder / "s1" / "subagents" / "agent-abc.jsonl"
        write_jsonl(agent, [
            user("PROJECT=/g\nROLE=artist\nMODE=WORK\nTASKS=T-007,T-008\nPACK=p.md"),
            # A streamed response is written once per content block, with the same id
            # and the usage recorded at its start.
            assistant("a1", usage(write=8000, output=2), [{"type": "thinking", "thinking": ""}]),
            assistant("a1", usage(write=8000, output=2),
                      [{"type": "tool_use", "name": "Read", "input": {"file_path": str(self.project / "Design/F-001.md")}}]),
            assistant("a2", usage(read=8000, write=2000, output=40),
                      [{"type": "tool_use", "name": "Bash", "input": {"command": "sed -n '1,20p' Design/F-001.md && cat > x <<EOF"}}]),
            assistant("a3", usage(read=10000, output=4), [{"type": "text", "text": "x" * 700}]),
        ])
        (agent.with_suffix(".meta.json")).write_text(json.dumps({"agentType": "game-studio:artist"}), encoding="utf-8")
        fork = self.folder / "s1" / "subagents" / "agent-fork.jsonl"
        write_jsonl(fork, [assistant("f1", usage(write=99999), [])])
        fork.with_suffix(".meta.json").write_text(json.dumps({"agentType": "fork", "isFork": True}), encoding="utf-8")

    def test_weighted_tokens_use_the_price_ratios(self):
        self.assertEqual(tokens.weighted(usage(input_tokens=100, write=1000, read=10000, output=10)), 100 + 1250 + 1000 + 50)
        self.assertEqual(tokens.weighted(usage(write=1000, write_1h=1000)), 2000)

    def test_report_splits_producer_and_runs(self):
        self.write_session()
        summary = tokens.summarize(self.project, tokens.load_session(tokens.latest_session(self.project)))
        producer, artist = summary["runs"]
        self.assertEqual((producer["label"], producer["requests"], producer["start"]), ("producer", 2, 30000))
        self.assertEqual(artist["label"], "artist WORK T-007,T-008")
        self.assertEqual(artist["requests"], 3)
        self.assertEqual((artist["start"], artist["peak"]), (8000, 10000))
        # The recorded output of a1 and a3 is the stream start; the visible text is longer.
        self.assertEqual(artist["output"], round(len(json.dumps({"file_path": str(self.project / "Design/F-001.md")})) / 3.5) + 40 + 200)
        self.assertEqual(summary["producer_requests"], 2)
        self.assertEqual(summary["agent_requests"], 3)
        self.assertEqual(summary["agent_prefix_share"], round(8000 * 3 / 28000, 3))
        self.assertEqual(summary["repeated_reads"], [{"path": "Design/F-001.md", "runs": 1, "reads": 2}])

    def test_agent_spend_is_found_by_id(self):
        self.write_session()
        spend = tokens.agent_spend(self.project, "abc")
        self.assertEqual(spend["requests"], 3)
        self.assertEqual(spend["final_context"], 10000 + 200)
        self.assertIsNone(tokens.agent_spend(self.project, "missing"))

    def test_bash_reads(self):
        self.assertEqual(tokens.bash_read_paths("head -30 a.md 2>/dev/null; tail -n 5 b.log"), ["a.md", "b.log"])
        self.assertEqual(tokens.bash_read_paths("grep -n x c.md | head -5"), [])
        self.assertEqual(tokens.bash_read_paths("cat > d.md <<EOF"), [])

    def test_no_session_is_an_error(self):
        with self.assertRaises(tokens.TokensError):
            tokens.latest_session(self.project)


if __name__ == "__main__":
    unittest.main()
