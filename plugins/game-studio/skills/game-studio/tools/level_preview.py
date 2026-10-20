#!/usr/bin/env python3
"""ASCII levels: PNG preview, marker and reachability checks, JSON export for the engine."""

import argparse
import json
import os
import re
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pngio  # noqa: E402

UNKNOWN_COLOR = (255, 0, 255, 255)
DEFAULT_COLOR = (128, 128, 128, 255)
MODES = ("topdown", "platformer", "none")


def load_level(path):
    with open(path, encoding="utf-8") as level_file:
        lines = [line.rstrip("\r\n") for line in level_file]
    meta = {}
    grid = []
    for line in lines:
        if line.startswith(";"):
            if ":" in line:
                key, value = line[1:].split(":", 1)
                meta[key.strip()] = value.strip()
            continue
        grid.append(line)
    while grid and not grid[-1].strip():
        grid.pop()
    while grid and not grid[0].strip():
        grid.pop(0)
    return meta, grid


def load_legend(path):
    with open(path, encoding="utf-8") as legend_file:
        legend = json.load(legend_file)
    legend.setdefault("tile_size", 16)
    legend.setdefault("tiles", {})
    legend.setdefault("required_markers", {})
    return legend


def find_markers(grid, legend):
    markers = {}
    for y, row in enumerate(grid):
        for x, symbol in enumerate(row):
            marker = legend["tiles"].get(symbol, {}).get("marker")
            if marker:
                markers.setdefault(marker, []).append([x, y])
    return markers


def count_matches(rule, count):
    rule = str(rule).strip()
    if rule.endswith("+"):
        return count >= int(rule[:-1])
    range_match = re.match(r"^(\d+)-(\d+)$", rule)
    if range_match:
        return int(range_match.group(1)) <= count <= int(range_match.group(2))
    return count == int(rule)


def reachable_cells(grid, legend, start):
    width = len(grid[0])
    seen = {tuple(start)}
    queue = deque([tuple(start)])
    while queue:
        x, y = queue.popleft()
        for next_x, next_y in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if not (0 <= next_y < len(grid) and 0 <= next_x < width) or (next_x, next_y) in seen:
                continue
            if legend["tiles"].get(grid[next_y][next_x], {}).get("solid"):
                continue
            seen.add((next_x, next_y))
            queue.append((next_x, next_y))
    return seen


def check(grid, legend, mode):
    errors = []
    warnings = []
    if not grid:
        return ["level is empty"], warnings
    widths = {len(row) for row in grid}
    if len(widths) > 1:
        errors.append("rows have different lengths: %s" % ", ".join(
            "row %d — %d" % (index, len(row)) for index, row in enumerate(grid) if len(row) != len(grid[0])))
    unknown = sorted({symbol for row in grid for symbol in row if symbol not in legend["tiles"]})
    if unknown:
        errors.append("symbols not in legend: %s" % " ".join(repr(symbol) for symbol in unknown))
    markers = find_markers(grid, legend)
    for marker, rule in legend["required_markers"].items():
        count = len(markers.get(marker, []))
        if not count_matches(rule, count):
            errors.append("marker %s: found %d, required %s" % (marker, count, rule))
    if mode == "platformer":
        warnings.append("platformer mode: reachability is not checked, verify by hand or playtest")
    elif mode == "topdown" and not errors:
        spawns = markers.get("spawn", [])
        if not spawns:
            errors.append("no spawn marker, cannot check reachability")
        else:
            reachable = reachable_cells(grid, legend, spawns[0])
            for x, y in markers.get("exit", []):
                if (x, y) not in reachable:
                    errors.append("exit (%d, %d) is unreachable from spawn" % (x, y))
            for x, y in markers.get("pickup", []):
                if (x, y) not in reachable:
                    warnings.append("pickup (%d, %d) is unreachable from spawn" % (x, y))
    return errors, warnings


def resolve_image(image_path, legend_path):
    candidates = [image_path, os.path.join(os.path.dirname(os.path.abspath(legend_path)), image_path)]
    for candidate in candidates:
        if os.path.isfile(candidate):
            return candidate
    return None


def render(grid, legend, legend_path, out_path, cell=None):
    cell = cell or legend["tile_size"]
    width = max(len(row) for row in grid) * cell
    height = len(grid) * cell
    canvas = pngio.new_canvas(width, height, (0, 0, 0, 255))
    tile_images = {}
    for symbol, tile in legend["tiles"].items():
        image = tile.get("image")
        resolved = resolve_image(image, legend_path) if image else None
        if resolved:
            image_width, image_height, pixels = pngio.read_png(resolved)
            tile_images[symbol] = pngio.scale_nearest(image_width, image_height, pixels, cell, cell)
    for y, row in enumerate(grid):
        for x, symbol in enumerate(row):
            tile = legend["tiles"].get(symbol)
            if tile is None:
                color = UNKNOWN_COLOR
            else:
                color = pngio.parse_color(tile["color"]) if tile.get("color") else DEFAULT_COLOR
            pngio.fill_rect(canvas, width, height, x * cell, y * cell, cell, cell, color)
            if symbol in tile_images:
                pngio.blit(canvas, width, height, tile_images[symbol], cell, cell, x * cell, y * cell)
    pngio.write_png(out_path, width, height, canvas)
    return width, height


def export(grid, legend, meta, mode):
    return {
        "title": meta.get("title", ""),
        "mode": mode,
        "width": max((len(row) for row in grid), default=0),
        "height": len(grid),
        "tile_size": legend["tile_size"],
        "grid": grid,
        "tiles": {symbol: tile.get("name", symbol) for symbol, tile in legend["tiles"].items()},
        "markers": find_markers(grid, legend),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="ASCII levels: preview, check, export.")
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (("render", "draw a PNG preview"),
                            ("check", "check grid, markers and reachability"),
                            ("export", "export the level to JSON for the engine")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("level", help="path to *.level.txt")
        command.add_argument("--legend", required=True, help="path to legend.json")
        command.add_argument("--mode", choices=MODES, help="mode (default: from \"; mode:\" or topdown)")
        if name in ("render", "export"):
            command.add_argument("--out", required=True, help="output file")
        if name == "render":
            command.add_argument("--cell", type=int, help="cell size in pixels (default: tile_size)")
    args = parser.parse_args(argv)

    meta, grid = load_level(args.level)
    legend = load_legend(args.legend)
    mode = args.mode or meta.get("mode") or "topdown"
    if mode not in MODES:
        print("unknown mode %r, expected one of %s" % (mode, ", ".join(MODES)), file=sys.stderr)
        return 1

    if args.command == "check":
        errors, warnings = check(grid, legend, mode)
        for warning in warnings:
            print("warning: " + warning)
        for error in errors:
            print("error: " + error)
        if errors:
            return 1
        print("%s: OK (%dx%d, mode %s)" % (args.level, len(grid[0]), len(grid), mode))
        return 0
    if not grid:
        print("%s: level is empty" % args.level, file=sys.stderr)
        return 1
    if args.command == "render":
        width, height = render(grid, legend, args.legend, args.out, args.cell)
        print("%s (%dx%d)" % (args.out, width, height))
        return 0
    with open(args.out, "w", encoding="utf-8") as out_file:
        json.dump(export(grid, legend, meta, mode), out_file, ensure_ascii=False, indent=2)
        out_file.write("\n")
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
