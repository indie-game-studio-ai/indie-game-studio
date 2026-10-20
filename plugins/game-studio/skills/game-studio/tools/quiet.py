#!/usr/bin/env python3
"""Runs a command, keeps the full output in a log file and prints only what matters.

Test runners, installers and engines print thousands of lines. Reading them costs
tokens and hides the failure. quiet.py prints one status line on success and the
error lines plus a short tail on failure. It exits with the command's exit code.
"""
import argparse
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SIGNAL = re.compile(
    r"error|fail|✗|✖|×|panic|exception|traceback|assert|expected|received|"
    r"cannot|not found|undefined|segmentation|warning:|E\d{3,}|TS\d{4}",
    re.IGNORECASE,
)
SUMMARY = re.compile(r"\b(\d+ (passed|failed|skipped|tests?|errors?)|tests? (passed|failed)|ok\b|PASS|FAIL)", re.IGNORECASE)
MAX_LINE = 300


def log_dir():
    studio = Path("Studio")
    if (studio / "studio.json").is_file():
        return studio / ".state" / "logs"
    return Path(tempfile.gettempdir()) / "game-studio-logs"


def slug(command):
    text = re.sub(r"[^A-Za-z0-9]+", "-", command).strip("-").lower()
    return text[:40] or "command"


def clip(line):
    return line if len(line) <= MAX_LINE else line[:MAX_LINE] + " …"


def signal_lines(lines, limit):
    picked, seen = [], set()
    for number, line in enumerate(lines, 1):
        if not SIGNAL.search(line):
            continue
        key = line.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        picked.append(f"{number:>6}: {clip(line)}")
        if len(picked) >= limit:
            break
    return picked


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a command quietly: full output to a log, short summary to stdout.")
    parser.add_argument("command", nargs="+", help="the command; a single argument is run through the shell")
    parser.add_argument("--log", help="log file path (default: Studio/.state/logs/<time>-<command>.log)")
    parser.add_argument("--signal", type=int, default=30, help="max error lines to print on failure")
    parser.add_argument("--tail", type=int, default=12, help="tail lines to print on failure")
    parser.add_argument("--ok-tail", type=int, default=0, help="tail lines to print on success")
    args = parser.parse_args(argv)

    shell = len(args.command) == 1
    command = args.command[0] if shell else args.command
    shown = command if shell else " ".join(command)

    log_path = Path(args.log) if args.log else log_dir() / f"{time.strftime('%Y%m%d-%H%M%S')}-{slug(shown)}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    result = subprocess.run(command, shell=shell, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = time.time() - started
    output = result.stdout.decode("utf-8", errors="replace")
    log_path.write_text(f"$ {shown}\n{output}", encoding="utf-8")
    lines = output.splitlines()

    print(f"$ {shown}")
    print(f"exit {result.returncode} · {elapsed:.1f}s · {len(lines)} lines · log: {log_path}")
    if result.returncode == 0:
        summary = [clip(line) for line in lines[-15:] if SUMMARY.search(line)][-3:]
        for line in summary:
            print(f"  {line.strip()}")
        for line in lines[-args.ok_tail:] if args.ok_tail else []:
            print(f"  {clip(line)}")
        return 0

    picked = signal_lines(lines, args.signal)
    if picked:
        print(f"--- error lines ({len(picked)}):")
        print("\n".join(picked))
    if args.tail:
        print(f"--- last {min(args.tail, len(lines))} lines:")
        for line in lines[-args.tail:]:
            print(clip(line))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
