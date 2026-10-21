#!/usr/bin/env python3
"""Real token spend of a studio session, read from Claude Code transcripts.

Claude Code keeps every session in ~/.claude/projects/<project slug>/<session>.jsonl and
its subagents in <session>/subagents/agent-<id>.jsonl. Each API request records its
usage there. The figure the Agent tool reports (`subagent_tokens`) is only the size of
the last request; the spend is the sum over all requests.

"Weighted" tokens are input-equivalent tokens at Claude's price ratios:
input ×1, cache write ×1.25 (5 min) or ×2 (1 h), cache read ×0.1, output ×5.

Input-side figures are exact. A transcript records the usage of a streamed response as
it starts, so output is taken as the larger of the recorded count and the visible text
at CHARS_PER_TOKEN; hidden thinking is not counted.
"""
import argparse
import json
import os
import re
import shlex
import sys
from pathlib import Path

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1
OUTPUT = 5.0
CHARS_PER_TOKEN = 3.5

HEADER_FIELD = re.compile(r"^(ROLE|MODE|TASKS)=(\S+)", re.M)
READ_COMMANDS = {"cat", "head", "tail", "less", "sed"}
COMMAND_SEPARATOR = re.compile(r"\|\||&&|[|;\n]")


class TokensError(Exception):
    pass


def claude_projects_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def project_slug(path):
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def project_dirs(project):
    project = Path(project)
    candidates = {str(project.absolute()), str(project.resolve())}
    base = claude_projects_dir()
    return [base / project_slug(path) for path in sorted(candidates) if (base / project_slug(path)).is_dir()]


def latest_session(project):
    sessions = [path for folder in project_dirs(project) for path in folder.glob("*.jsonl")]
    if not sessions:
        raise TokensError(f"no Claude Code sessions for {project} in {claude_projects_dir()}")
    return max(sessions, key=lambda path: path.stat().st_mtime)


def resolve_session(project, session):
    if not session:
        return latest_session(project)
    path = Path(session)
    if path.suffix == ".jsonl" and path.is_file():
        return path
    for folder in project_dirs(project):
        candidate = folder / f"{session}.jsonl"
        if candidate.is_file():
            return candidate
    raise TokensError(f"session {session} not found for {project}")


def find_agent_transcript(project, agent_id):
    for folder in project_dirs(project):
        for path in folder.glob(f"*/subagents/agent-{agent_id}.jsonl"):
            return path
    return None


def weighted(usage):
    cache_write = usage.get("cache_creation_input_tokens") or 0
    split = usage.get("cache_creation") or {}
    write_1h = split.get("ephemeral_1h_input_tokens") or 0
    write_5m = cache_write - write_1h
    return (
        (usage.get("input_tokens") or 0)
        + CACHE_WRITE_5M * write_5m
        + CACHE_WRITE_1H * write_1h
        + CACHE_READ * (usage.get("cache_read_input_tokens") or 0)
        + OUTPUT * (usage.get("output_tokens") or 0)
    )


def context_size(usage):
    return (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )


def text_of(content):
    if isinstance(content, str):
        return content
    return "\n".join(block.get("text", "") for block in content or [] if block.get("type") == "text")


def bash_read_paths(command):
    paths = []
    for segment in COMMAND_SEPARATOR.split(command):
        try:
            words = shlex.split(segment)
        except ValueError:
            continue
        if not words or words[0] not in READ_COMMANDS:
            continue
        # `cat > file <<EOF` writes, it does not read.
        if any(word.startswith(">") or word.startswith("<<") for word in words):
            continue
        arguments = [word for word in words[1:] if not re.match(r"^\d*[<>]", word)]
        if words[0] == "sed":
            if "-n" not in arguments:
                continue
            # The first argument after the flags is the sed script, not a file.
            arguments = [word for word in arguments if not word.startswith("-")][1:]
        skip_next = False
        for word in arguments:
            if skip_next:
                skip_next = False
                continue
            if word in ("-n", "-c"):
                skip_next = True
                continue
            if word.startswith("-") or word.startswith(">") or word.isdigit():
                continue
            paths.append(word)
    return paths


def read_paths(tool, tool_input):
    if tool == "Read" and tool_input.get("file_path"):
        return [tool_input["file_path"]]
    if tool == "Bash":
        return bash_read_paths(tool_input.get("command", ""))
    return []


def visible_chars(content):
    size = 0
    for block in content or []:
        kind = block.get("type")
        if kind == "text":
            size += len(block.get("text", ""))
        elif kind == "thinking":
            size += len(block.get("thinking", ""))
        elif kind == "tool_use":
            size += len(json.dumps(block.get("input") or {}, ensure_ascii=False))
    return size


class Run:
    """One transcript: the main session or one subagent."""

    def __init__(self, path, label="producer", agent_type=""):
        self.path = path
        self.label = label
        self.agent_type = agent_type
        self.usages = []
        self.tools = {}
        self.reads = []
        self.prompt = ""
        self.model = ""
        self.load()

    def load(self):
        by_id = {}
        recorded_output = {}
        chars = {}
        order = []
        with open(self.path, encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                record = json.loads(line)
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") == "user" and not self.prompt and not record.get("isMeta"):
                    content = message.get("content")
                    text = text_of(content)
                    if text and "<system-reminder>" not in text[:20]:
                        self.prompt = text
                if message.get("role") != "assistant":
                    continue
                self.model = message.get("model") or self.model
                # A streamed response is written once per content block with the same id.
                key = message.get("id") or id(record)
                usage = message.get("usage")
                if usage and key not in by_id:
                    order.append(key)
                by_id[key] = usage or by_id.get(key)
                if usage:
                    recorded_output[key] = max(recorded_output.get(key, 0), usage.get("output_tokens") or 0)
                chars[key] = chars.get(key, 0) + visible_chars(message.get("content"))
                for block in message.get("content") or []:
                    if block.get("type") != "tool_use":
                        continue
                    self.tools[block.get("name")] = self.tools.get(block.get("name"), 0) + 1
                    self.reads.extend(read_paths(block.get("name"), block.get("input") or {}))
        self.usages = [
            dict(by_id[key], output_tokens=max(recorded_output.get(key, 0), round(chars.get(key, 0) / CHARS_PER_TOKEN)))
            for key in order
            if by_id.get(key)
        ]

    @property
    def header(self):
        return dict(HEADER_FIELD.findall(self.prompt))

    def metrics(self):
        usages = self.usages
        totals = {
            key: sum(usage.get(key) or 0 for usage in usages)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
        }
        last = usages[-1] if usages else {}
        return {
            "label": self.label,
            "agent_type": self.agent_type,
            "model": self.model,
            "requests": len(usages),
            "start": context_size(usages[0]) if usages else 0,
            "peak": max((context_size(usage) for usage in usages), default=0),
            "final_context": context_size(last) + (last.get("output_tokens") or 0),
            "input": totals["input_tokens"],
            "cache_write": totals["cache_creation_input_tokens"],
            "cache_read": totals["cache_read_input_tokens"],
            "output": totals["output_tokens"],
            "weighted": round(sum(weighted(usage) for usage in usages)),
            "tool_calls": sum(self.tools.values()),
        }


def subagent_label(run):
    role = (run.agent_type or "agent").rsplit(":", 1)[-1]
    header = run.header
    parts = [header.get("ROLE", role)]
    if header.get("MODE"):
        parts.append(header["MODE"])
    if header.get("TASKS"):
        parts.append(header["TASKS"])
    return " ".join(parts)


def load_session(path):
    runs = [Run(path)]
    folder = path.with_suffix("") / "subagents"
    if folder.is_dir():
        for transcript in sorted(folder.glob("agent-*.jsonl"), key=lambda item: item.stat().st_mtime):
            meta_path = transcript.with_suffix(".meta.json")
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
            if meta.get("isFork"):
                continue
            run = Run(transcript, agent_type=meta.get("agentType", ""))
            run.label = subagent_label(run)
            runs.append(run)
    return runs


def agent_spend(project, agent_id):
    """Weighted spend and final context of one subagent, or None if its transcript is not found."""
    path = find_agent_transcript(project, agent_id)
    if path is None:
        return None
    metrics = Run(path).metrics()
    return {"weighted": metrics["weighted"], "final_context": metrics["final_context"], "requests": metrics["requests"]}


def relative(project, path):
    for root in {str(Path(project).absolute()), str(Path(project).resolve())}:
        if path.startswith(root + "/"):
            return path[len(root) + 1:]
    return path


def repeated_reads(project, runs):
    usage = {}
    for index, run in enumerate(runs):
        for path in run.reads:
            entry = usage.setdefault(relative(project, path), {"runs": set(), "reads": 0})
            entry["runs"].add(index)
            entry["reads"] += 1
    rows = [
        {"path": path, "runs": len(entry["runs"]), "reads": entry["reads"]}
        for path, entry in usage.items()
        if entry["reads"] > 1
    ]
    return sorted(rows, key=lambda row: (-row["reads"], -row["runs"], row["path"]))


def summarize(project, runs):
    rows = [run.metrics() for run in runs]
    total = sum(row["weighted"] for row in rows)
    producer = rows[0]["weighted"] if rows else 0
    total_input = sum(row["input"] + row["cache_write"] + row["cache_read"] for row in rows)
    prefix = sum(row["start"] * row["requests"] for row in rows[1:])
    agent_input = sum(row["input"] + row["cache_write"] + row["cache_read"] for row in rows[1:])
    return {
        "runs": rows,
        "total_weighted": total,
        "producer_weighted": producer,
        "agents_weighted": total - producer,
        "producer_requests": rows[0]["requests"] if rows else 0,
        "agent_requests": sum(row["requests"] for row in rows[1:]),
        "agent_prefix_share": round(prefix / agent_input, 3) if agent_input else 0,
        "input_tokens_processed": total_input,
        "repeated_reads": repeated_reads(project, runs),
    }


def short(number):
    number = float(number)
    if number >= 1_000_000:
        return f"{number / 1_000_000:.2f}M"
    if number >= 1000:
        return f"{number / 1000:.1f}K"
    return str(int(number))


def print_report(session, summary, top):
    rows = summary["runs"]
    print(f"Session {session.stem}: producer + {len(rows) - 1} agent runs")
    print(f"{'run':<34} {'req':>4} {'start':>7} {'peak':>7} {'cw':>7} {'cr':>8} {'out':>7} {'weighted':>9}")
    for row in rows:
        print(
            f"{row['label'][:34]:<34} {row['requests']:>4} {short(row['start']):>7} {short(row['peak']):>7} "
            f"{short(row['cache_write']):>7} {short(row['cache_read']):>8} {short(row['output']):>7} {short(row['weighted']):>9}"
        )
    total = summary["total_weighted"] or 1
    print(
        f"TOTAL weighted {short(summary['total_weighted'])}: producer {short(summary['producer_weighted'])} "
        f"({summary['producer_weighted'] / total:.0%}, {summary['producer_requests']} requests), "
        f"agents {short(summary['agents_weighted'])} ({summary['agent_requests']} requests)"
    )
    print(f"Agent start prefix re-sent: {summary['agent_prefix_share']:.0%} of agent input")
    reads = summary["repeated_reads"][:top]
    if reads:
        print("Read more than once (path: runs, reads):")
        for row in reads:
            print(f"  {row['path']}: {row['runs']}, {row['reads']}")


def command_report(args):
    session = resolve_session(args.project, args.session)
    summary = summarize(args.project, load_session(session))
    if args.json:
        print(json.dumps(dict(summary, session=str(session)), ensure_ascii=False, indent=2))
        return 0
    print_report(session, summary, args.top)
    return 0


def command_agent(args):
    spend = agent_spend(args.project, args.agent_id)
    if spend is None:
        print(f"tokens: no transcript for agent {args.agent_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(spend))
        return 0
    print(f"weighted={spend['weighted']} context={spend['final_context']} requests={spend['requests']}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Token spend from Claude Code transcripts.")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="spend of a session: producer, every agent run, repeated reads")
    report.add_argument("--session", help="session id or path to its .jsonl (default: the latest)")
    report.add_argument("--top", type=int, default=10, help="how many repeated reads to show")
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=command_report)

    agent = sub.add_parser("agent", help="weighted spend and final context of one subagent")
    agent.add_argument("agent_id")
    agent.add_argument("--json", action="store_true")
    agent.set_defaults(handler=command_agent)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except TokensError as error:
        print(f"tokens: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
