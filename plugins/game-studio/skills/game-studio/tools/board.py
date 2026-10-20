#!/usr/bin/env python3
"""Studio task board: Studio/tasks/T-NNN.md.

Only the producer writes the board, and only through this script.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROLES = (
    "game-designer",
    "art-director",
    "architect",
    "artist",
    "developer",
    "qa",
    "sound-designer",
    "narrative-designer",
    "level-designer",
)

MAKER_MODES = {"PREPRO", "MEETING", "WORK", "FIX"}
MODES = {
    "game-designer": {"KICKOFF_TREE", "PREPRO", "SPEC", "MEETING_RESOLVE", "REVIEW", "ACCEPT"},
    "architect": {"KICKOFF_TREE", "PREPRO", "MEETING", "PLAN", "CODE_REVIEW"},
    "art-director": {"PREPRO", "MEETING", "REVIEW", "SCREEN_REVIEW"},
    "artist": MAKER_MODES,
    "developer": {"WORK", "INTEGRATE", "FIX"},
    "qa": {"MEETING", "WRITE_TESTS", "VERIFY", "FIX"},
    "sound-designer": MAKER_MODES,
    "narrative-designer": MAKER_MODES,
    "level-designer": MAKER_MODES,
}
FIX_MODE = "FIX"

STATUSES = ("todo", "doing", "review", "changes", "done", "blocked")
STATUS_ICONS = {
    "todo": "⬜",
    "doing": "🔨",
    "review": "👀",
    "changes": "🔁",
    "done": "✅",
    "blocked": "⛔",
}

KEY_ORDER = (
    "id",
    "title",
    "feature",
    "sprint",
    "owner",
    "mode",
    "status",
    "deps",
    "reviewers",
    "approvals",
    "inputs",
    "outputs",
    "rework",
)
LIST_KEYS = {"deps", "reviewers", "approvals", "inputs", "outputs"}
INT_KEYS = {"sprint", "rework"}

RUN_PRIORITY = {"ACCEPT": 1, "VERIFY": 2, "PLAN": 3, "WRITE_TESTS": 4}
TASK_FILE = re.compile(r"^T-(\d+)\.md$")
LOG_HEADER = "## Log"


class BoardError(Exception):
    pass


class Task:
    def __init__(self, path, meta, body):
        self.path = path
        self.meta = meta
        self.body = body

    @property
    def id(self):
        return self.meta.get("id", "")

    @property
    def number(self):
        match = re.match(r"^T-(\d+)$", self.id)
        return int(match.group(1)) if match else 0

    def get(self, key):
        if key in LIST_KEYS:
            return self.meta.get(key, [])
        if key in INT_KEYS:
            return self.meta.get(key, 0)
        return self.meta.get(key, "")

    def render(self):
        lines = ["---"]
        keys = list(KEY_ORDER) + [key for key in self.meta if key not in KEY_ORDER]
        for key in keys:
            if key not in self.meta and key not in LIST_KEYS and key not in INT_KEYS:
                continue
            value = self.get(key)
            if key in LIST_KEYS:
                lines.append(f"{key}: [{', '.join(value)}]")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        body = self.body if self.body.startswith("\n") else "\n" + self.body
        text = "\n".join(lines) + body
        return text if text.endswith("\n") else text + "\n"

    def save(self):
        self.path.write_text(self.render(), encoding="utf-8")

    def log(self, line):
        entry = f"- {line}"
        if LOG_HEADER not in self.body:
            self.body = self.body.rstrip("\n") + f"\n\n{LOG_HEADER}\n{entry}\n"
            return
        start = self.body.index(LOG_HEADER)
        next_section = self.body.find("\n## ", start + len(LOG_HEADER))
        if next_section == -1:
            self.body = self.body.rstrip("\n") + f"\n{entry}\n"
        else:
            head = self.body[:next_section].rstrip("\n")
            self.body = f"{head}\n{entry}\n{self.body[next_section:]}"


def parse_list(raw):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [item.strip() for item in raw.split(",") if item.strip()]


def parse_task(path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        raise BoardError(f"{path.name}: no frontmatter")
    end = text.find("\n---", 3)
    if end == -1:
        raise BoardError(f"{path.name}: frontmatter is not closed")
    meta = {}
    for line in text[3:end].strip("\n").splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        if not separator:
            raise BoardError(f"{path.name}: frontmatter line without ':' — {line!r}")
        key = key.strip()
        value = value.strip()
        if key in LIST_KEYS:
            meta[key] = parse_list(value)
        elif key in INT_KEYS:
            try:
                meta[key] = int(value or 0)
            except ValueError as error:
                raise BoardError(f"{path.name}: {key} must be a number") from error
        else:
            meta[key] = value
    body = text[end + len("\n---"):]
    return Task(path, meta, body)


class Board:
    def __init__(self, project):
        self.project = Path(project).resolve()
        self.tasks_dir = self.project / "Studio" / "tasks"

    def load(self):
        if not self.tasks_dir.is_dir():
            return []
        tasks = []
        for path in sorted(self.tasks_dir.iterdir()):
            if path.name.startswith("_") or not TASK_FILE.match(path.name):
                continue
            tasks.append(parse_task(path))
        return sorted(tasks, key=lambda task: (task.number, task.id))

    def find(self, task_id):
        task_id = normalize_id(task_id)
        for task in self.load():
            if task.id == task_id:
                return task
        raise BoardError(f"task {task_id} not found")

    def current_sprint(self):
        ledger = self.project / "Studio" / "ledger.md"
        if ledger.is_file():
            match = re.search(r"^- Sprint:\s*(\d+)", ledger.read_text(encoding="utf-8"), re.M)
            if match:
                return int(match.group(1))
        return 0

    def next_id(self):
        numbers = [task.number for task in self.load()]
        if self.tasks_dir.is_dir():
            for path in self.tasks_dir.iterdir():
                match = TASK_FILE.match(path.name)
                if match:
                    numbers.append(int(match.group(1)))
        return f"T-{max(numbers, default=0) + 1:03d}"


def normalize_id(task_id):
    task_id = task_id.strip()
    if re.match(r"^\d+$", task_id):
        return f"T-{int(task_id):03d}"
    return task_id


def check_role(role):
    if role not in ROLES:
        raise BoardError(f"unknown role {role!r}; allowed: {', '.join(ROLES)}")


def check_mode(role, mode):
    if mode not in MODES[role]:
        allowed = ", ".join(sorted(MODES[role]))
        raise BoardError(f"mode {mode!r} does not fit role {role}; allowed: {allowed}")


def check_status(status):
    if status not in STATUSES:
        raise BoardError(f"unknown status {status!r}; allowed: {', '.join(STATUSES)}")


def producer_line(task, text):
    return f"S{task.get('sprint')} producer: {text}"


def change_status(task, status, note=""):
    old = task.get("status")
    task.meta["status"] = status
    task.log(producer_line(task, f"status {old} → {status}{note}"))


def latest_review_context(board, task):
    reviews_dir = board.project / "Studio" / "reviews"
    rounds = {}
    if reviews_dir.is_dir():
        for path in reviews_dir.glob(f"*/{task.id}-r*.md"):
            match = re.search(r"-r(\d+)\.md$", path.name)
            if match:
                rounds.setdefault(int(match.group(1)), []).append(path)
    context = []
    if rounds:
        context.extend(sorted(rounds[max(rounds)]))
    reports_dir = board.project / "Studio" / "reports"
    if reports_dir.is_dir():
        for reviewer in task.get("reviewers"):
            context.extend(sorted(reports_dir.glob(f"S*/{task.id}.{reviewer}.md")))
    return [str(path.relative_to(board.project)) for path in context]


def ready_items(board, sprint=None):
    tasks = board.load()
    done = {task.id for task in tasks if task.get("status") == "done"}
    items = []
    for task in tasks:
        if sprint is not None and task.get("sprint") != sprint:
            continue
        status = task.get("status")
        base = {
            "id": task.id,
            "title": task.get("title"),
            "feature": task.get("feature"),
            "sprint": task.get("sprint"),
            "status": status,
            "rework": task.get("rework"),
        }
        if status in ("todo", "changes") and all(dep in done for dep in task.get("deps")):
            fixing = status == "changes"
            items.append(
                dict(
                    base,
                    kind="RUN",
                    role=task.get("owner"),
                    mode=FIX_MODE if fixing else task.get("mode"),
                    context=latest_review_context(board, task) if fixing else [],
                )
            )
        elif status == "review":
            for reviewer in task.get("reviewers"):
                if reviewer in task.get("approvals"):
                    continue
                mode = "CODE_REVIEW" if reviewer == "architect" else "REVIEW"
                items.append(dict(base, kind="REVIEW", role=reviewer, mode=mode, context=[]))

    def order(item):
        rank = 0 if item["kind"] == "REVIEW" else RUN_PRIORITY.get(item["mode"], 5)
        number = item["id"][2:]
        return (rank, int(number) if number.isdigit() else 0, item["role"])

    return sorted(items, key=order)


def command_new(board, args):
    check_role(args.owner)
    check_mode(args.owner, args.mode)
    reviewers = parse_list(args.reviewers or "")
    for reviewer in reviewers:
        check_role(reviewer)
    task_id = board.next_id()
    sprint = args.sprint if args.sprint is not None else board.current_sprint()
    meta = {
        "id": task_id,
        "title": " ".join(args.title.split()),
        "feature": args.feature or "",
        "sprint": sprint,
        "owner": args.owner,
        "mode": args.mode,
        "status": "todo",
        "deps": [normalize_id(dep) for dep in parse_list(args.deps or "")],
        "reviewers": reviewers,
        "approvals": [],
        "inputs": parse_list(args.inputs or ""),
        "outputs": parse_list(args.outputs or ""),
        "rework": 0,
    }
    done_when = [item.strip() for item in (args.done_when or "").split(";") if item.strip()]
    done_block = "\n".join(f"- {item}" for item in done_when) or "- —"
    body = (
        f"\n## Description\n{(args.desc or '—').strip()}\n\n"
        f"## Done when\n{done_block}\n\n"
        f"{LOG_HEADER}\n- S{sprint} producer: created\n"
    )
    board.tasks_dir.mkdir(parents=True, exist_ok=True)
    Task(board.tasks_dir / f"{task_id}.md", meta, body).save()
    print(task_id)


def command_next(board, args):
    items = ready_items(board, args.sprint)
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        print("No ready tasks.")
        return
    for item in items:
        print(f'{item["kind"]} {item["id"]} {item["role"]} {item["mode"]} "{item["title"]}"')
        if item["context"]:
            print(f'    context: {", ".join(item["context"])}')


def command_start(board, args):
    task = board.find(args.id)
    status = task.get("status")
    if status not in ("todo", "changes"):
        raise BoardError(f"{task.id}: start works from todo or changes, now {status}")
    mode = FIX_MODE if status == "changes" else task.get("mode")
    change_status(task, "doing", f", in progress ({mode})")
    task.save()
    print(f"{task.id}: doing")


def command_finish(board, args):
    task = board.find(args.id)
    if task.get("status") not in ("todo", "doing", "changes"):
        raise BoardError(f"{task.id}: finish works from todo, doing or changes, now {task.get('status')}")
    task.meta["approvals"] = []
    change_status(task, "review" if task.get("reviewers") else "done")
    task.save()
    print(f"{task.id}: {task.get('status')}")


def command_review(board, args):
    task = board.find(args.id)
    if task.get("status") != "review":
        raise BoardError(f"{task.id}: a review is possible only in status review, now {task.get('status')}")
    if args.reviewer not in task.get("reviewers"):
        raise BoardError(f"{task.id}: {args.reviewer} is not a reviewer of this task")
    if args.verdict == "approve":
        approvals = task.get("approvals")
        if args.reviewer not in approvals:
            approvals.append(args.reviewer)
        task.meta["approvals"] = approvals
        task.log(producer_line(task, f"{args.reviewer} — APPROVE"))
        if set(task.get("reviewers")) <= set(approvals):
            change_status(task, "done")
    else:
        task.meta["approvals"] = []
        task.meta["rework"] = task.get("rework") + 1
        task.log(producer_line(task, f"{args.reviewer} — CHANGES_REQUESTED, rework {task.get('rework')}"))
        change_status(task, "changes")
    task.save()
    print(f"{task.id}: {task.get('status')}")


def apply_assignment(task, assignment):
    key, separator, value = assignment.partition("=")
    key = key.strip()
    if not separator or not key:
        raise BoardError(f"expected key=value, got {assignment!r}")
    if key == "id":
        raise BoardError("id cannot be changed")
    if key in LIST_KEYS:
        current = task.get(key)
        if value.startswith("+"):
            for item in parse_list(value[1:]):
                item = normalize_id(item) if key == "deps" else item
                if item not in current:
                    current.append(item)
        elif value.startswith("-"):
            removed = {normalize_id(item) if key == "deps" else item for item in parse_list(value[1:])}
            current = [item for item in current if item not in removed]
        else:
            current = parse_list(value)
            if key == "deps":
                current = [normalize_id(item) for item in current]
        if key == "reviewers":
            for reviewer in current:
                check_role(reviewer)
        task.meta[key] = current
    elif key in INT_KEYS:
        try:
            task.meta[key] = task.get(key) + int(value[1:]) if value.startswith("+") else int(value)
        except ValueError as error:
            raise BoardError(f"{key} must be a number") from error
    else:
        if key == "status":
            check_status(value)
        if key == "owner":
            check_role(value)
        task.meta[key] = value
    if key in ("owner", "mode"):
        check_role(task.get("owner"))
        check_mode(task.get("owner"), task.get("mode"))
    return f"{key}={', '.join(task.get(key)) if key in LIST_KEYS else task.get(key)}"


def command_set(board, args):
    task = board.find(args.id)
    changes = [apply_assignment(task, assignment) for assignment in args.assignments]
    task.log(producer_line(task, "changed " + "; ".join(changes)))
    task.save()
    print(f"{task.id}: " + "; ".join(changes))


def command_block(board, args):
    task = board.find(args.id)
    task.meta["status"] = "blocked"
    task.log(producer_line(task, f"blocked: {args.reason}"))
    task.save()
    print(f"{task.id}: blocked")


def command_unblock(board, args):
    task = board.find(args.id)
    if task.get("status") != "blocked":
        raise BoardError(f"{task.id} is not blocked, status {task.get('status')}")
    change_status(task, "todo")
    task.save()
    print(f"{task.id}: todo")


def command_log(board, args):
    task = board.find(args.id)
    task.log(args.text)
    task.save()
    print(f"{task.id}: entry added")


def command_show(board, args):
    print(board.find(args.id).path.read_text(encoding="utf-8"), end="")


def cell(value):
    return str(value).replace("|", "\\|") or "—"


def command_render(board, args):
    tasks = board.load()
    counts = {status: 0 for status in STATUSES}
    for task in tasks:
        counts[task.get("status")] = counts.get(task.get("status"), 0) + 1
    summary = " · ".join(
        f"{STATUS_ICONS[status]} {status} {counts[status]}" for status in STATUSES if counts.get(status)
    )
    lines = [
        "# Board",
        "",
        "Generated by `python3 Studio/tools/board.py render`. Do not edit by hand.",
        "",
        f"Tasks: {len(tasks)}" + (f" · {summary}" if summary else ""),
    ]
    for sprint in sorted({task.get("sprint") for task in tasks}):
        lines += [
            "",
            f"## Sprint {sprint}",
            "",
            "| ID | Feature | Task | Role | Mode | Status | Deps | Review |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for task in (task for task in tasks if task.get("sprint") == sprint):
            reviews = ", ".join(
                f"{reviewer} ✓" if reviewer in task.get("approvals") else reviewer
                for reviewer in task.get("reviewers")
            )
            status = task.get("status")
            lines.append(
                "| "
                + " | ".join(
                    [
                        task.id,
                        cell(task.get("feature")),
                        cell(task.get("title")),
                        cell(task.get("owner")),
                        cell(task.get("mode")),
                        f"{STATUS_ICONS.get(status, '')} {status}".strip(),
                        cell(", ".join(task.get("deps"))),
                        cell(reviews),
                    ]
                )
                + " |"
            )
    target = board.project / "Studio" / "BOARD.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{target.relative_to(board.project)}: {len(tasks)} tasks")


def find_cycles(tasks):
    graph = {task.id: list(task.get("deps")) for task in tasks}
    state = {}
    cycles = []

    def visit(node, stack):
        state[node] = "visiting"
        stack.append(node)
        for dep in graph.get(node, []):
            if dep not in graph:
                continue
            if state.get(dep) == "visiting":
                cycles.append(stack[stack.index(dep):] + [dep])
            elif dep not in state:
                visit(dep, stack)
        stack.pop()
        state[node] = "done"

    for node in graph:
        if node not in state:
            visit(node, [])
    return cycles


def command_validate(board, args):
    errors = []
    try:
        tasks = board.load()
    except BoardError as error:
        print(f"ERROR: {error}")
        sys.exit(1)
    seen = {}
    for task in tasks:
        if task.id in seen:
            errors.append(f"{task.id}: duplicate id ({seen[task.id]} and {task.path.name})")
        seen[task.id] = task.path.name
        if task.path.stem != task.id:
            errors.append(f"{task.path.name}: frontmatter id {task.id!r} does not match the file name")
        owner = task.get("owner")
        if owner not in ROLES:
            errors.append(f"{task.id}: unknown role {owner!r}")
        elif task.get("mode") not in MODES[owner]:
            errors.append(f"{task.id}: mode {task.get('mode')!r} does not fit role {owner}")
        if task.get("status") not in STATUSES:
            errors.append(f"{task.id}: unknown status {task.get('status')!r}")
        for reviewer in task.get("reviewers"):
            if reviewer not in ROLES:
                errors.append(f"{task.id}: unknown reviewer {reviewer!r}")
            if reviewer == owner:
                errors.append(f"{task.id}: {owner} reviews its own work")
    ids = {task.id for task in tasks}
    for task in tasks:
        for dep in task.get("deps"):
            if dep not in ids:
                errors.append(f"{task.id}: unknown dependency {dep}")
    for cycle in find_cycles(tasks):
        errors.append("dependency cycle: " + " → ".join(cycle))
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        sys.exit(1)
    print(f"OK: {len(tasks)} tasks")


def command_summary(board, args):
    tasks = [task for task in board.load() if args.sprint is None or task.get("sprint") == args.sprint]
    features = {}
    for task in tasks:
        features.setdefault(task.get("feature") or "—", []).append(task)
    result = []
    for feature, feature_tasks in sorted(features.items()):
        done = [task for task in feature_tasks if task.get("status") == "done"]
        accepted = any(task.get("mode") == "ACCEPT" and task.get("status") == "done" for task in feature_tasks)
        result.append(
            {
                "feature": feature,
                "done": len(done),
                "total": len(feature_tasks),
                "accepted": accepted,
                "rework": sum(task.get("rework") for task in feature_tasks),
                "blocked": [task.id for task in feature_tasks if task.get("status") == "blocked"],
            }
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    scope = f"Sprint {args.sprint}" if args.sprint is not None else "All sprints"
    print(f"{scope}: tasks {len(tasks)}, done {sum(item['done'] for item in result)}")
    for item in result:
        blocked = ", ".join(item["blocked"]) or "none"
        print(
            f"{item['feature']}: {item['done']}/{item['total']} done, "
            f"accepted: {'yes' if item['accepted'] else 'no'}, "
            f"rework: {item['rework']}, blocked: {blocked}"
        )


def build_parser():
    parser = argparse.ArgumentParser(description="Studio task board (Studio/tasks).")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    new = sub.add_parser("new", help="create a task, prints its id")
    new.add_argument("--owner", required=True, help="role that does the work")
    new.add_argument("--mode", required=True, help="role mode (WORK, PLAN, …)")
    new.add_argument("--title", required=True, help="short title")
    new.add_argument("--feature", help="feature, e.g. F-001")
    new.add_argument("--sprint", type=int, help="sprint; defaults to the one in Studio/ledger.md")
    new.add_argument("--deps", help="comma-separated dependencies")
    new.add_argument("--reviewers", help="comma-separated reviewers")
    new.add_argument("--inputs", help="comma-separated input paths")
    new.add_argument("--outputs", help="comma-separated output paths")
    new.add_argument("--desc", help="task description")
    new.add_argument("--done-when", help="';'-separated done criteria")
    new.set_defaults(handler=command_new)

    next_parser = sub.add_parser("next", help="tasks and reviews ready to run")
    next_parser.add_argument("--sprint", type=int, help="only this sprint")
    next_parser.add_argument("--json", action="store_true", help="JSON output")
    next_parser.set_defaults(handler=command_next)

    for name, handler, help_text in (
        ("start", command_start, "take a task: todo|changes → doing"),
        ("finish", command_finish, "finish a task: review if it has reviewers, otherwise done"),
        ("unblock", command_unblock, "unblock: blocked → todo"),
        ("show", command_show, "print the task file"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("id", help="task id, e.g. T-007")
        command.set_defaults(handler=handler)

    review = sub.add_parser("review", help="record a review verdict")
    review.add_argument("id", help="task id")
    review.add_argument("reviewer", help="reviewer role")
    review.add_argument("verdict", choices=("approve", "changes"), help="review verdict")
    review.set_defaults(handler=command_review)

    set_parser = sub.add_parser("set", help="change fields: key=value, lists +X/-X, rework=+1")
    set_parser.add_argument("id", help="task id")
    set_parser.add_argument("assignments", nargs="+", help="key=value")
    set_parser.set_defaults(handler=command_set)

    block = sub.add_parser("block", help="block a task")
    block.add_argument("id", help="task id")
    block.add_argument("reason", help="reason")
    block.set_defaults(handler=command_block)

    log = sub.add_parser("log", help="append a line to the task log")
    log.add_argument("id", help="task id")
    log.add_argument("text", help="entry text")
    log.set_defaults(handler=command_log)

    render = sub.add_parser("render", help="build Studio/BOARD.md")
    render.set_defaults(handler=command_render)

    validate = sub.add_parser("validate", help="check the board: roles, modes, deps, cycles")
    validate.set_defaults(handler=command_validate)

    summary = sub.add_parser("summary", help="per-feature summary for a gate")
    summary.add_argument("--sprint", type=int, help="only this sprint")
    summary.add_argument("--json", action="store_true", help="JSON output")
    summary.set_defaults(handler=command_summary)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.handler(Board(args.project), args)
    except BoardError as error:
        print(f"board: {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
