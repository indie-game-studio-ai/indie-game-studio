#!/usr/bin/env python3
"""A wave of agent runs in two producer calls.

`start` picks the ready work, checks the budget, snapshots the zones, starts the tasks,
builds one context pack per run and prints the agent prompts. `finish` reads the
summaries from the reports, logs the real spend, verifies the zones and the test lock,
moves the tasks on the board and prints only what needs the producer. Every step the
producer used to make by hand is one of these two calls now, so its context grows by a
few lines per wave instead of a dozen tool calls.
"""
import argparse
import contextlib
import io
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import board as boardlib  # noqa: E402
import budget as budgetlib  # noqa: E402
import tokens as tokenslib  # noqa: E402
import zones as zoneslib  # noqa: E402

STATE_DIR = Path("Studio") / ".state"
STATUS_VALUES = (
    "DONE_WITH_CONCERNS", "DONE", "NEEDS_INPUT", "BLOCKED", "APPROVE", "CHANGES_REQUESTED", "TEST_DISPUTE",
)
STATUS_PREFIX = re.compile(r"^(" + "|".join(STATUS_VALUES) + r")\b\s*[—–:-]*\s*(.*)$")
SUMMARY_LABEL = re.compile(r"^(STATUS|TASK|FILES|CHECK|HANDOFF|NEW TASKS|QUESTIONS)\s*:\s*(.*)$", re.I)
TASK_STATUS_LINE = re.compile(r"^(T-\d+)\s*:\s*(.+)$")
HANDOFF_TARGET = re.compile(r"→\s*([a-z][a-z-]*)\s*:")
LIST_LABELS = {"HANDOFF": "handoff", "NEW TASKS": "new_tasks", "QUESTIONS": "questions"}
FINISHED = ("DONE", "DONE_WITH_CONCERNS")
VERDICT_MODES = ("VERIFY", "ACCEPT")
REVIEW_FOLDERS = {"art-director": "art", "architect": "code"}
LOCK_NAME = "acceptance"
# Time the producer needs between `start` and the continued agent's first request.
CACHE_MARGIN = 60


class WaveError(Exception):
    pass


# --- state -----------------------------------------------------------------------------

def state_path(project, name):
    return Path(project) / STATE_DIR / name


def load_json(project, name, default):
    path = state_path(project, name)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(project, name, data):
    path = state_path(project, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def load_config(project):
    path = Path(project) / "Studio" / "studio.json"
    if not path.is_file():
        raise WaveError(f"no {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def short(number):
    return tokenslib.short(number) if number is not None else "?"


# --- summaries -------------------------------------------------------------------------

def split_status(value):
    match = STATUS_PREFIX.match(value.strip())
    if not match:
        return None, value.strip()
    return match.group(1), match.group(2).strip()


def parse_summary(text):
    """The '## Summary' section of a report as a dict, or None if there is none."""
    lines = text.splitlines()
    start = next((index for index, line in enumerate(lines) if re.match(r"^##\s+Summary\s*$", line, re.I)), None)
    if start is None:
        return None
    summary = {
        "status": None, "note": "", "tasks": [], "statuses": {}, "files": [], "check": "",
        "handoff": [], "new_tasks": [], "questions": [],
    }
    current = None
    for raw in lines[start + 1:]:
        if raw.startswith("## "):
            break
        line = raw.strip()
        if not line or line.startswith("```"):
            continue
        item = line.lstrip("-*").strip().strip("`").strip()
        label = SUMMARY_LABEL.match(item)
        if label:
            current = label.group(1).upper()
            value = label.group(2).strip()
            if current == "STATUS":
                summary["status"], summary["note"] = split_status(value)
            elif current == "TASK":
                summary["tasks"] = [boardlib.normalize_id(task) for task in boardlib.parse_list(value)]
            elif current == "FILES":
                summary["files"] = boardlib.parse_list(value)
            elif current == "CHECK":
                summary["check"] = value
            elif value:
                summary[LIST_LABELS[current]].append(value)
            continue
        per_task = TASK_STATUS_LINE.match(item)
        if per_task:
            status, note = split_status(per_task.group(2))
            if status:
                summary["statuses"][per_task.group(1)] = (status, note)
                current = None
                continue
        if current in LIST_LABELS:
            summary[LIST_LABELS[current]].append(item)
        elif current == "CHECK":
            summary["check"] = f"{summary['check']} {item}".strip()
    return summary


def task_statuses(summary, ids):
    if summary["statuses"]:
        return {task_id: summary["statuses"].get(task_id, (None, "")) for task_id in ids}
    targets = set(summary["tasks"]) & set(ids) or set(ids)
    return {task_id: (summary["status"], summary["note"]) if task_id in targets else (None, "") for task_id in ids}


# --- start -----------------------------------------------------------------------------

def report_path(group, sprint, wave, tasks):
    ids = group["ids"]
    single = len(ids) == 1
    folder = REVIEW_FOLDERS.get(group["role"])
    if group["kind"] == "REVIEW" and folder:
        if single:
            return f"Studio/reviews/{folder}/{ids[0]}-r{tasks[ids[0]].get('rework') + 1}.md"
        return f"Studio/reviews/{folder}/S{sprint}-W{wave}.md"
    name = ids[0] if single else f"W{wave}"
    return f"Studio/reports/S{sprint}/{name}.{group['role']}.md"


def pick_groups(groups, limit):
    chosen = []
    roles = set()
    for group in groups:
        if group["role"] in roles:
            continue
        chosen.append(group)
        roles.add(group["role"])
        if len(chosen) == limit:
            break
    return chosen


def block_over_limit(board, sprint, rework_limit):
    """Blocks tasks whose escalated round failed too. Returns their ids."""
    blocked = []
    for task in board.load():
        if task.get("sprint") == sprint and task.get("status") == "changes" and task.get("rework") > rework_limit + 1:
            boardlib.block_task(task, f"rework limit: {task.get('rework')} rounds, the escalated one included")
            blocked.append(task.id)
    return blocked


def agent_name(sprint, wave, role):
    """Codex task name: lowercase letters, digits and underscores."""
    return f"s{sprint}_w{wave}_{role.replace('-', '_')}"


def cache_warm(previous, now):
    last, ttl = previous.get("last_at"), previous.get("ttl")
    return bool(last and ttl) and now < last + ttl - CACHE_MARGIN


def reusable_agent(agents, group, sprint, model, escalated, budget, now):
    previous = agents.get(group["role"])
    if escalated or not previous or previous.get("sprint") != sprint or previous.get("model") != model:
        return None
    if group["kind"] == "REVIEW" and not budget.get("reuse_reviewers", True):
        return None
    context = previous.get("context")
    if context is None or context > budget.get("reuse_max_context", 40000):
        return None
    # Once the cache has expired, a continued agent writes its whole context again at
    # 1.25×; past a small context a fresh agent with a pack costs less.
    if not cache_warm(previous, now) and context > budget.get("reuse_cold_max_context", 15000):
        return None
    return previous


def prompt(run, project, config, sprint):
    budget = config.get("budget", {})
    return "\n".join([
        f"PROJECT={Path(project).resolve()}",
        f"ROLE={run['role']}",
        f"MODE={run['mode']}",
        f"LANG={config.get('language') or 'en'}",
        f"PROFILE={budget.get('profile', 'standard')}",
        f"SPRINT={sprint}",
        f"TASKS={','.join(run['ids'])}",
        f"PACK={run['pack']}",
        f"REPORT={run['report']}",
        f"CONTEXT: {', '.join(run['context']) or '—'}",
    ])


def print_runs(state, project, config):
    for number, run in enumerate(state["runs"], start=1):
        how = (
            f"continue agent {run['continue']} (context {short(run['continue_context'])}, "
            f"cache {'warm' if run.get('warm') else 'cold'})"
            if run.get("continue") else f"new agent {run['name']}" if run.get("name") else "new agent"
        )
        escalated = " · escalated" if run.get("escalated") else ""
        print(f"\n### {number}. {run['role']} {run['mode']} {','.join(run['ids'])} — "
              f"model={run['model']} effort={run['effort']} — {how}{escalated}")
        print(prompt(run, project, config, state["sprint"]))
    roles = " ".join(f"{run['role']}=<agent id>" for run in state["runs"] if not run.get("continue"))
    print("\nLaunch them in one message, wait for all, then: python3 Studio/tools/waves.py finish"
          + (f" --agent {roles}" if roles else "")
          + "\n(Codex agents spawned with these names and continued agents need no --agent.)")


def command_start(project, args):
    state = load_json(project, "wave.json", {})
    if state.get("open"):
        raise WaveError(f"wave {state['label']} is open: finish it first (waves.py status shows its prompts)")
    config = load_config(project)
    board = boardlib.Board(project)
    sprint = args.sprint if args.sprint is not None else board.current_sprint()
    limits = config.get("limits", {})
    budget = config.get("budget", {})
    rework_limit = limits.get("rework_cycles", 3)
    blocked = block_over_limit(board, sprint, rework_limit)

    groups = pick_groups(
        boardlib.batch_items(boardlib.ready_items(board, sprint)),
        args.max or limits.get("parallel_agents", 4),
    )
    if not groups:
        left = [task for task in board.load() if task.get("sprint") == sprint and task.get("status") != "done"]
        print("No ready tasks." + (f" Not done: {len(left)} (blocked: "
              f"{', '.join(task.id for task in left if task.get('status') == 'blocked') or 'none'})." if left else
              " The sprint's tasks are all done."))
        for task_id in blocked:
            print(f"Blocked {task_id}: rework limit — ask the user")
        return 0

    verdict, line = budgetlib.check(budgetlib.Studio(project), len(groups), sprint)
    if verdict == "OVER":
        print(f"OVER BUDGET — {line}. Call an early gate.")
        return 1

    wave = state.get("waves", {}).get(str(sprint), 0) + 1
    label = f"S{sprint}-W{wave}"
    files = zoneslib.take_snapshot(zoneslib.Zones(project), label)
    agents = load_json(project, "agents.json", {})
    profile = budget.get("profile", "standard")
    now = time.time()
    runs = []
    started = []
    for group in groups:
        tasks = {task_id: board.find(task_id) for task_id in group["ids"]}
        escalated = any(task.get("rework") > rework_limit for task in tasks.values())
        model, effort = budgetlib.route(profile, group["role"], group["mode"], escalated)
        pack = f"Studio/.state/packs/{label}-{group['role']}.md"
        pack_file = Path(project) / pack
        pack_file.parent.mkdir(parents=True, exist_ok=True)
        pack_file.write_text(
            boardlib.build_pack(board, group["ids"], group["role"], group["mode"], group["context"]),
            encoding="utf-8",
        )
        previous = reusable_agent(agents, group, sprint, model, escalated, budget, now)
        if group["kind"] == "RUN":
            for task in tasks.values():
                boardlib.change_status(task, "doing", f", in progress ({group['mode']})")
                task.save()
                started.append(task.id)
        runs.append({
            "kind": group["kind"], "role": group["role"], "mode": group["mode"], "ids": group["ids"],
            "context": group["context"], "report": report_path(group, sprint, wave, tasks), "pack": pack,
            "model": model, "effort": effort, "escalated": escalated,
            "name": agent_name(sprint, wave, group["role"]),
            "continue": previous["agent"] if previous else None,
            "continue_context": previous.get("context") if previous else None,
            "warm": cache_warm(previous, now) if previous else False,
        })

    state.update({
        "open": True, "sprint": sprint, "wave": wave, "label": label,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": runs,
    })
    save_json(project, "wave.json", state)
    budget_word = "WARN" if verdict == "WARN" else "budget OK"
    print(f"{label}: {len(runs)} runs · {budget_word} — {line}")
    print(f"snapshot {files} files · started {', '.join(started) or '—'}"
          + (f" · blocked {', '.join(blocked)} (rework limit — ask the user)" if blocked else ""))
    print_runs(state, project, config)
    return 0


def command_status(project, args):
    state = load_json(project, "wave.json", {})
    if not state.get("open"):
        last = state.get("label")
        print(f"No open wave{f' (last: {last})' if last else ''}.")
        return 0
    print(f"{state['label']} is open since {state['started_at']}: {len(state['runs'])} runs")
    print_runs(state, project, load_config(project))
    return 0


# --- finish ----------------------------------------------------------------------------

def parse_agents(values):
    result = {}
    for value in values or []:
        role, separator, rest = value.partition("=")
        if not separator or not rest:
            raise WaveError(f"--agent expects role=<agent id>[:<context tokens>], got {value!r}")
        agent, _, context = rest.partition(":")
        result[role.strip()] = (agent.strip(), int(context) if context.strip().isdigit() else None)
    return result


def run_spend(project, agents, role, agent_id):
    """(tokens of this run, the agent's spend so far), or (None, None) without a
    transcript. A continued agent's transcript holds its earlier runs too, so the run is
    the difference."""
    spend = tokenslib.agent_spend(project, agent_id)
    if spend is None:
        return None, None
    previous = agents.get(role, {})
    before = previous.get("weighted", 0) if previous.get("agent") == agent_id else 0
    return spend["weighted"] - before, spend


def default_agent(project, run):
    """A continued agent keeps its id; a Codex agent is found by the name it was spawned with."""
    if run.get("continue"):
        return run["continue"]
    if run.get("name") and tokenslib.find_codex_thread(project, run["name"]):
        return run["name"]
    return ""


def producer_spend(project, state):
    producer = tokenslib.producer_run(project)
    if producer is None:
        return None
    name, run = producer
    weighted = run.metrics()["weighted"]
    mark = state.get("producer_mark", {})
    state["producer_mark"] = {name: weighted}
    return weighted - mark.get(name, 0)


def reopen(task, reason):
    status = "changes" if task.get("rework") > 0 else "todo"
    boardlib.change_status(task, status, f" — round void: {reason}")
    task.save()


def log_handoffs(board, run, summary, tasks, sprint):
    features = {task.get("feature") for task in tasks if task.get("feature")}
    for handoff in summary["handoff"]:
        entry = f"S{sprint} {run['role']} {handoff if handoff.startswith('→') else '→ ' + handoff}"
        targets = list(tasks)
        match = HANDOFF_TARGET.search(entry)
        if match:
            targets += [
                task for task in board.load()
                if task.get("owner") == match.group(1) and task.get("sprint") == sprint
                and task.get("status") != "done" and task.get("feature") in features
            ]
        for task in {task.id: task for task in targets}.values():
            task.log(entry)
            task.save()


def create_new_tasks(board, run, summary, tasks, sprint, digest):
    specs = [spec for spec in (boardlib.parse_task_line(line) for line in summary["new_tasks"]) if spec]
    unparsed = len(summary["new_tasks"]) - len(specs)
    if unparsed:
        digest["needs"].append(f"{run['role']}: {unparsed} NEW TASKS line(s) not in '<owner>/<MODE>: <title>' form ({run['report']})")
    if not specs:
        return []
    feature = next((task.get("feature") for task in tasks if task.get("feature")), "")
    try:
        created, later = boardlib.create_tasks(board, specs, sprint, feature=feature, source=run["role"])
    except boardlib.BoardError as error:
        digest["needs"].append(f"{run['role']}: NEW TASKS not created — {error} ({run['report']})")
        return []
    for task in created:
        deps = f" [deps {', '.join(task.get('deps'))}]" if task.get("deps") else ""
        digest["lines"].append(f"created {task.id} {task.get('owner')}/{task.get('mode')}: {task.get('title')}{deps}")
    if later:
        digest["lines"].append(f"proposals.md: +{len(later)} from {run['role']}")
    return created


def append_questions(project, sprint, run, task_ids, questions):
    if not questions:
        return
    path = Path(project) / "Studio" / "questions.md"
    text = path.read_text(encoding="utf-8") if path.is_file() else "# Questions\n"
    entries = "".join(f"- S{sprint} {','.join(task_ids)} ({run['role']}): {question}\n" for question in questions)
    path.write_text(text.rstrip("\n") + "\n" + entries, encoding="utf-8")


def apply_task(board, run, task, status, note, proof, sprint, summary, digest):
    """Moves one task by its status. Returns "done", "verdict" (CHANGES_REQUESTED of a
    VERIFY or ACCEPT, settled once the new tasks exist) or None when the agent has to
    answer again."""
    head = f"{task.id} {run['role']} {run['mode']}: {status or 'no status'}" + (f" — {note}" if note else "")
    finished = status in FINISHED or (status == "APPROVE" and run["mode"] in VERDICT_MODES)
    if status is None:
        digest["needs"].append(f"{head} ({run['report']})")
        return None
    if run["kind"] == "REVIEW" and status not in ("APPROVE", "CHANGES_REQUESTED", "NEEDS_INPUT", "BLOCKED"):
        digest["needs"].append(f"{head}: a review answers APPROVE or CHANGES_REQUESTED")
        return None
    if run["kind"] == "RUN" and finished and not proof and run["mode"] not in VERDICT_MODES:
        digest["needs"].append(f"{head}: no CHECK and no FILES — send it back to the agent for proof")
        return None
    if run["kind"] == "RUN" and status == "CHANGES_REQUESTED" and run["mode"] not in VERDICT_MODES:
        digest["needs"].append(f"{head}: unexpected for {run['mode']}")
        return None
    task.log(f"S{sprint} {run['role']}: {status}{' — ' + note if note else ''} ({run['report']})")
    task.save()
    if status == "NEEDS_INPUT":
        boardlib.block_task(task, f"needs input — {run['report']}")
        digest["needs"].append(head + "".join(f"\n    ? {question}" for question in summary["questions"]))
    elif status == "BLOCKED":
        boardlib.block_task(task, f"{note or 'blocked'} — {run['report']}")
        digest["needs"].append(head)
    elif status == "TEST_DISPUTE":
        boardlib.block_task(task, f"test dispute — architect CODE_REVIEW with {run['report']}")
        digest["needs"].append(f"{head} → launch architect CODE_REVIEW with {run['report']} in CONTEXT")
    elif run["kind"] == "REVIEW":
        boardlib.record_review(task, run["role"], "approve" if status == "APPROVE" else "changes", run["report"])
        digest["lines"].append(f"{head} → {task.get('status')}" + (f" (rework {task.get('rework')})" if status != "APPROVE" else ""))
    elif status == "CHANGES_REQUESTED":
        return "verdict"
    else:
        boardlib.finish_task(task)
        outcome = f"{head} → {task.get('status')}"
        if run["mode"] == "ACCEPT":
            outcome += f" · {task.get('feature') or 'feature'} accepted"
        digest["lines"].append(outcome)
        if status == "DONE_WITH_CONCERNS":
            digest["needs"].append(f"{task.id}: concerns — read {run['report']} before the gate")
    return "done"


def apply_run(project, board, run, sprint, config, digest):
    """Applies a run's summary. A task the agent has to answer again (no status, no
    proof) stays in the run; True when nothing is left."""
    report = Path(project) / run["report"]
    summary = parse_summary(report.read_text(encoding="utf-8")) if report.is_file() else None
    if summary is None:
        digest["needs"].append(f"{run['role']} {run['mode']} {','.join(run['ids'])}: no '## Summary' in {run['report']}")
        return False
    applied = set(run.get("applied_ids", []))
    statuses = task_statuses(summary, run["ids"])
    proof = summary["check"] or summary["files"]
    verdicts = []
    for task_id in run["ids"]:
        if task_id in applied:
            continue
        status, note = statuses[task_id]
        outcome = apply_task(board, run, board.find(task_id), status, note, proof, sprint, summary, digest)
        if outcome == "done":
            applied.add(task_id)
        elif outcome == "verdict":
            verdicts.append(task_id)

    if not run.get("extras_applied"):
        run["extras_applied"] = True
        log_handoffs(board, run, summary, [board.find(task_id) for task_id in run["ids"]], sprint)
        run["created"] = [task.id for task in create_new_tasks(board, run, summary, [board.find(task_id) for task_id in run["ids"]], sprint, digest)]
        append_questions(project, sprint, run, run["ids"], summary["questions"])
        if not summary["statuses"] and summary["questions"] and summary["status"] != "NEEDS_INPUT":
            digest["needs"].extend(f"{run['role']} asks: {question}" for question in summary["questions"])
    created = [board.find(task_id) for task_id in run.get("created", [])]
    for task_id in verdicts:
        task = board.find(task_id)
        new_ids = [new.id for new in created if not task.get("feature") or new.get("feature") in ("", task.get("feature"))]
        if not new_ids:
            digest["needs"].append(f"{task.id} {run['mode']}: CHANGES_REQUESTED without NEW TASKS ({run['report']})")
            continue
        task.meta["deps"] = task.get("deps") + [new_id for new_id in new_ids if new_id not in task.get("deps")]
        boardlib.change_status(task, "todo", f", waits for {', '.join(new_ids)}")
        task.save()
        applied.add(task_id)
        digest["lines"].append(f"{task.id} {run['role']} {run['mode']}: CHANGES_REQUESTED → todo after {', '.join(new_ids)}")
    run["applied_ids"] = sorted(applied)
    return applied >= set(run["ids"])


def update_ledger(project, label, wave, timeline):
    path = Path(project) / "Studio" / "ledger.md"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"^- Wave:.*$", f"- Wave: {wave}", text, count=1, flags=re.M)
    path.write_text(text.rstrip("\n") + f"\n- {label}: {timeline}\n", encoding="utf-8")


def render_board(board):
    with contextlib.redirect_stdout(io.StringIO()):
        boardlib.command_render(board, None)


def log_spend(project, studio, agents, run, agent_ids, sprint, digest):
    """Logs a run once, and again for what a continued agent spent on top (for example
    when it was asked for a missing summary). Returns the tokens logged now."""
    agent_id, given_context = agent_ids.get(run["role"], ("", None))
    agent_id = agent_id or default_agent(project, run)
    if not agent_id:
        if not run.get("logged"):
            budgetlib.log_run(studio, run["role"], run["mode"], run["ids"], None, run["model"], "", sprint)
            digest["needs"].append(f"{run['role']}: no --agent id, its spend is not counted")
            run["logged"] = True
        return 0
    tokens, spend = run_spend(project, agents, run["role"], agent_id)
    if not run.get("logged") or (tokens or 0) > 0:
        budgetlib.log_run(studio, run["role"], run["mode"], run["ids"], tokens, run["model"], agent_id, sprint)
        run["logged"] = True
    previous = agents.get(run["role"], {})
    agents[run["role"]] = {
        "agent": agent_id, "model": run["model"], "sprint": sprint,
        "context": spend["final_context"] if spend else given_context,
        "weighted": spend["weighted"] if spend else previous.get("weighted"),
        "last_at": spend["last_at"] if spend else None,
        "ttl": spend["ttl"] if spend else None,
    }
    return tokens or 0


def command_finish(project, args):
    state = load_json(project, "wave.json", {})
    if not state.get("open"):
        raise WaveError("no open wave: run waves.py start")
    config = load_config(project)
    board = boardlib.Board(project)
    zones = zoneslib.Zones(project)
    sprint = state["sprint"]
    pending = [run for run in state["runs"] if not run.get("applied")]
    agent_ids = parse_agents(args.agent)
    unknown = sorted(set(agent_ids) - {run["role"] for run in state["runs"]})
    if unknown:
        raise WaveError(f"--agent for roles outside the wave: {', '.join(unknown)}")
    void = {role: "voided by the producer" for role in boardlib.parse_list(args.void or "")}

    _, violations = zoneslib.verify_changes(zones, state["label"], [run["role"] for run in state["runs"]])
    if violations and not args.allow_violations and not void:
        for violation in violations:
            owners = ", ".join(violation["owners"]) or "nobody"
            print(f"VIOLATION {violation['kind']} {violation['path']} (zone of: {owners})")
        print("Nothing applied. Revert these writes (delete new files, git restore changed ones) and run finish "
              "again with --void <role> for the role whose round is void, or --allow-violations if they are fine.")
        return 1

    digest = {"lines": [], "needs": []}
    if any(run["role"] == "developer" for run in pending) and zoneslib.has_lock(zones, LOCK_NAME):
        changed = zoneslib.check_lock(zones, LOCK_NAME, restore=True)
        if changed:
            void["developer"] = f"changed {len(changed)} locked acceptance test file(s); restored"

    studio = budgetlib.Studio(project)
    agents = load_json(project, "agents.json", {})
    spent_agents = sum(log_spend(project, studio, agents, run, agent_ids, sprint, digest) for run in pending)
    producer = producer_spend(project, state)
    if producer is not None:
        budgetlib.log_run(studio, budgetlib.PRODUCER, "WAVE", tokens=producer, sprint=sprint)

    tests_first = config.get("budget", {}).get("tests_first", True)
    lock_after = False
    for run in pending:
        if run["role"] in void:
            if run["kind"] == "RUN":
                for task_id in run["ids"]:
                    reopen(board.find(task_id), void[run["role"]])
            digest["needs"].append(f"{run['role']} {','.join(run['ids'])}: round void — {void[run['role']]}")
            run["applied"] = "void"
            continue
        if apply_run(project, board, run, sprint, config, digest):
            run["applied"] = True
            if run["mode"] == "WRITE_TESTS" or (run["mode"] == "VERIFY" and not tests_first):
                lock_after = True
    if lock_after:
        digest["lines"].append(f"locked acceptance tests: {zoneslib.lock_tests(zones, LOCK_NAME)} files")

    save_json(project, "agents.json", agents)
    render_board(board)
    waiting = [run for run in state["runs"] if not run.get("applied")]
    if not waiting:
        timeline = "; ".join(
            f"{run['role']} {run['mode']} {','.join(run['ids'])}{' void' if run['applied'] == 'void' else ''}"
            for run in state["runs"]
        )
        update_ledger(project, state["label"], state["wave"], timeline)
        state["open"] = False
        state.setdefault("waves", {})[str(sprint)] = state["wave"]
    save_json(project, "wave.json", state)

    verdict, line = budgetlib.check(studio, 0, sprint)
    print(f"{state['label']} {'finished' if not waiting else 'stays open'} · spend: agents {short(spent_agents)}, "
          f"producer {short(producer)} · {'OVER BUDGET' if verdict == 'OVER' else verdict} — {line}")
    for entry in digest["lines"]:
        print(entry)
    if digest["needs"]:
        print("Needs you:")
        for entry in digest["needs"]:
            print(f"  {entry}")
    if waiting:
        print(f"Waiting for: {', '.join(run['role'] for run in waiting)}. Continue each with its line "
              "under 'Needs you', then run waves.py finish again with its --agent.")
        return 0
    ready = pick_groups(boardlib.batch_items(boardlib.ready_items(board, sprint)),
                        config.get("limits", {}).get("parallel_agents", 4))
    if verdict == "OVER":
        print("Budget cap reached: call an early gate.")
    elif ready:
        print(f"Next: {len(ready)} run(s) ready — waves.py start")
    else:
        print("Next: nothing ready — sprint end or blocked tasks")
    return 0


def command_solo(project, args):
    """A run outside the board (kickoff, pre-production, spec, meeting): log its spend
    and print its summary."""
    studio = budgetlib.Studio(project)
    sprint = args.sprint if args.sprint is not None else studio.current_sprint()
    tokens = None
    if args.agent:
        spend = tokenslib.agent_spend(project, args.agent)
        tokens = spend["weighted"] if spend else None
    budgetlib.log_run(studio, args.role, args.mode, tokens=tokens, model=args.model or "", agent=args.agent or "", sprint=sprint)
    print(f"logged S{sprint} {args.role} {args.mode}: {short(tokens)} tokens")
    if not args.report:
        return 0
    path = Path(project) / args.report
    summary = parse_summary(path.read_text(encoding="utf-8")) if path.is_file() else None
    if summary is None:
        print(f"no '## Summary' in {args.report}")
        return 1
    print(f"STATUS: {summary['status'] or '?'}" + (f" — {summary['note']}" if summary["note"] else ""))
    for key, label in (("handoff", "HANDOFF"), ("new_tasks", "NEW TASKS"), ("questions", "QUESTIONS")):
        for value in summary[key]:
            print(f"{label}: {value}")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="A wave of agent runs in two producer calls.")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="pick ready work, check the budget, snapshot, start tasks, print prompts")
    start.add_argument("--sprint", type=int, help="defaults to the sprint in Studio/ledger.md")
    start.add_argument("--max", type=int, help="at most this many runs (default: limits.parallel_agents)")
    start.set_defaults(handler=command_start)

    finish = sub.add_parser("finish", help="apply the summaries, log spend, verify zones and the test lock")
    finish.add_argument("--agent", action="append", metavar="ROLE=ID[:CONTEXT]",
                        help="agent id of a run; add :<subagent_tokens> when there is no transcript")
    finish.add_argument("--void", help="comma-separated roles whose round is void (after a zone violation)")
    finish.add_argument("--allow-violations", action="store_true", help="apply although zones.py found violations")
    finish.set_defaults(handler=command_finish)

    status = sub.add_parser("status", help="the open wave and its prompts")
    status.set_defaults(handler=command_status)

    solo = sub.add_parser("solo", help="log a run outside the board and print its summary")
    solo.add_argument("--role", required=True)
    solo.add_argument("--mode", required=True)
    solo.add_argument("--agent", help="agent id; its spend is read from the transcript")
    solo.add_argument("--report", help="report with a '## Summary' section")
    solo.add_argument("--model", help="model of the run")
    solo.add_argument("--sprint", type=int)
    solo.set_defaults(handler=command_solo)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args.project, args)
    except (WaveError, boardlib.BoardError, budgetlib.BudgetError, zoneslib.ZonesError, tokenslib.TokensError) as error:
        print(f"wave: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
