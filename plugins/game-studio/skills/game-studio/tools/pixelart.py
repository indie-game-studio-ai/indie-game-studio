#!/usr/bin/env python3
"""Pixel art from a text spec: a symbol palette plus frames as strings → PNG sheet and atlas."""

import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pngio  # noqa: E402


def load_spec(path):
    with open(path, encoding="utf-8") as spec_file:
        return json.load(spec_file)


def load_project_palette(path):
    with open(path, encoding="utf-8") as palette_file:
        data = json.load(palette_file)
    colors = data.get("colors", data)
    return {pngio.parse_color(value)[:3] for value in colors.values() if value}


def validate(spec, project_palette=None):
    errors = []
    size = spec.get("size")
    if not (isinstance(size, list) and len(size) == 2 and all(isinstance(v, int) and v > 0 for v in size)):
        return ["size must be [width, height] of positive integers"]
    width, height = size
    palette = spec.get("palette")
    if not isinstance(palette, dict) or not palette:
        errors.append("palette is empty or not an object")
        palette = {}
    parsed_palette = {}
    for symbol, color in palette.items():
        if len(symbol) != 1:
            errors.append("palette: key %r must be a single character" % symbol)
            continue
        try:
            parsed_palette[symbol] = pngio.parse_color(color)
        except ValueError as error:
            errors.append("palette[%r]: %s" % (symbol, error))
    if project_palette is not None:
        for symbol, color in sorted(parsed_palette.items()):
            if color[3] != 0 and color[:3] not in project_palette:
                errors.append("palette[%r] = %s is not in the project palette" % (symbol, palette[symbol]))
    frames = spec.get("frames")
    if not isinstance(frames, dict) or not frames:
        errors.append("frames is empty or not an object")
        frames = {}
    for frame_name, rows in frames.items():
        if not isinstance(rows, list):
            errors.append("frame %s: expected a list of strings" % frame_name)
            continue
        if len(rows) != height:
            errors.append("frame %s: %d rows, but height is %d" % (frame_name, len(rows), height))
        for row_index, row in enumerate(rows):
            if len(row) != width:
                errors.append("frame %s, row %d: length %d, but width is %d" % (frame_name, row_index, len(row), width))
            unknown = sorted({symbol for symbol in row if symbol not in palette})
            if unknown:
                errors.append("frame %s, row %d: symbols not in palette: %s" % (frame_name, row_index, "".join(unknown)))
    for animation_name, animation in (spec.get("animations") or {}).items():
        for frame_name in animation.get("frames", []):
            if frame_name not in frames:
                errors.append("animation %s: unknown frame %s" % (animation_name, frame_name))
        fps = animation.get("fps", 1)
        if not isinstance(fps, (int, float)) or fps <= 0:
            errors.append("animation %s: fps must be > 0" % animation_name)
    return errors


def frame_pixels(spec, frame_name):
    width, height = spec["size"]
    palette = {symbol: pngio.parse_color(color) for symbol, color in spec["palette"].items()}
    canvas = bytearray(width * height * 4)
    for y, row in enumerate(spec["frames"][frame_name]):
        for x, symbol in enumerate(row):
            canvas[(y * width + x) * 4:(y * width + x) * 4 + 4] = bytes(palette[symbol])
    return canvas


def report_invalid(spec, path):
    errors = validate(spec)
    if errors:
        print("%s: spec has errors, run validate first:" % path, file=sys.stderr)
        for error in errors:
            print("  - " + error, file=sys.stderr)
    return bool(errors)


def render(spec, out_path, atlas_path=None, columns=None, padding=0):
    width, height = spec["size"]
    frame_names = list(spec["frames"])
    columns = columns or len(frame_names)
    rows = math.ceil(len(frame_names) / columns)
    sheet_width = columns * width + (columns - 1) * padding
    sheet_height = rows * height + (rows - 1) * padding
    sheet = pngio.new_canvas(sheet_width, sheet_height)
    frames_atlas = {}
    for index, frame_name in enumerate(frame_names):
        left = (index % columns) * (width + padding)
        top = (index // columns) * (height + padding)
        pngio.blit(sheet, sheet_width, sheet_height, frame_pixels(spec, frame_name), width, height, left, top)
        frames_atlas[frame_name] = {"x": left, "y": top, "w": width, "h": height}
    pngio.write_png(out_path, sheet_width, sheet_height, sheet)
    if atlas_path is None:
        stem = os.path.splitext(out_path)[0]
        atlas_path = stem + ".atlas.json"
    atlas = {
        "image": os.path.basename(out_path),
        "frameWidth": width,
        "frameHeight": height,
        "frames": frames_atlas,
        "animations": spec.get("animations", {}),
        "origin": spec.get("origin", [width // 2, height // 2]),
    }
    with open(atlas_path, "w", encoding="utf-8") as atlas_file:
        json.dump(atlas, atlas_file, ensure_ascii=False, indent=2)
        atlas_file.write("\n")
    return atlas_path


def preview(spec, out_path, scale=8, gap=4):
    width, height = spec["size"]
    frame_names = list(spec["frames"])
    scaled_width = width * scale
    scaled_height = height * scale
    canvas_width = len(frame_names) * scaled_width + (len(frame_names) + 1) * gap
    canvas_height = scaled_height + 2 * gap
    canvas = pngio.checkerboard(canvas_width, canvas_height, cell=max(4, scale))
    for index, frame_name in enumerate(frame_names):
        scaled = pngio.scale_nearest(width, height, frame_pixels(spec, frame_name), scaled_width, scaled_height)
        left = gap + index * (scaled_width + gap)
        pngio.blit(canvas, canvas_width, canvas_height, scaled, scaled_width, scaled_height, left, gap)
    pngio.write_png(out_path, canvas_width, canvas_height, canvas)


def new_spec(name, width, height):
    empty_row = "." * width
    return {
        "name": name,
        "size": [width, height],
        "palette": {".": None, "k": "#1a1c2c", "w": "#f4f4f4"},
        "frames": {"idle_0": [empty_row] * height},
        "animations": {"idle": {"frames": ["idle_0"], "fps": 1, "loop": True}},
        "origin": [width // 2, height // 2],
    }


def parse_size(text):
    try:
        width, height = (int(part) for part in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 16x16")
    return width, height


def main(argv=None):
    parser = argparse.ArgumentParser(description="Pixel art: *.sprite.json spec → PNG sheet, atlas, preview.")
    commands = parser.add_subparsers(dest="command", required=True)

    render_parser = commands.add_parser("render", help="build a PNG sprite sheet and a JSON atlas")
    render_parser.add_argument("spec", help="path to *.sprite.json")
    render_parser.add_argument("--out", required=True, help="output PNG")
    render_parser.add_argument("--atlas", help="atlas path (default: <stem>.atlas.json next to the PNG)")
    render_parser.add_argument("--columns", type=int, help="frames per sheet row (default: all in one row)")
    render_parser.add_argument("--padding", type=int, default=0, help="gap between frames, in pixels")

    preview_parser = commands.add_parser("preview", help="enlarged preview of all frames on a checkerboard")
    preview_parser.add_argument("spec", help="path to *.sprite.json")
    preview_parser.add_argument("--out", required=True, help="output PNG")
    preview_parser.add_argument("--scale", type=int, default=8, help="scale factor")

    validate_parser = commands.add_parser("validate", help="validate a spec")
    validate_parser.add_argument("spec", help="path to *.sprite.json")
    validate_parser.add_argument("--palette", help="project palette, e.g. References/palette.json")

    new_parser = commands.add_parser("new", help="create a spec skeleton")
    new_parser.add_argument("name", help="sprite name")
    new_parser.add_argument("--size", type=parse_size, default=(16, 16), help="frame size, e.g. 16x16")
    new_parser.add_argument("--out", required=True, help="output *.sprite.json")

    args = parser.parse_args(argv)

    if args.command == "new":
        if os.path.exists(args.out):
            print("%s already exists, not overwriting" % args.out, file=sys.stderr)
            return 1
        with open(args.out, "w", encoding="utf-8") as spec_file:
            json.dump(new_spec(args.name, *args.size), spec_file, ensure_ascii=False, indent=2)
            spec_file.write("\n")
        print(args.out)
        return 0

    spec = load_spec(args.spec)
    if args.command == "validate":
        project_palette = load_project_palette(args.palette) if args.palette else None
        errors = validate(spec, project_palette)
        if errors:
            print("%s: %d error(s)" % (args.spec, len(errors)))
            for error in errors:
                print("  - " + error)
            return 1
        print("%s: OK, %d frame(s)" % (args.spec, len(spec["frames"])))
        return 0

    if report_invalid(spec, args.spec):
        return 1
    if args.command == "render":
        atlas_path = render(spec, args.out, args.atlas, args.columns, args.padding)
        print("%s, %s" % (args.out, atlas_path))
    else:
        preview(spec, args.out, args.scale)
        print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
