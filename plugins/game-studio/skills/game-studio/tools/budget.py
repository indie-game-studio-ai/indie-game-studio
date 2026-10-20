#!/usr/bin/env python3
"""Studio token budget: profiles, model routing and per-sprint spend.

Profiles set the limits, the meeting format and the review policy in
Studio/studio.json. Routing picks a model (Claude Code) and a reasoning effort
(Codex) for a role and mode. The spend log lives in Studio/.state/budget.jsonl.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

PROFILES = {
    "lean": {
        "limits": {"parallel_agents": 3, "features_per_sprint": 1, "rework_cycles": 2, "max_sprints": 3},
        "budget": {
            "max_agent_runs_per_sprint": 15,
            "max_tokens_per_sprint": 1200000,
            "meeting": "none",
            "reviews": "per-feature",
            "reuse_reviewers": True,
            "verify": "sprint-end",
            "tests_first": False,
        },
    },
    "standard": {
        "limits": {"parallel_agents": 4, "features_per_sprint": 2, "rework_cycles": 3, "max_sprints": 5},
        "budget": {
            "max_agent_runs_per_sprint": 30,
            "max_tokens_per_sprint": 3000000,
            "meeting": "involved",
            "reviews": "batched",
            "reuse_reviewers": True,
            "verify": "sprint-end",
            "tests_first": True,
        },
    },
    "thorough": {
        "limits": {"parallel_agents": 4, "features_per_sprint": 2, "rework_cycles": 3, "max_sprints": 8},
        "budget": {
            "max_agent_runs_per_sprint": 60,
            "max_tokens_per_sprint": 8000000,
            "meeting": "full",
            "reviews": "every",
            "reuse_reviewers": False,
            "verify": "per-feature",
            "tests_first": True,
        },
    },
}

# Groups of work by how much judgement they need.
DECIDE = "decide"
REVIEW = "review"
BUILD = "build"
CONTENT = "content"
REPLY = "reply"

ROUTES = {
    DECIDE: {"lean": ("sonnet", "medium"), "standard": ("opus", "high"), "thorough": ("opus", "high")},
    REVIEW: {"lean": ("sonnet", "medium"), "standard": ("sonnet", "medium"), "thorough": ("opus", "high")},
    BUILD: {"lean": ("sonnet", "medium"), "standard": ("sonnet", "medium"), "thorough": ("sonnet", "high")},
    CONTENT: {"lean": ("haiku", "low"), "standard": ("sonnet", "medium"), "thorough": ("sonnet", "medium")},
    REPLY: {"lean": ("haiku", "low"), "standard": ("haiku", "low"), "thorough": ("sonnet", "medium")},
}
ESCALATED = ("opus", "high")

LEADS = {"game-designer", "architect", "art-director"}
CONTENT_ROLES = {"sound-designer", "narrative-designer", "level-designer"}
ROLES = (
    "game-designer", "art-director", "architect", "artist", "developer", "qa",
    "sound-designer", "narrative-designer", "level-designer",
)

WARN_SHARE = 0.8


class BudgetError(Exception):
    pass


def route_group(role, mode):
    if mode == "MEETING":
        return REPLY
    if mode in ("REVIEW", "CODE_REVIEW", "SCREEN_REVIEW"):
        return REVIEW
    if mode in ("KICKOFF_TREE", "SPEC", "PLAN", "ACCEPT"):
        return DECIDE
    if mode == "PREPRO" and role in LEADS:
        return DECIDE
    if role in CONTENT_ROLES:
        return CONTENT
    return BUILD


def route(profile, role, mode, escalate=False):
    if profile not in PROFILES:
        raise BudgetError(f"unknown profile {profile!r}; allowed: {', '.join(PROFILES)}")
    if role not in ROLES:
        raise BudgetError(f"unknown role {role!r}")
    if escalate:
        return ESCALATED
    return ROUTES[route_group(role, mode)][profile]


class Studio:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.config_path = self.project / "Studio" / "studio.json"
        self.log_path = self.project / "Studio" / ".state" / "budget.jsonl"

    def config(self):
        if not self.config_path.is_file():
            raise BudgetError(f"no {self.config_path}")
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def profile(self):
        return self.config().get("budget", {}).get("profile", "standard")

    def caps(self):
        budget = dict(PROFILES[self.profile()]["budget"])
        budget.update(self.config().get("budget", {}))
        return budget

    def current_sprint(self):
        ledger = self.project / "Studio" / "ledger.md"
        if ledger.is_file():
            match = re.search(r"^- Sprint:\s*(\d+)", ledger.read_text(encoding="utf-8"), re.M)
            if match:
                return int(match.group(1))
        return 0

    def entries(self, sprint=None):
        if not self.log_path.is_file():
            return []
        result = []
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            if sprint is None or entry.get("sprint") == sprint:
                result.append(entry)
        return result


def command_profile(studio, args):
    if args.name not in PROFILES:
        raise BudgetError(f"unknown profile {args.name!r}; allowed: {', '.join(PROFILES)}")
    config = studio.config()
    preset = PROFILES[args.name]
    config.setdefault("limits", {}).update(preset["limits"])
    config["budget"] = dict(preset["budget"], profile=args.name)
    studio.config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    budget = config["budget"]
    print(
        f"profile {args.name}: meeting={budget['meeting']} reviews={budget['reviews']} "
        f"verify={budget['verify']} tests_first={budget['tests_first']} "
        f"runs/sprint≤{budget['max_agent_runs_per_sprint']} tokens/sprint≤{budget['max_tokens_per_sprint']}"
    )


def command_route(studio, args):
    profile = args.profile or studio.profile()
    model, effort = route(profile, args.role, args.mode, args.escalate)
    print(f"claude={model} codex={effort}")


def command_routes(studio, args):
    profile = args.profile or studio.profile()
    groups = {
        DECIDE: "KICKOFF_TREE, SPEC, PLAN, ACCEPT, PREPRO of leads",
        REVIEW: "REVIEW, CODE_REVIEW, SCREEN_REVIEW",
        BUILD: "developer, qa, artist work; MEETING_RESOLVE",
        CONTENT: "sound, narrative, level PREPRO/WORK/FIX",
        REPLY: "MEETING replies",
    }
    print(f"profile {profile} (escalation after the rework limit: claude={ESCALATED[0]} codex={ESCALATED[1]})")
    for group, label in groups.items():
        model, effort = ROUTES[group][profile]
        print(f"  {group:<8} claude={model:<7} codex={effort:<7} {label}")


def command_log(studio, args):
    if args.role not in ROLES:
        raise BudgetError(f"unknown role {args.role!r}")
    entry = {
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "sprint": args.sprint if args.sprint is not None else studio.current_sprint(),
        "role": args.role,
        "mode": args.mode,
        "tasks": [item.strip() for item in (args.tasks or "").split(",") if item.strip()],
        "tokens": args.tokens,
        "model": args.model or "",
    }
    studio.log_path.parent.mkdir(parents=True, exist_ok=True)
    with studio.log_path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"logged: S{entry['sprint']} {args.role} {args.mode} tokens={args.tokens if args.tokens is not None else '?'}")


def spend(entries):
    runs = len(entries)
    tokens = sum(entry["tokens"] for entry in entries if entry.get("tokens") is not None)
    unknown = sum(1 for entry in entries if entry.get("tokens") is None)
    return runs, tokens, unknown


def command_report(studio, args):
    sprint = args.sprint if args.sprint is not None else studio.current_sprint()
    entries = studio.entries(sprint)
    caps = studio.caps()
    runs, tokens, unknown = spend(entries)
    by_role = {}
    for entry in entries:
        role_runs, role_tokens = by_role.get(entry["role"], (0, 0))
        by_role[entry["role"]] = (role_runs + 1, role_tokens + (entry.get("tokens") or 0))
    if args.json:
        print(json.dumps({
            "sprint": sprint, "profile": studio.profile(), "runs": runs, "tokens": tokens,
            "runs_without_tokens": unknown, "caps": caps,
            "by_role": {role: {"runs": r, "tokens": t} for role, (r, t) in by_role.items()},
        }, ensure_ascii=False, indent=2))
        return
    print(
        f"Sprint {sprint} ({studio.profile()}): runs {runs}/{caps['max_agent_runs_per_sprint']}, "
        f"tokens {tokens:,}/{caps['max_tokens_per_sprint']:,}"
        + (f" ({unknown} runs without a token count)" if unknown else "")
    )
    for role, (role_runs, role_tokens) in sorted(by_role.items(), key=lambda item: -item[1][1]):
        print(f"  {role:<19} runs {role_runs:<3} tokens {role_tokens:,}")


def command_check(studio, args):
    sprint = args.sprint if args.sprint is not None else studio.current_sprint()
    caps = studio.caps()
    runs, tokens, _ = spend(studio.entries(sprint))
    planned_runs = runs + args.next
    run_cap = caps["max_agent_runs_per_sprint"]
    token_cap = caps["max_tokens_per_sprint"]
    line = f"S{sprint}: runs {runs}+{args.next}/{run_cap}, tokens {tokens:,}/{token_cap:,}"
    if planned_runs > run_cap or tokens >= token_cap:
        print(f"OVER BUDGET — {line}. Call an early gate.")
        sys.exit(1)
    if planned_runs >= run_cap * WARN_SHARE or tokens >= token_cap * WARN_SHARE:
        print(f"WARN — {line}. Over {int(WARN_SHARE * 100)}% of the sprint budget.")
        return
    print(f"OK — {line}")


def build_parser():
    parser = argparse.ArgumentParser(description="Studio token budget: profiles, routing, spend.")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    profile = sub.add_parser("profile", help="apply a budget profile to Studio/studio.json")
    profile.add_argument("name", help="lean | standard | thorough")
    profile.set_defaults(handler=command_profile)

    route_parser = sub.add_parser("route", help="model and effort for one role and mode")
    route_parser.add_argument("role")
    route_parser.add_argument("mode")
    route_parser.add_argument("--escalate", action="store_true", help="the task exceeded the rework limit")
    route_parser.add_argument("--profile", help="override the profile from studio.json")
    route_parser.set_defaults(handler=command_route)

    routes = sub.add_parser("routes", help="the routing table of the active profile")
    routes.add_argument("--profile", help="override the profile from studio.json")
    routes.set_defaults(handler=command_routes)

    log = sub.add_parser("log", help="record one agent run")
    log.add_argument("--role", required=True)
    log.add_argument("--mode", required=True)
    log.add_argument("--tasks", help="comma-separated task ids")
    log.add_argument("--tokens", type=int, help="tokens the platform reported for the run")
    log.add_argument("--model", help="model or effort used")
    log.add_argument("--sprint", type=int, help="defaults to the sprint in Studio/ledger.md")
    log.set_defaults(handler=command_log)

    report = sub.add_parser("report", help="spend of a sprint")
    report.add_argument("--sprint", type=int)
    report.add_argument("--json", action="store_true")
    report.set_defaults(handler=command_report)

    check = sub.add_parser("check", help="exit 1 if the next wave would exceed the sprint budget")
    check.add_argument("--next", type=int, default=0, help="agent runs planned for the next wave")
    check.add_argument("--sprint", type=int)
    check.set_defaults(handler=command_check)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.handler(Studio(args.project), args)
    except BudgetError as error:
        print(f"budget: {error}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
