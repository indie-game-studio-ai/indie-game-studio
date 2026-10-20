#!/usr/bin/env python3
"""SVG → PNG with the first rasterizer found on this machine."""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

CHROME_APP_PATHS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
CHROME_COMMANDS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"]
INSTALL_HINT = (
    "No SVG rasterizer found. Install one of:\n"
    "  macOS: brew install resvg   (or brew install librsvg)\n"
    "  Linux: apt install librsvg2-bin   (or cargo install resvg)\n"
    "inkscape, ImageMagick (magick) or Chrome/Chromium also work."
)


def find_chrome():
    for path in CHROME_APP_PATHS:
        if os.path.exists(path):
            return path
    for command in CHROME_COMMANDS:
        found = shutil.which(command)
        if found:
            return found
    return None


def find_backend():
    for name in ("resvg", "rsvg-convert", "inkscape", "magick"):
        found = shutil.which(name)
        if found:
            return name, found
    chrome = find_chrome()
    if chrome:
        return "chrome", chrome
    return None, None


def _length(value):
    if not value:
        return None
    match = re.match(r"\s*([0-9.]+)\s*(px)?\s*$", value)
    return int(round(float(match.group(1)))) if match else None


def svg_size(svg_path):
    with open(svg_path, encoding="utf-8") as svg_file:
        text = svg_file.read(65536)
    tag = re.search(r"<svg\b[^>]*>", text, re.S)
    if not tag:
        return None, None
    attributes = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', tag.group(0)))
    width = _length(attributes.get("width"))
    height = _length(attributes.get("height"))
    view_box = attributes.get("viewBox")
    if view_box and (width is None or height is None):
        parts = [float(part) for part in re.split(r"[\s,]+", view_box.strip()) if part]
        if len(parts) == 4:
            if width is None and height is None:
                width, height = int(round(parts[2])), int(round(parts[3]))
            elif width is None:
                width = int(round(height * parts[2] / parts[3]))
            else:
                height = int(round(width * parts[3] / parts[2]))
    return width, height


def target_size(svg_path, width, height):
    natural_width, natural_height = svg_size(svg_path)
    if width and height:
        return width, height
    if width and natural_width and natural_height:
        return width, int(round(width * natural_height / natural_width))
    if height and natural_width and natural_height:
        return int(round(height * natural_width / natural_height)), height
    return natural_width or width or 512, natural_height or height or 512


def chrome_command(chrome, svg_path, out_path, width, height):
    # Chrome screenshots a whole page; the HTML wrapper removes margins and the white background.
    html = tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8")
    html.write(
        "<!doctype html><html><head><style>html,body{margin:0;padding:0;background:transparent;}"
        "img{display:block;width:%dpx;height:%dpx;}</style></head><body><img src=\"file://%s\"></body></html>"
        % (width, height, os.path.abspath(svg_path))
    )
    html.close()
    command = [
        chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
        "--allow-file-access-from-files", "--default-background-color=00000000",
        "--window-size=%d,%d" % (width, height),
        "--screenshot=%s" % os.path.abspath(out_path),
        "file://" + html.name,
    ]
    return command, html.name


def convert(svg_path, out_path, width=None, height=None):
    backend, executable = find_backend()
    if backend is None:
        raise FileNotFoundError(INSTALL_HINT)
    width, height = target_size(svg_path, width, height)
    cleanup = None
    if backend == "resvg":
        command = [executable, "-w", str(width), "-h", str(height), svg_path, out_path]
    elif backend == "rsvg-convert":
        command = [executable, "-w", str(width), "-h", str(height), "-o", out_path, svg_path]
    elif backend == "inkscape":
        command = [executable, svg_path, "--export-type=png", "--export-filename=" + out_path,
                   "-w", str(width), "-h", str(height)]
    elif backend == "magick":
        command = [executable, "-background", "none", "-density", "384", svg_path,
                   "-resize", "%dx%d!" % (width, height), out_path]
    else:
        command, cleanup = chrome_command(executable, svg_path, out_path, width, height)
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
    finally:
        if cleanup:
            os.unlink(cleanup)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("%s exited with code %d: %s" % (
            backend, result.returncode, result.stderr.decode("utf-8", "replace").strip()[-500:]))
    return backend, width, height


def main(argv=None):
    parser = argparse.ArgumentParser(description="SVG → PNG via resvg, rsvg-convert, inkscape, magick or Chrome.")
    parser.add_argument("svg", nargs="?", help="input SVG")
    parser.add_argument("png", nargs="?", help="output PNG")
    parser.add_argument("--width", type=int, help="PNG width; without --height the aspect ratio is kept")
    parser.add_argument("--height", type=int, help="PNG height")
    parser.add_argument("--which", action="store_true", help="print which rasterizer would be used")
    args = parser.parse_args(argv)

    if args.which:
        backend, executable = find_backend()
        if backend is None:
            print(INSTALL_HINT, file=sys.stderr)
            return 3
        print("%s: %s" % (backend, executable))
        return 0
    if not args.svg or not args.png:
        parser.error("IN.svg and OUT.png are required")
    if not os.path.isfile(args.svg):
        print("no such file: %s" % args.svg, file=sys.stderr)
        return 2
    try:
        backend, width, height = convert(args.svg, args.png, args.width, args.height)
    except FileNotFoundError as error:
        print(error, file=sys.stderr)
        return 3
    except RuntimeError as error:
        print(error, file=sys.stderr)
        return 1
    print("%s (%dx%d, %s)" % (args.png, width, height, backend))
    return 0


if __name__ == "__main__":
    sys.exit(main())
