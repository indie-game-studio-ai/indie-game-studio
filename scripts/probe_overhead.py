#!/usr/bin/env python3
"""Measures what each studio agent costs before it does anything.

Starts `claude -p` with the plugin in an empty temporary project and asks it to launch
every role once with a one-word task, then reads the transcripts. The first request of
an agent is its fixed start: system prompt, tool definitions and everything Claude Code
adds. Every later request re-sends it, so it multiplies by the number of requests.

This spends real tokens (about 10K per role on Sonnet). It is not part of CI.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "game-studio"
sys.path.insert(0, str(PLUGIN / "skills" / "game-studio" / "tools"))

import tokens  # noqa: E402

ROLES = sorted(path.stem for path in (ROOT / "roles").glob("*.md") if not path.name.startswith("_"))


def main():
    parser = argparse.ArgumentParser(description="Start cost of every studio agent, from a live run.")
    parser.add_argument("--roles", default="all", help="comma-separated roles (default: all)")
    parser.add_argument("--model", default="sonnet", help="model of the launching session")
    parser.add_argument("--plugin-dir", default=str(PLUGIN))
    parser.add_argument("--max-start", type=int, default=12000, help="fail if an agent starts above this")
    args = parser.parse_args()

    roles = ROLES if args.roles == "all" else [role.strip() for role in args.roles.split(",") if role.strip()]
    project = Path(tempfile.mkdtemp(prefix="studio-probe-"))
    calls = ", ".join(f'"game-studio:{role}"' for role in roles)
    prompt = (
        f"Call the Agent tool once for each of these subagent types, all in one message: {calls}. "
        'Each call has description "probe" and prompt "Reply with exactly the word OK. Use no tools." '
        "When all have answered, reply DONE."
    )
    result = subprocess.run(
        ["claude", "-p", "--plugin-dir", args.plugin_dir, "--model", args.model,
         "--max-turns", "4", "--output-format", "json", prompt],
        cwd=project, capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(result.stdout + result.stderr, file=sys.stderr)
        return 2
    session_id = json.loads(result.stdout)["session_id"]
    session = tokens.resolve_session(project, session_id)
    runs = tokens.load_session(session)[1:]

    print(f"session {session_id} in {project}")
    print(f"{'role':<20} {'start':>7} {'requests':>9}")
    failed = []
    for run in sorted(runs, key=lambda item: item.agent_type):
        metrics = run.metrics()
        role = run.agent_type.rsplit(":", 1)[-1]
        print(f"{role:<20} {metrics['start']:>7} {metrics['requests']:>9}")
        if metrics["start"] > args.max_start:
            failed.append(role)
    missing = sorted(set(roles) - {run.agent_type.rsplit(':', 1)[-1] for run in runs})
    if missing:
        print(f"not launched: {', '.join(missing)}")
    if failed:
        print(f"over {args.max_start}: {', '.join(failed)}")
    return 1 if failed or missing else 0


if __name__ == "__main__":
    sys.exit(main())
