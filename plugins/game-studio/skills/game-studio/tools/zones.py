#!/usr/bin/env python3
"""Role write zones: snapshots, wave verification, test lock, hook.

Zones live in Studio/studio.json. The Claude Code plugin hook reads the same file.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
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
STATE_DIR = "Studio/.state"
CONFIG_PATH = "Studio/studio.json"
PATH_VARIABLE = re.compile(r"\$\{paths\.([A-Za-z0-9_]+)\}")
PATCH_LINE = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to):\s*(.+?)\s*$")


class ZonesError(Exception):
    pass


def load_config(project):
    path = Path(project) / CONFIG_PATH
    if not path.is_file():
        raise ZonesError(f"no {CONFIG_PATH} in {project}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ZonesError(f"{CONFIG_PATH}: invalid JSON — {error}") from error


def expand_pattern(pattern, paths):
    """Expands ${paths.key}: a list gives one rule per item, an empty value drops the rule."""
    match = PATH_VARIABLE.search(pattern)
    if not match:
        return [pattern]
    value = paths.get(match.group(1))
    if value is None:
        print(f"zones: paths has no key {match.group(1)!r}, rule {pattern!r} skipped", file=sys.stderr)
        return []
    values = value if isinstance(value, list) else [value]
    expanded = []
    for item in values:
        item = str(item).strip().rstrip("/")
        if not item:
            continue
        expanded.extend(expand_pattern(pattern[: match.start()] + item + pattern[match.end():], paths))
    return expanded


def expand_all(patterns, paths, role=None):
    result = []
    for pattern in patterns:
        if role is not None:
            pattern = pattern.replace("{role}", role)
        result.extend(expand_pattern(pattern, paths))
    return result


def glob_to_regex(pattern):
    index = 0
    parts = []
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif pattern.startswith("**", index):
            parts.append(".*")
            index += 2
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


class Zones:
    def __init__(self, project, config=None):
        self.project = Path(project).resolve()
        self.config = config if config is not None else load_config(self.project)
        self.paths = self.config.get("paths", {})
        self._compiled = {}

    def role_patterns(self, role):
        return expand_all(self.config.get("zones", {}).get(role, []), self.paths)

    def shared_patterns(self, role):
        return expand_all(self.config.get("shared_zones", []), self.paths, role=role)

    def producer_patterns(self):
        return expand_all(self.config.get("producer_zones", []), self.paths)

    def acceptance_patterns(self):
        return expand_all(["${paths.acceptance_tests}"], self.paths)

    def matches(self, path, patterns):
        for pattern in patterns:
            regex = self._compiled.get(pattern)
            if regex is None:
                regex = self._compiled[pattern] = glob_to_regex(pattern)
            if regex.match(path):
                return True
        return False

    def allowed(self, role, path):
        return self.matches(path, self.role_patterns(role)) or self.matches(path, self.shared_patterns(role))

    def owners(self, path):
        return [role for role in ROLES if self.allowed(role, path)]

    def is_producer(self, path):
        return self.matches(path, self.producer_patterns())

    def ignored_names(self):
        return set(self.config.get("ignore", []))

    def walk(self, patterns=None):
        ignored = self.ignored_names()
        files = {}
        for root, dirs, names in os.walk(self.project):
            relative_root = Path(root).relative_to(self.project).as_posix()
            relative_root = "" if relative_root == "." else relative_root
            kept = []
            for name in dirs:
                relative = f"{relative_root}/{name}" if relative_root else name
                if name in ignored or relative == STATE_DIR:
                    continue
                kept.append(name)
            dirs[:] = sorted(kept)
            for name in sorted(names):
                if name in ignored:
                    continue
                relative = f"{relative_root}/{name}" if relative_root else name
                if patterns is not None and not self.matches(relative, patterns):
                    continue
                files[relative] = file_hash(Path(root) / name)
        return files


def file_hash(path):
    digest = hashlib.sha1()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def diff(before, after):
    changes = []
    for path in sorted(set(before) | set(after)):
        if path not in before:
            changes.append(("added", path))
        elif path not in after:
            changes.append(("deleted", path))
        elif before[path] != after[path]:
            changes.append(("modified", path))
    return changes


def check_label(label):
    if not re.match(r"^[A-Za-z0-9_.-]+$", label):
        raise ZonesError(f"label {label!r}: use latin letters, digits, '_', '-', '.'")
    return label


def snapshot_path(zones, label):
    return zones.project / STATE_DIR / "snapshots" / f"{check_label(label)}.json"


def take_snapshot(zones, label):
    files = zones.walk()
    target = snapshot_path(zones, label)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"label": label, "files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(files)


def command_snapshot(zones, args):
    print(f"snapshot {args.label}: {take_snapshot(zones, args.label)} files")
    return 0


def parse_roles(raw):
    roles = [role.strip() for role in raw.split(",") if role.strip()]
    unknown = [role for role in roles if role not in ROLES]
    if unknown:
        raise ZonesError(f"unknown roles: {', '.join(unknown)}; allowed: {', '.join(ROLES)}")
    return roles


def verify_changes(zones, label, roles):
    source = snapshot_path(zones, label)
    if not source.is_file():
        raise ZonesError(f"no snapshot {label}: run zones.py snapshot {label} first")
    before = json.loads(source.read_text(encoding="utf-8"))["files"]
    changes = []
    violations = []
    for kind, path in diff(before, zones.walk()):
        if zones.is_producer(path):
            continue
        writers = [role for role in roles if zones.allowed(role, path)]
        if writers:
            changes.append({"kind": kind, "path": path, "roles": writers})
        else:
            violations.append({"kind": kind, "path": path, "owners": zones.owners(path)})
    return changes, violations


def command_verify(zones, args):
    roles = parse_roles(args.roles)
    changes, violations = verify_changes(zones, args.label, roles)
    if args.json:
        print(json.dumps({"changes": changes, "violations": violations}, ensure_ascii=False, indent=2))
        return 1 if violations else 0
    for role in roles:
        own = [change for change in changes if role in change["roles"]]
        if own:
            print(f"{role}:")
            for change in own:
                print(f"  {change['kind']} {change['path']}")
    if not changes and not violations:
        print("no changes")
    for violation in violations:
        owners = ", ".join(violation["owners"]) or "nobody"
        print(f"VIOLATION {violation['kind']} {violation['path']} (zone of: {owners})")
    if violations:
        print(f"TOTAL: {len(violations)} violations")
        return 1
    print("OK: no violations")
    return 0


def lock_paths(zones, name):
    base = zones.project / STATE_DIR / "locks"
    return base / f"{check_label(name)}.json", base / name


def lock_tests(zones, name):
    patterns = zones.acceptance_patterns()
    if not patterns:
        print("zones: paths.acceptance_tests is empty — the lock is empty", file=sys.stderr)
    files = zones.walk(patterns)
    manifest, copies = lock_paths(zones, name)
    if copies.exists():
        shutil.rmtree(copies)
    copies.mkdir(parents=True, exist_ok=True)
    for path in files:
        destination = copies / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(zones.project / path, destination)
    manifest.write_text(
        json.dumps({"name": name, "patterns": patterns, "files": files}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return len(files)


def command_lock(zones, args):
    print(f"lock {args.name}: {lock_tests(zones, args.name)} files")
    return 0


def has_lock(zones, name="acceptance"):
    return lock_paths(zones, name)[0].is_file()


def check_lock(zones, name, restore=False):
    manifest, copies = lock_paths(zones, name)
    if not manifest.is_file():
        raise ZonesError(f"no lock {name}: run zones.py lock --name {name} first")
    lock = json.loads(manifest.read_text(encoding="utf-8"))
    changes = diff(lock["files"], zones.walk(lock["patterns"]))
    if restore:
        for kind, path in changes:
            target = zones.project / path
            if kind == "added":
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(copies / path, target)
    return changes


def command_check_lock(zones, args):
    changes = check_lock(zones, args.name, args.restore)
    if not changes:
        print(f"lock {args.name}: unchanged")
        return 0
    for kind, path in changes:
        print(f"CHANGED {kind} {path}")
    if args.restore:
        print(f"lock {args.name}: restored {len(changes)}")
    return 1


def command_who(zones, args):
    path = relative_to_project(zones.project, Path(args.path))
    if path is None:
        print("path is outside the project")
        return 0
    own = [role for role in ROLES if zones.matches(path, zones.role_patterns(role))]
    shared = [role for role in ROLES if zones.matches(path, zones.shared_patterns(role))]
    print(f"{path}")
    print(f"  role zone: {', '.join(own) or 'nobody'}")
    print(f"  shared zone for: {', '.join(shared) or 'nobody'}")
    print(f"  producer zone: {'yes' if zones.is_producer(path) else 'no'}")
    return 0


def command_zones(zones, args):
    roles = [args.role] if args.role else list(ROLES)
    parse_roles(",".join(roles))
    for role in roles:
        print(f"{role}:")
        for pattern in zones.role_patterns(role) + zones.shared_patterns(role):
            print(f"  {pattern}")
    print("producer:")
    for pattern in zones.producer_patterns():
        print(f"  {pattern}")
    return 0


def relative_to_project(project, path):
    resolved = Path(os.path.normpath(path if path.is_absolute() else project / path))
    try:
        return resolved.relative_to(project).as_posix()
    except ValueError:
        return None


def role_from_agent(agent_type):
    role = (agent_type or "").rsplit(":", 1)[-1].strip()
    if role.startswith("studio-"):
        role = role[len("studio-"):]
    return role if role in ROLES else None


def hook_paths(payload):
    tool_input = payload.get("tool_input")
    paths = []
    command = ""
    if isinstance(tool_input, dict):
        for key in ("file_path", "notebook_path"):
            if isinstance(tool_input.get(key), str) and tool_input[key]:
                paths.append(tool_input[key])
        if isinstance(tool_input.get("command"), str):
            command = tool_input["command"]
    elif isinstance(tool_input, str):
        command = tool_input
    if payload.get("tool_name") == "apply_patch" or "*** Begin Patch" in command:
        for line in command.splitlines():
            match = PATCH_LINE.match(line)
            if match:
                paths.append(match.group(1))
    return paths


def command_hook(args):
    # A broken hook must not break unrelated sessions: any parse error allows the write.
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    role = role_from_agent(payload.get("agent_type"))
    if role is None:
        return 0
    root_value = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    root = Path(root_value).resolve()
    try:
        zones = Zones(root)
    except ZonesError:
        return 0
    base = Path(payload.get("cwd") or root).resolve()
    denied = []
    for raw in hook_paths(payload):
        candidate = Path(raw)
        absolute = candidate if candidate.is_absolute() else base / candidate
        relative = relative_to_project(zones.project, absolute)
        if relative is None:
            continue
        if not zones.allowed(role, relative):
            denied.append(relative)
    if not denied:
        return 0
    zone = ", ".join(zones.role_patterns(role) + zones.shared_patterns(role)) or "empty"
    print(
        f"game-studio: role {role} may not write {', '.join(denied)}. "
        f"Its zone: {zone}. If this work is needed, put it under NEW TASKS or HANDOFF "
        f"in your summary to the producer.",
        file=sys.stderr,
    )
    return 2


def build_parser():
    parser = argparse.ArgumentParser(description="Role write zones of the studio (Studio/studio.json).")
    parser.add_argument("--project", default=".", help="game project root (default: current directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="snapshot project files before a wave")
    snapshot.add_argument("label", help="label, e.g. W3")

    verify = sub.add_parser("verify", help="check changes after a wave against role zones")
    verify.add_argument("label", help="snapshot label")
    verify.add_argument("--roles", required=True, help="comma-separated roles of the wave")
    verify.add_argument("--json", action="store_true", help="JSON output")

    lock = sub.add_parser("lock", help="lock acceptance tests (hashes and copies)")
    lock.add_argument("--name", default="acceptance", help="lock name")

    check = sub.add_parser("check-lock", help="check the test lock")
    check.add_argument("--name", default="acceptance", help="lock name")
    check.add_argument("--restore", action="store_true", help="restore tests from the lock")

    sub.add_parser("hook", help="PreToolUse hook: JSON on stdin, exit 2 means the write is denied")

    who = sub.add_parser("who", help="who may write a path")
    who.add_argument("path", help="path relative to the project")

    zones = sub.add_parser("zones", help="expanded role zones")
    zones.add_argument("--role", help="only this role")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "hook":
        return command_hook(args)
    handlers = {
        "snapshot": command_snapshot,
        "verify": command_verify,
        "lock": command_lock,
        "check-lock": command_check_lock,
        "who": command_who,
        "zones": command_zones,
    }
    try:
        return handlers[args.command](Zones(args.project), args)
    except ZonesError as error:
        print(f"zones: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
