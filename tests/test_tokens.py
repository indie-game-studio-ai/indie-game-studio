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


def write_rollout(codex_home, meta, responses, stamp="2026-10-21T10:00:00.000Z"):
    """A Codex rollout: session_meta, then a token_usage_record and a token_count event per
    response (input, cached, output). Codex may write a record twice."""
    records = [{"timestamp": stamp, "type": "session_meta", "payload": meta},
               {"timestamp": stamp, "type": "turn_context", "payload": {"model": "gpt-6.1"}}]
    total = {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}
    for index, (input_tokens, cached, output) in enumerate(responses):
        usage = {"input_tokens": input_tokens, "cached_input_tokens": cached, "cache_write_input_tokens": 0,
                 "output_tokens": output, "reasoning_output_tokens": 0, "total_tokens": input_tokens + output}
        record = {"timestamp": stamp, "type": "token_usage_record",
                  "payload": {"thread_id": meta["id"], "response_id": f"resp_{meta['id']}_{index}", "usage": usage}}
        total = {key: total[key] + usage[key] for key in total}
        records += [record, record, {"timestamp": stamp, "type": "event_msg",
                                     "payload": {"type": "token_count", "info": {"total_token_usage": total}}}]
    write_jsonl(codex_home / "sessions" / "2026" / "10" / "21" / f"rollout-2026-10-21T10-00-00-{meta['id']}.jsonl", records)


class TokensTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.project = base / "game"
        self.project.mkdir()
        self.config_dir = base / "claude"
        self.codex_home = base / "codex"
        self.previous = {name: os.environ.get(name) for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME")}
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.config_dir)
        os.environ["CODEX_HOME"] = str(self.codex_home)
        self.folder = self.config_dir / "projects" / tokens.project_slug(self.project.resolve())

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
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
        with self.assertRaises(tokens.TokensError):
            tokens.session_runs(self.project)

    def test_a_cold_request_and_the_cache_lifetime(self):
        write_jsonl(self.folder / "s1.jsonl", [
            dict(assistant("m1", usage(write=10000), []), timestamp="2026-10-21T10:00:00.000Z"),
            dict(assistant("m2", usage(read=10000, write=500), []), timestamp="2026-10-21T10:00:30.000Z"),
            # Continued after the cache expired: the whole context is written again.
            dict(assistant("m3", usage(read=3000, write=8000), []), timestamp="2026-10-21T10:20:00.000Z"),
        ])
        metrics = tokens.Run(self.folder / "s1.jsonl").metrics()
        self.assertEqual(metrics["cold"], 1)
        self.assertEqual(metrics["ttl"], tokens.CACHE_TTL_5M)
        self.assertEqual(metrics["last_at"], tokens.parse_time("2026-10-21T10:20:00Z"))
        write_jsonl(self.folder / "s2.jsonl", [assistant("m1", usage(write=1000, write_1h=1000), [])])
        self.assertEqual(tokens.Run(self.folder / "s2.jsonl").metrics()["ttl"], tokens.CACHE_TTL_1H)

    def test_codex_session_producer_and_agents(self):
        cwd = str(self.project.resolve())
        write_rollout(self.codex_home, {"id": "root", "session_id": "root", "cwd": cwd, "thread_source": "user"},
                      [(20000, 0, 50), (21000, 20000, 50)])
        write_rollout(self.codex_home, {
            "id": "child", "session_id": "root", "parent_thread_id": "root", "cwd": cwd, "thread_source": "subagent",
            "agent_role": "studio-artist", "agent_path": "/root/s1_w1_artist",
        }, [(20000, 15000, 100)])
        write_rollout(self.codex_home, {"id": "elsewhere", "session_id": "elsewhere", "cwd": "/other"}, [(1, 0, 1)])

        name, runs = tokens.session_runs(self.project)
        self.assertEqual(name, "root")
        summary = tokens.summarize(self.project, runs)
        producer, artist = summary["runs"]
        # Each response counts once, though its record is written twice.
        self.assertEqual((producer["requests"], producer["weighted"]), (2, 20000 + 250 + 1000 + 2000 + 250))
        self.assertEqual((artist["label"], artist["start"]), ("artist s1_w1_artist", 20000))
        self.assertEqual(artist["weighted"], 5000 + 1500 + 500)
        self.assertEqual(artist["model"], "gpt-6.1")

        for agent_id in ("s1_w1_artist", "/root/s1_w1_artist", "child"):
            self.assertEqual(tokens.agent_spend(self.project, agent_id)["weighted"], 7000)
        self.assertEqual(tokens.agent_spend(self.project, "child")["ttl"], tokens.CODEX_CACHE_TTL)
        self.assertEqual(tokens.producer_run(self.project)[0], "root")


if __name__ == "__main__":
    unittest.main()
