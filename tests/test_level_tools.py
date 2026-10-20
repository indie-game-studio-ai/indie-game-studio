import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "game-studio", "skills", "game-studio")
TOOLS = os.path.join(SKILL_DIR, "tools")
TEMPLATES = os.path.join(SKILL_DIR, "templates")
sys.path.insert(0, TOOLS)

import level_preview  # noqa: E402
import pngio  # noqa: E402

EXAMPLE_LEVEL = os.path.join(TEMPLATES, "Levels", "L00.level.txt")
EXAMPLE_LEGEND = os.path.join(TEMPLATES, "Levels", "legend.json")


def run_quietly(main, argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(argv)
    return code, output.getvalue()


class LevelPreviewTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def write_level(self, text):
        path = os.path.join(self.directory, "level.level.txt")
        with open(path, "w", encoding="utf-8") as level_file:
            level_file.write(text)
        return path

    def check(self, text, *extra):
        return run_quietly(level_preview.main, ["check", self.write_level(text), "--legend", EXAMPLE_LEGEND] + list(extra))

    def test_example_level_passes(self):
        code, output = run_quietly(level_preview.main, ["check", EXAMPLE_LEVEL, "--legend", EXAMPLE_LEGEND])
        self.assertEqual(code, 0, output)

    def test_unreachable_exit_is_an_error(self):
        code, output = self.check("#####\n#P#E#\n#####\n")
        self.assertEqual(code, 1)
        self.assertIn("exit (3, 1) is unreachable", output)

    def test_unreachable_pickup_is_only_a_warning(self):
        code, output = self.check("######\n#PE#*#\n######\n")
        self.assertEqual(code, 0)
        self.assertIn("warning:", output)

    def test_grid_shape_symbols_and_markers(self):
        code, output = self.check("####\n#P.\n####\n")
        self.assertEqual(code, 1)
        self.assertIn("rows have different lengths", output)
        code, output = self.check("####\n#PQ#\n####\n")
        self.assertIn("symbols not in legend: 'Q'", output)
        code, output = self.check("####\n#..#\n####\n")
        self.assertIn("marker spawn: found 0", output)
        self.assertIn("marker exit: found 0", output)

    def test_platformer_skips_reachability_and_says_so(self):
        code, output = self.check("; mode: platformer\n#####\n#P#E#\n#####\n")
        self.assertEqual(code, 0)
        self.assertIn("reachability is not checked", output)

    def test_render_paints_tiles_by_legend(self):
        out = os.path.join(self.directory, "L00.png")
        code, _ = run_quietly(level_preview.main, ["render", EXAMPLE_LEVEL, "--legend", EXAMPLE_LEGEND, "--out", out])
        self.assertEqual(code, 0)
        width, height, rgba = pngio.read_png(out)
        self.assertEqual((width, height), (160, 96))
        spawn_center = ((1 * 16 + 8) * width + 1 * 16 + 8) * 4
        self.assertEqual(tuple(rgba[spawn_center:spawn_center + 4]), (0x38, 0xb7, 0x64, 255))

    def test_render_uses_tile_image_when_present(self):
        tile_path = os.path.join(self.directory, "wall.png")
        pngio.write_png(tile_path, 2, 2, bytes((200, 10, 10, 255)) * 4)
        legend_path = os.path.join(self.directory, "legend.json")
        with open(legend_path, "w", encoding="utf-8") as legend_file:
            json.dump({"tile_size": 8, "tiles": {"#": {"name": "wall", "solid": True, "image": "wall.png"}}}, legend_file)
        out = os.path.join(self.directory, "tiles.png")
        code, _ = run_quietly(level_preview.main, ["render", self.write_level("##\n"), "--legend", legend_path, "--out", out])
        self.assertEqual(code, 0)
        width, _, rgba = pngio.read_png(out)
        self.assertEqual(width, 16)
        self.assertEqual(tuple(rgba[(4 * 16 + 12) * 4:(4 * 16 + 12) * 4 + 4]), (200, 10, 10, 255))

    def test_export_lists_markers_and_tiles(self):
        out = os.path.join(self.directory, "L00.json")
        code, _ = run_quietly(level_preview.main, ["export", EXAMPLE_LEVEL, "--legend", EXAMPLE_LEGEND, "--out", out])
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as export_file:
            exported = json.load(export_file)
        self.assertEqual((exported["width"], exported["height"], exported["mode"]), (10, 6, "topdown"))
        self.assertEqual(exported["markers"]["spawn"], [[1, 1]])
        self.assertEqual(exported["markers"]["exit"], [[8, 1]])
        self.assertEqual(exported["tiles"]["#"], "wall")
        self.assertEqual(exported["title"], "Example room with an exit")

    def test_marker_count_rules(self):
        self.assertTrue(level_preview.count_matches("1+", 3))
        self.assertFalse(level_preview.count_matches("1", 2))
        self.assertTrue(level_preview.count_matches("0-2", 2))


if __name__ == "__main__":
    unittest.main()
