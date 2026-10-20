#!/usr/bin/env python3
"""Lays the studio out in a game project: templates, protocol and tools.

Run it from the skill folder: the copy in Studio/tools/ has no templates next to it.
"""
import argparse
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path

GITIGNORE_LINE = "Studio/.state/"


def copy_templates(templates, project):
    created = []
    skipped = []
    for root, dirs, names in os.walk(templates):
        dirs[:] = sorted(name for name in dirs if name != "__pycache__")
        relative_root = Path(root).relative_to(templates)
        (project / relative_root).mkdir(parents=True, exist_ok=True)
        for name in sorted(names):
            if name == ".DS_Store":
                continue
            relative = (relative_root / name).as_posix()
            destination = project / relative
            if destination.exists():
                skipped.append(relative)
            else:
                shutil.copy2(Path(root) / name, destination)
                created.append(relative)
    return created, skipped


def copy_tools(skill_dir, project, update):
    updated = []
    tools_dir = project / "Studio" / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted((skill_dir / "tools").glob("*.py"))
    protocol = skill_dir / "references" / "protocol.md"
    pairs = [(source, tools_dir / source.name) for source in sources]
    if protocol.is_file():
        pairs.append((protocol, project / "Studio" / "PROTOCOL.md"))
    for source, destination in pairs:
        if destination.exists() and not update:
            continue
        shutil.copy2(source, destination)
        if destination.suffix == ".py":
            mode = destination.stat().st_mode
            destination.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        updated.append(destination.relative_to(project).as_posix())
    return updated


def fill_empty_field(project, field, value):
    config = project / "Studio" / "studio.json"
    if not value or not config.is_file():
        return False
    text = config.read_text(encoding="utf-8")
    # Text replacement instead of json.dump keeps the hand-made formatting of the file.
    updated, count = re.subn(
        rf'"{field}":\s*""', f'"{field}": {json.dumps(value, ensure_ascii=False)}', text, count=1
    )
    if count:
        config.write_text(updated, encoding="utf-8")
    return bool(count)


def ensure_gitignore(project):
    path = project / ".gitignore"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(line.strip() == GITIGNORE_LINE for line in text.splitlines()):
        return False
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + GITIGNORE_LINE + "\n", encoding="utf-8")
    return True


def language_code(value):
    if not re.match(r"^[a-z]{2}$", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an ISO 639-1 code like en or ru")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lay the studio out in a game project, or resume work in it.")
    parser.add_argument("project", help="game project root")
    parser.add_argument("--game", help="game title for Studio/studio.json")
    parser.add_argument("--language", type=language_code, help="studio working language, ISO 639-1 (en, ru, …)")
    parser.add_argument("--update-tools", action="store_true", help="overwrite Studio/tools and Studio/PROTOCOL.md")
    parser.add_argument("--skill-dir", help="skill folder; defaults to the parent of the tools folder")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args(argv)

    skill_dir = Path(args.skill_dir).resolve() if args.skill_dir else Path(__file__).resolve().parent.parent
    templates = skill_dir / "templates"
    if not templates.is_dir():
        print(f"studio_init: no templates in {templates}; run the script from the skill folder", file=sys.stderr)
        return 2
    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"studio_init: project folder does not exist: {project}", file=sys.stderr)
        return 2

    mode = "resume" if (project / "Studio" / "ledger.md").is_file() else "init"
    created, skipped = copy_templates(templates, project)
    tools_updated = copy_tools(skill_dir, project, args.update_tools)
    game_named = fill_empty_field(project, "game", args.game)
    language_set = fill_empty_field(project, "language", args.language)
    gitignore_updated = ensure_gitignore(project)

    if args.json:
        print(
            json.dumps(
                {
                    "mode": mode,
                    "project": str(project),
                    "created": created,
                    "skipped": skipped,
                    "tools_updated": tools_updated,
                    "game_named": game_named,
                    "language_set": language_set,
                    "gitignore_updated": gitignore_updated,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"Studio: {mode} in {project}")
    print(f"Files created: {len(created)}, already present: {len(skipped)}")
    if tools_updated:
        print("Updated: " + ", ".join(tools_updated))
    if game_named:
        print(f"Game: {args.game}")
    if language_set:
        print(f"Language: {args.language}")
    if gitignore_updated:
        print(f"Added {GITIGNORE_LINE} to .gitignore")
    return 0


if __name__ == "__main__":
    sys.exit(main())
