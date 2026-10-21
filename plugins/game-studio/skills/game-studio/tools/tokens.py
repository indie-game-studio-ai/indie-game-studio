#!/usr/bin/env python3
"""Real token spend of a studio session, read from Claude Code and Codex transcripts.

Claude Code keeps every session in ~/.claude/projects/<project slug>/<session>.jsonl and
its subagents in <session>/subagents/agent-<id>.jsonl. Each API request records its
usage there. The figure the Agent tool reports (`subagent_tokens`) is only the size of
the last request; the spend is the sum over all requests.

Codex keeps every thread, the producer's and each agent's, in
~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl. The first record names the
thread's working directory, its root session, its role and its agent path
(`/root/<task name>`); a `token_usage_record` per response holds the usage.

"Weighted" tokens are input-equivalent tokens at Claude's price ratios:
input ×1, cache write ×1.25 (5 min) or ×2 (1 h), cache read ×0.1, output ×5. Codex has
no cache write price, so its uncached input counts ×1 on the same scale.

Input-side figures are exact. A Claude Code transcript records the usage of a streamed
response as it starts, so output is taken as the larger of the recorded count and the
visible text at CHARS_PER_TOKEN; hidden thinking is not counted.
"""
import argparse
import json
import os
import re
import shlex
import sys
from datetime import datetime
from pathlib import Path

CACHE_WRITE_5M = 1.25
CACHE_WRITE_1H = 2.0
CACHE_READ = 0.1
OUTPUT = 5.0
CHARS_PER_TOKEN = 3.5

CACHE_TTL_5M = 300
CACHE_TTL_1H = 3600
# OpenAI keeps a cached prefix for 5 to 10 idle minutes; take the short end.
CODEX_CACHE_TTL = 300
# A later request that reads less than this share of its context from the cache wrote
# it again: an agent continued after its cache expired, or a pause longer than the TTL.
COLD_SHARE = 0.5

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


def is_cold(usage):
    size = context_size(usage)
    return size > 0 and (usage.get("cache_read_input_tokens") or 0) < COLD_SHARE * size


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


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
        self.times = []
        self.tools = {}
        self.reads = []
        self.prompt = ""
        self.model = ""
        self.load()

    def cache_ttl(self):
        split = [usage.get("cache_creation") or {} for usage in self.usages]
        return CACHE_TTL_1H if any(part.get("ephemeral_1h_input_tokens") for part in split) else CACHE_TTL_5M

    def load(self):
        by_id = {}
        recorded_output = {}
        chars = {}
        order = []
        started = {}
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
                    started[key] = parse_time(record.get("timestamp"))
                by_id[key] = usage or by_id.get(key)
                if usage:
                    recorded_output[key] = max(recorded_output.get(key, 0), usage.get("output_tokens") or 0)
                chars[key] = chars.get(key, 0) + visible_chars(message.get("content"))
                for block in message.get("content") or []:
                    if block.get("type") != "tool_use":
                        continue
                    self.tools[block.get("name")] = self.tools.get(block.get("name"), 0) + 1
                    self.reads.extend(read_paths(block.get("name"), block.get("input") or {}))
        kept = [key for key in order if by_id.get(key)]
        self.usages = [
            dict(by_id[key], output_tokens=max(recorded_output.get(key, 0), round(chars.get(key, 0) / CHARS_PER_TOKEN)))
            for key in kept
        ]
        self.times = [started.get(key) for key in kept]

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
            "cold": sum(1 for usage in usages[1:] if is_cold(usage)),
            "last_at": next((moment for moment in reversed(self.times) if moment), None),
            "ttl": self.cache_ttl(),
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


# --- Codex -----------------------------------------------------------------------------

def codex_home():
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def codex_usage(usage):
    """A Codex usage in the Claude Code shape: cached input is a cache read, the rest input."""
    cached = usage.get("cached_input_tokens") or 0
    return {
        "input_tokens": max((usage.get("input_tokens") or 0) - cached, 0),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cached,
        "output_tokens": usage.get("output_tokens") or 0,
    }


def rollout_meta(path):
    with open(path, encoding="utf-8") as stream:
        for _, line in zip(range(5), stream):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                return None
            if record.get("type") == "session_meta":
                return record.get("payload") or {}
    return None


def codex_threads(project):
    """Codex threads that ran in this project: {thread id: {"meta", "paths", "mtime"}}.
    A resumed thread may continue in another rollout file."""
    roots = {str(Path(project).absolute()), str(Path(project).resolve())}
    folder = codex_home() / "sessions"
    threads = {}
    for path in sorted(folder.rglob("rollout-*.jsonl")) if folder.is_dir() else ():
        meta = rollout_meta(path)
        if not meta or not meta.get("id") or meta.get("cwd") not in roots:
            continue
        thread = threads.setdefault(meta["id"], {"meta": meta, "paths": [], "mtime": 0})
        thread["paths"].append(path)
        thread["mtime"] = max(thread["mtime"], path.stat().st_mtime)
    return threads


def codex_source(meta):
    """user (a session someone started), subagent or guardian_review."""
    return meta.get("thread_source") or ("subagent" if meta.get("parent_thread_id") else "user")


def codex_agent_name(meta):
    source = meta.get("source")
    spawn = ((source.get("subagent") or {}).get("thread_spawn") or {}) if isinstance(source, dict) else {}
    return (meta.get("agent_path") or spawn.get("agent_path") or "").rsplit("/", 1)[-1]


class CodexRun(Run):
    """One Codex thread: the producer or one agent."""

    def __init__(self, thread, label="producer", agent_type=""):
        self.thread = thread
        super().__init__(thread["paths"][0], label, agent_type)

    def cache_ttl(self):
        return CODEX_CACHE_TTL

    def load(self):
        responses = {}
        totals = []
        for path in self.thread["paths"]:
            with open(path, encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    kind = record.get("type")
                    payload = record.get("payload") or {}
                    moment = parse_time(record.get("timestamp"))
                    if kind == "turn_context":
                        self.model = payload.get("model") or self.model
                    elif kind == "token_usage_record":
                        key = payload.get("response_id") or f"{path.name}:{len(responses)}"
                        responses.setdefault(key, (moment, payload.get("usage") or {}))
                    elif kind == "event_msg" and payload.get("type") == "token_count":
                        total = (payload.get("info") or {}).get("total_token_usage")
                        if total:
                            totals.append((moment, total))
                    elif kind == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
                        name = payload.get("name") or "?"
                        self.tools[name] = self.tools.get(name, 0) + 1
        entries = list(responses.values())
        if not entries and totals:
            # Older rollouts have no record per response, only a running total.
            entries = [max(totals, key=lambda entry: entry[1].get("total_tokens") or 0)]
        self.usages = [codex_usage(usage) for _, usage in entries]
        self.times = [moment for moment, _ in entries]


def latest_codex_session(threads):
    roots = [thread for thread in threads.values() if codex_source(thread["meta"]) == "user"]
    return max(roots, key=lambda thread: thread["mtime"], default=None)


def load_codex_session(threads, root):
    runs = [CodexRun(root)]
    root_id = root["meta"]["id"]
    children = [
        thread for thread in threads.values()
        if thread["meta"].get("session_id") == root_id and thread["meta"]["id"] != root_id
    ]
    for thread in sorted(children, key=lambda item: item["mtime"]):
        meta = thread["meta"]
        role = meta.get("agent_role") or codex_source(meta)
        run = CodexRun(thread, agent_type=role)
        run.label = " ".join(part for part in (re.sub(r"^studio-", "", role), codex_agent_name(meta)) if part)
        runs.append(run)
    return runs


def find_codex_thread(project, agent_id):
    """A Codex agent by thread id or by name (`/root/s1_w2_artist` or `s1_w2_artist`);
    the latest one if a name repeats."""
    name = agent_id.rsplit("/", 1)[-1]
    matches = [
        thread for thread in codex_threads(project).values()
        if thread["meta"]["id"] == agent_id or (name and codex_agent_name(thread["meta"]) == name)
    ]
    return max(matches, key=lambda thread: thread["mtime"], default=None)


# --- both platforms ----------------------------------------------------------------------

def latest_producer(project, threads):
    """The newest producer session on either platform: a Claude Code transcript path, a
    Codex thread, or None."""
    try:
        claude = latest_session(project)
    except TokensError:
        claude = None
    codex = latest_codex_session(threads)
    if codex and (claude is None or codex["mtime"] > claude.stat().st_mtime):
        return codex
    return claude


def producer_run(project):
    """(session name, Run) of the latest producer session, or None."""
    latest = latest_producer(project, codex_threads(project))
    if latest is None:
        return None
    if isinstance(latest, Path):
        return latest.stem, Run(latest)
    return latest["meta"]["id"], CodexRun(latest)


def session_runs(project, session=None):
    """(session name, runs) of the given session or the latest one, on either platform."""
    threads = codex_threads(project)
    if session and session in threads:
        return session, load_codex_session(threads, threads[session])
    latest = resolve_session(project, session) if session else latest_producer(project, threads)
    if latest is None:
        raise TokensError(f"no Claude Code or Codex sessions for {project}")
    if isinstance(latest, Path):
        return latest.stem, load_session(latest)
    return latest["meta"]["id"], load_codex_session(threads, latest)


def agent_spend(project, agent_id):
    """Spend, final context and cache state of one agent, or None if its transcript is not
    found. Claude Code agents are found by agent id, Codex agents by thread id or name."""
    path = find_agent_transcript(project, agent_id)
    if path is not None:
        run = Run(path)
    else:
        thread = find_codex_thread(project, agent_id)
        if thread is None:
            return None
        run = CodexRun(thread)
    metrics = run.metrics()
    return {key: metrics[key] for key in ("weighted", "final_context", "requests", "cold", "last_at", "ttl")}


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
        "cold_requests": sum(row["cold"] for row in rows),
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


def print_report(name, summary, top):
    rows = summary["runs"]
    print(f"Session {name}: producer + {len(rows) - 1} agent runs")
    print(f"{'run':<34} {'req':>4} {'start':>7} {'peak':>7} {'cw':>7} {'cr':>8} {'out':>7} {'cold':>5} {'weighted':>9}")
    for row in rows:
        print(
            f"{row['label'][:34]:<34} {row['requests']:>4} {short(row['start']):>7} {short(row['peak']):>7} "
            f"{short(row['cache_write']):>7} {short(row['cache_read']):>8} {short(row['output']):>7} "
            f"{row['cold']:>5} {short(row['weighted']):>9}"
        )
    total = summary["total_weighted"] or 1
    print(
        f"TOTAL weighted {short(summary['total_weighted'])}: producer {short(summary['producer_weighted'])} "
        f"({summary['producer_weighted'] / total:.0%}, {summary['producer_requests']} requests), "
        f"agents {short(summary['agents_weighted'])} ({summary['agent_requests']} requests)"
    )
    print(f"Agent start prefix re-sent: {summary['agent_prefix_share']:.0%} of agent input")
    if summary["cold_requests"]:
        print(f"Cold cache: {summary['cold_requests']} requests wrote their context again "
              "(an agent continued after its cache expired, or a long pause)")
    reads = summary["repeated_reads"][:top]
    if reads:
        print("Read more than once (path: runs, reads):")
        for row in reads:
            print(f"  {row['path']}: {row['runs']}, {row['reads']}")


def command_report(args):
    name, runs = session_runs(args.project, args.session)
    summary = summarize(args.project, runs)
    if args.json:
        print(json.dumps(dict(summary, session=name), ensure_ascii=False, indent=2))
        return 0
    print_report(name, summary, args.top)
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
    parser = argparse.ArgumentParser(description="Token spend from Claude Code and Codex transcripts.")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="spend of a session: producer, every agent run, repeated reads")
    report.add_argument("--session", help="Claude Code session id or .jsonl path, or Codex thread id (default: the latest)")
    report.add_argument("--top", type=int, default=10, help="how many repeated reads to show")
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=command_report)

    agent = sub.add_parser("agent", help="weighted spend and final context of one subagent")
    agent.add_argument("agent_id", help="Claude Code agent id, or Codex thread id or agent name")
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
