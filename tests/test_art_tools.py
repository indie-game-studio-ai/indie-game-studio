import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import zlib

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "game-studio", "skills", "game-studio")
TOOLS = os.path.join(SKILL_DIR, "tools")
TEMPLATES = os.path.join(SKILL_DIR, "templates")
sys.path.insert(0, TOOLS)

import contact_sheet  # noqa: E402
import pixelart  # noqa: E402
import pngio  # noqa: E402
import svg2png  # noqa: E402

EXAMPLE_SPRITE = os.path.join(TEMPLATES, "Art", "source", "_example.sprite.json")


def run_quietly(main, argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(argv)
    return code, output.getvalue()


def png_chunk(kind, data):
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def paeth(left, up, up_left):
    estimate = left + up - up_left
    candidates = [(abs(estimate - left), 0, left), (abs(estimate - up), 1, up), (abs(estimate - up_left), 2, up_left)]
    return min(candidates)[2]


def encode_rgb_with_filters(width, rows, filters):
    """Encode RGB with a chosen filter per row, the way browsers and editors do."""
    raw = bytearray()
    previous = bytes(width * 3)
    for row, filter_type in zip(rows, filters):
        line = bytearray()
        for i, value in enumerate(row):
            left = row[i - 3] if i >= 3 else 0
            up = previous[i]
            up_left = previous[i - 3] if i >= 3 else 0
            predictor = {0: 0, 1: left, 2: up, 3: (left + up) >> 1, 4: paeth(left, up, up_left)}[filter_type]
            line.append((value - predictor) & 0xFF)
        raw.append(filter_type)
        raw += line
        previous = bytes(row)
    header = struct.pack(">IIBBBBB", width, len(rows), 8, 2, 0, 0, 0)
    return pngio.PNG_SIGNATURE + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(bytes(raw))) + png_chunk(b"IEND", b"")


class PngIoTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_rgba_survives_write_and_read(self):
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 128, 0, 0, 255, 0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120])
        path = os.path.join(self.directory, "image.png")
        pngio.write_png(path, 3, 2, pixels)
        self.assertEqual(pngio.read_png(path), (3, 2, bytearray(pixels)))

    def test_reads_rgb_with_every_row_filter(self):
        rows = [bytes((x * 37 + y * 11 + channel * 5) % 256 for x in range(4) for channel in range(3)) for y in range(5)]
        path = os.path.join(self.directory, "filters.png")
        with open(path, "wb") as png_file:
            png_file.write(encode_rgb_with_filters(4, rows, [0, 1, 2, 3, 4]))
        width, height, rgba = pngio.read_png(path)
        self.assertEqual((width, height), (4, 5))
        for y, row in enumerate(rows):
            for x in range(4):
                self.assertEqual(tuple(rgba[(y * 4 + x) * 4:(y * 4 + x) * 4 + 4]), tuple(row[x * 3:x * 3 + 3]) + (255,))

    def test_reads_palette_png_with_transparency(self):
        header = struct.pack(">IIBBBBB", 2, 1, 8, 3, 0, 0, 0)
        data = (pngio.PNG_SIGNATURE + png_chunk(b"IHDR", header)
                + png_chunk(b"PLTE", bytes([255, 0, 0, 0, 0, 255])) + png_chunk(b"tRNS", bytes([0]))
                + png_chunk(b"IDAT", zlib.compress(bytes([0, 0, 1]))) + png_chunk(b"IEND", b""))
        path = os.path.join(self.directory, "palette.png")
        with open(path, "wb") as png_file:
            png_file.write(data)
        self.assertEqual(pngio.read_png(path)[2], bytearray([255, 0, 0, 0, 0, 0, 255, 255]))

    def test_interlaced_png_is_rejected_with_reason(self):
        header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 1)
        path = os.path.join(self.directory, "interlaced.png")
        with open(path, "wb") as png_file:
            png_file.write(pngio.PNG_SIGNATURE + png_chunk(b"IHDR", header) + png_chunk(b"IEND", b""))
        with self.assertRaisesRegex(pngio.PngError, "interlaced"):
            pngio.read_png(path)

    def test_parse_color_formats(self):
        self.assertEqual(pngio.parse_color("#f00"), (255, 0, 0, 255))
        self.assertEqual(pngio.parse_color("#11223344"), (17, 34, 51, 68))
        self.assertEqual(pngio.parse_color(None), (0, 0, 0, 0))
        with self.assertRaises(ValueError):
            pngio.parse_color("red")


class PixelArtTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def write_spec(self, spec):
        path = os.path.join(self.directory, "sprite.sprite.json")
        with open(path, "w", encoding="utf-8") as spec_file:
            json.dump(spec, spec_file)
        return path

    def test_example_renders_sheet_and_atlas(self):
        out = os.path.join(self.directory, "ship.png")
        code, _ = run_quietly(pixelart.main, ["render", EXAMPLE_SPRITE, "--out", out])
        self.assertEqual(code, 0)
        width, height, rgba = pngio.read_png(out)
        self.assertEqual((width, height), (32, 16))
        self.assertEqual(tuple(rgba[(0 * width + 7) * 4:(0 * width + 7) * 4 + 4]), (0x1a, 0x1c, 0x2c, 255))
        self.assertEqual(rgba[3], 0)
        with open(os.path.join(self.directory, "ship.atlas.json"), encoding="utf-8") as atlas_file:
            atlas = json.load(atlas_file)
        self.assertEqual(atlas["image"], "ship.png")
        self.assertEqual(atlas["frames"]["thrust_1"], {"x": 16, "y": 0, "w": 16, "h": 16})
        self.assertEqual(atlas["animations"]["thrust"]["fps"], 8)

    def test_render_with_columns_and_padding(self):
        out = os.path.join(self.directory, "grid.png")
        atlas_path = os.path.join(self.directory, "grid.json")
        spec = pixelart.load_spec(EXAMPLE_SPRITE)
        spec["frames"]["thrust_2"] = spec["frames"]["thrust_0"]
        code, _ = run_quietly(pixelart.main, ["render", self.write_spec(spec), "--out", out,
                                              "--atlas", atlas_path, "--columns", "2", "--padding", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(pngio.read_png(out)[:2], (34, 34))
        with open(atlas_path, encoding="utf-8") as atlas_file:
            self.assertEqual(json.load(atlas_file)["frames"]["thrust_2"], {"x": 0, "y": 18, "w": 16, "h": 16})

    def test_preview_scales_all_frames(self):
        out = os.path.join(self.directory, "preview.png")
        code, _ = run_quietly(pixelart.main, ["preview", EXAMPLE_SPRITE, "--out", out, "--scale", "4"])
        self.assertEqual(code, 0)
        self.assertEqual(pngio.read_png(out)[:2], (2 * 64 + 3 * 4, 64 + 2 * 4))

    def test_validate_lists_every_problem(self):
        spec = {"name": "bad", "size": [2, 2], "palette": {".": None, "k": "#000"},
                "frames": {"a": ["..", "kz"], "b": ["..."]},
                "animations": {"run": {"frames": ["a", "missing"], "fps": 6}}}
        code, output = run_quietly(pixelart.main, ["validate", self.write_spec(spec)])
        self.assertEqual(code, 1)
        self.assertIn("symbols not in palette: z", output)
        self.assertIn("1 rows, but height is 2", output)
        self.assertIn("unknown frame missing", output)

    def test_validate_checks_project_palette(self):
        palette_path = os.path.join(self.directory, "palette.json")
        with open(palette_path, "w", encoding="utf-8") as palette_file:
            json.dump({"name": "test", "colors": {"ink": "#1a1c2c"}}, palette_file)
        code, output = run_quietly(pixelart.main, ["validate", EXAMPLE_SPRITE, "--palette", palette_path])
        self.assertEqual(code, 1)
        self.assertIn("is not in the project palette", output)

    def test_render_refuses_invalid_spec(self):
        spec = {"name": "bad", "size": [2, 1], "palette": {".": None}, "frames": {"a": ["x."]}}
        code, _ = run_quietly(pixelart.main, ["render", self.write_spec(spec), "--out", os.path.join(self.directory, "x.png")])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.directory, "x.png")))

    def test_new_creates_valid_skeleton(self):
        path = os.path.join(self.directory, "hero.sprite.json")
        self.assertEqual(run_quietly(pixelart.main, ["new", "hero", "--size", "8x12", "--out", path])[0], 0)
        self.assertEqual(run_quietly(pixelart.main, ["validate", path])[0], 0)
        self.assertEqual(pixelart.load_spec(path)["size"], [8, 12])


class ContactSheetTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def solid_png(self, name, width, height, color):
        path = os.path.join(self.directory, name)
        pngio.write_png(path, width, height, bytes(color) * (width * height))
        return path

    def test_cells_hold_images_scaled_up_by_integer_factor(self):
        red = self.solid_png("red.png", 8, 8, (255, 0, 0, 255))
        blue = self.solid_png("blue.png", 100, 50, (0, 0, 255, 255))
        out = os.path.join(self.directory, "sheet.png")
        code, _ = run_quietly(contact_sheet.main, [out, red, blue, "--cols", "2", "--cell", "64"])
        self.assertEqual(code, 0)
        width, height, rgba = pngio.read_png(out)
        self.assertEqual((width, height), (2 * 64 + 3 * 8, 64 + 2 * 8))
        center_of_first = (40 * width + 40) * 4
        self.assertEqual(tuple(rgba[center_of_first:center_of_first + 4]), (255, 0, 0, 255))
        center_of_second = (40 * width + 8 + 64 + 8 + 32) * 4
        self.assertEqual(tuple(rgba[center_of_second:center_of_second + 4]), (0, 0, 255, 255))

    def test_labels_add_a_text_row(self):
        image = self.solid_png("hero-idle_0.png", 4, 4, (0, 255, 0, 255))
        out = os.path.join(self.directory, "labeled.png")
        self.assertEqual(run_quietly(contact_sheet.main, [out, image, "--cell", "64", "--labels"])[0], 0)
        self.assertGreater(pngio.read_png(out)[1], 64 + 2 * 8)
        self.assertEqual(contact_sheet.label_text("/x/hero-idle_0.png", 6), "HERO-I")

    def test_missing_input_is_reported(self):
        code, output = run_quietly(contact_sheet.main, [os.path.join(self.directory, "o.png"), "nope.png"])
        self.assertEqual(code, 2)
        self.assertIn("nope.png", output)


class SvgToPngTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.saved_path = os.environ.get("PATH", "")
        self.saved_chrome_paths = list(svg2png.CHROME_APP_PATHS)

    def tearDown(self):
        os.environ["PATH"] = self.saved_path
        svg2png.CHROME_APP_PATHS[:] = self.saved_chrome_paths
        shutil.rmtree(self.directory)

    def test_without_backends_exits_3_with_install_hint(self):
        os.environ["PATH"] = self.directory
        svg2png.CHROME_APP_PATHS[:] = []
        code, output = run_quietly(svg2png.main, ["--which"])
        self.assertEqual(code, 3)
        self.assertIn("brew install resvg", output)

    def test_size_comes_from_viewbox(self):
        path = os.path.join(self.directory, "icon.svg")
        with open(path, "w", encoding="utf-8") as svg_file:
            svg_file.write('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 24"></svg>')
        self.assertEqual(svg2png.svg_size(path), (48, 24))
        self.assertEqual(svg2png.target_size(path, 96, None), (96, 48))

    def test_real_conversion_when_fast_backend_exists(self):
        backend, _ = svg2png.find_backend()
        if backend is None or (backend == "chrome" and not os.environ.get("STUDIO_SLOW_TESTS")):
            self.skipTest("no fast rasterizer; Chrome is tested with STUDIO_SLOW_TESTS=1")
        svg_path = os.path.join(self.directory, "half.svg")
        with open(svg_path, "w", encoding="utf-8") as svg_file:
            svg_file.write('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10">'
                           '<rect width="10" height="10" fill="#ff0000"/></svg>')
        png_path = os.path.join(self.directory, "half.png")
        self.assertEqual(run_quietly(svg2png.main, [svg_path, png_path])[0], 0)
        width, height, rgba = pngio.read_png(png_path)
        self.assertEqual((width, height), (20, 10))
        self.assertEqual(tuple(rgba[(5 * 20 + 2) * 4:(5 * 20 + 2) * 4 + 4]), (255, 0, 0, 255))
        self.assertEqual(rgba[(5 * 20 + 17) * 4 + 3], 0)


if __name__ == "__main__":
    unittest.main()
