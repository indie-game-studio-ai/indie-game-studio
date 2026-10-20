#!/usr/bin/env python3
"""Lay out PNGs on one sheet for art and screenshot reviews."""

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pngio  # noqa: E402

FONT_3X5 = {
    "A": ("010", "101", "111", "101", "101"), "B": ("110", "101", "110", "101", "110"),
    "C": ("011", "100", "100", "100", "011"), "D": ("110", "101", "101", "101", "110"),
    "E": ("111", "100", "110", "100", "111"), "F": ("111", "100", "110", "100", "100"),
    "G": ("011", "100", "101", "101", "011"), "H": ("101", "101", "111", "101", "101"),
    "I": ("111", "010", "010", "010", "111"), "J": ("001", "001", "001", "101", "010"),
    "K": ("101", "101", "110", "101", "101"), "L": ("100", "100", "100", "100", "111"),
    "M": ("101", "111", "111", "101", "101"), "N": ("110", "101", "101", "101", "101"),
    "O": ("010", "101", "101", "101", "010"), "P": ("110", "101", "110", "100", "100"),
    "Q": ("010", "101", "101", "110", "011"), "R": ("110", "101", "110", "101", "101"),
    "S": ("011", "100", "010", "001", "110"), "T": ("111", "010", "010", "010", "010"),
    "U": ("101", "101", "101", "101", "111"), "V": ("101", "101", "101", "101", "010"),
    "W": ("101", "101", "111", "111", "101"), "X": ("101", "101", "010", "101", "101"),
    "Y": ("101", "101", "010", "010", "010"), "Z": ("111", "001", "010", "100", "111"),
    "0": ("111", "101", "101", "101", "111"), "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "010", "100", "111"), "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"), "5": ("111", "100", "110", "001", "110"),
    "6": ("011", "100", "111", "101", "111"), "7": ("111", "001", "010", "010", "010"),
    "8": ("111", "101", "111", "101", "111"), "9": ("111", "101", "111", "001", "110"),
    "-": ("000", "000", "111", "000", "000"), "_": ("000", "000", "000", "000", "111"),
    ".": ("000", "000", "000", "000", "010"), " ": ("000", "000", "000", "000", "000"),
}
GLYPH_WIDTH = 3
GLYPH_HEIGHT = 5
LABEL_SCALE = 2
LABEL_COLOR = (235, 235, 235, 255)


def label_text(path, max_chars):
    stem = os.path.splitext(os.path.basename(path))[0].upper()
    text = "".join(symbol if symbol in FONT_3X5 else "_" for symbol in stem)
    return text[:max_chars]


def draw_text(canvas, canvas_width, canvas_height, text, left, top, scale, color):
    for index, symbol in enumerate(text):
        glyph = FONT_3X5[symbol]
        glyph_left = left + index * (GLYPH_WIDTH + 1) * scale
        for row, bits in enumerate(glyph):
            for column, bit in enumerate(bits):
                if bit == "1":
                    pngio.fill_rect(canvas, canvas_width, canvas_height,
                                    glyph_left + column * scale, top + row * scale, scale, scale, color)


def fit(width, height, cell):
    """Small art is scaled up by an integer factor so pixels stay crisp."""
    if width <= cell and height <= cell:
        factor = max(1, min(cell // width, cell // height))
        return width * factor, height * factor
    ratio = min(cell / width, cell / height)
    return max(1, int(width * ratio)), max(1, int(height * ratio))


def build_sheet(out_path, image_paths, columns=None, cell=192, background="#202028", labels=False, gap=8):
    columns = columns or min(len(image_paths), max(1, int(math.ceil(math.sqrt(len(image_paths))))))
    rows = int(math.ceil(len(image_paths) / columns))
    label_height = (GLYPH_HEIGHT * LABEL_SCALE + 6) if labels else 0
    sheet_width = columns * cell + (columns + 1) * gap
    sheet_height = rows * (cell + label_height) + (rows + 1) * gap
    sheet = pngio.new_canvas(sheet_width, sheet_height, pngio.parse_color(background))
    max_chars = cell // ((GLYPH_WIDTH + 1) * LABEL_SCALE)
    for index, path in enumerate(image_paths):
        width, height, pixels = pngio.read_png(path)
        fitted_width, fitted_height = fit(width, height, cell)
        scaled = pngio.scale_nearest(width, height, pixels, fitted_width, fitted_height)
        cell_left = gap + (index % columns) * (cell + gap)
        cell_top = gap + (index // columns) * (cell + label_height + gap)
        pngio.blit(sheet, sheet_width, sheet_height, scaled, fitted_width, fitted_height,
                   cell_left + (cell - fitted_width) // 2, cell_top + (cell - fitted_height) // 2)
        if labels:
            draw_text(sheet, sheet_width, sheet_height, label_text(path, max_chars),
                      cell_left, cell_top + cell + 4, LABEL_SCALE, LABEL_COLOR)
    pngio.write_png(out_path, sheet_width, sheet_height, sheet)
    return sheet_width, sheet_height


def main(argv=None):
    parser = argparse.ArgumentParser(description="Lay out PNG images in a grid on one sheet.")
    parser.add_argument("out", help="output PNG")
    parser.add_argument("images", nargs="+", help="input PNGs")
    parser.add_argument("--cols", type=int, help="columns (default: about the square root of the image count)")
    parser.add_argument("--cell", type=int, default=192, help="cell size, in pixels")
    parser.add_argument("--bg", default="#202028", help="background color")
    parser.add_argument("--labels", action="store_true", help="label images with their file names")
    args = parser.parse_args(argv)
    missing = [path for path in args.images if not os.path.isfile(path)]
    if missing:
        print("missing files: " + ", ".join(missing), file=sys.stderr)
        return 2
    try:
        width, height = build_sheet(args.out, args.images, args.cols, args.cell, args.bg, args.labels)
    except pngio.PngError as error:
        print(error, file=sys.stderr)
        return 1
    print("%s (%dx%d, %d image(s))" % (args.out, width, height, len(args.images)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
