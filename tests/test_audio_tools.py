import contextlib
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave

SKILL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "plugins", "game-studio", "skills", "game-studio")
TOOLS = os.path.join(SKILL_DIR, "tools")
TEMPLATES = os.path.join(SKILL_DIR, "templates")
sys.path.insert(0, TOOLS)

import chiptune  # noqa: E402
import sfx  # noqa: E402

EXAMPLE_PACK = os.path.join(TEMPLATES, "Audio", "sfx", "_example.json")
EXAMPLE_SCORE = os.path.join(TEMPLATES, "Audio", "music", "_example.score")


def run_quietly(main, argv):
    output = io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        code = main(argv)
    return code, output.getvalue()


def frame_count(path):
    with wave.open(path, "rb") as wav_file:
        return wav_file.getnframes(), wav_file.getframerate(), wav_file.getnchannels(), wav_file.getsampwidth()


class SfxTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def write_json(self, name, data):
        path = os.path.join(self.directory, name)
        with open(path, "w", encoding="utf-8") as json_file:
            json.dump(data, json_file)
        return path

    def test_single_sound_has_exact_length_and_format(self):
        out = os.path.join(self.directory, "jump.wav")
        code, _ = run_quietly(sfx.main, ["render", self.write_json("jump.json", {"preset": "jump"}), "--out", out])
        self.assertEqual(code, 0)
        self.assertEqual(frame_count(out), (int(0.25 * 44100), 44100, 1, 2))
        report = sfx.analyze(out)
        self.assertEqual(report["problems"], [])
        self.assertLess(report["peak_dbfs"], 0)

    def test_every_preset_renders_without_clipping(self):
        for name in sfx.PRESETS:
            with self.subTest(preset=name):
                out = os.path.join(self.directory, name + ".wav")
                sfx.write_wav(out, sfx.synthesize({"preset": name}))
                self.assertEqual(sfx.analyze(out)["clipped"], 0)

    def test_loud_settings_are_limited_not_clipped(self):
        out = os.path.join(self.directory, "loud.wav")
        sfx.write_wav(out, sfx.synthesize({"wave": "square", "volume": 5.0, "duration": 0.05}))
        report = sfx.analyze(out)
        self.assertEqual(report["clipped"], 0)
        self.assertAlmostEqual(report["peak_dbfs"], -0.45, delta=0.1)

    def test_example_pack_renders_each_sound_into_directory(self):
        out_directory = os.path.join(self.directory, "pack")
        code, _ = run_quietly(sfx.main, ["render", EXAMPLE_PACK, "--out", out_directory])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(out_directory)), ["boom.wav", "pickup.wav", "shoot.wav"])
        self.assertEqual(frame_count(os.path.join(out_directory, "boom.wav"))[0], int(0.5 * 44100))
        code, _ = run_quietly(sfx.main, ["analyze"] + [os.path.join(out_directory, name) for name in os.listdir(out_directory)])
        self.assertEqual(code, 0)

    def test_analyze_flags_clipping(self):
        path = os.path.join(self.directory, "clipped.wav")
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(44100)
            wav_file.writeframes(struct.pack("<4h", 0, 32767, -32768, 100))
        code, output = run_quietly(sfx.main, ["analyze", path, "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)[0]["clipped"], 2)

    def test_analyze_flags_empty_file(self):
        path = os.path.join(self.directory, "empty.wav")
        sfx.write_wav(path, [])
        code, output = run_quietly(sfx.main, ["analyze", path])
        self.assertEqual(code, 1)
        self.assertIn("empty file", output)

    def test_unknown_preset_and_bad_params_fail(self):
        self.assertEqual(run_quietly(sfx.main, ["preset", "nope"])[0], 1)
        bad = self.write_json("bad.json", {"wave": "kazoo"})
        self.assertEqual(run_quietly(sfx.main, ["render", bad, "--out", os.path.join(self.directory, "bad.wav")])[0], 1)

    def test_preset_prints_reusable_json(self):
        code, output = run_quietly(sfx.main, ["preset", "coin"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output)["wave"], "square")


class ChiptuneTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_example_loop_is_exactly_two_bars(self):
        out = os.path.join(self.directory, "loop.wav")
        code, _ = run_quietly(chiptune.main, ["render", EXAMPLE_SCORE, "--out", out])
        self.assertEqual(code, 0)
        # 8 beats at 140 BPM is exactly 151200 samples; no tail because of loop: true.
        self.assertEqual(frame_count(out)[0], 151200)
        report = sfx.analyze(out)
        self.assertEqual(report["clipped"], 0)
        self.assertAlmostEqual(report["peak_dbfs"], -1.0, delta=0.1)

    def test_non_loop_score_gets_tail(self):
        score = chiptune.parse_score("bpm: 120\ntrack a sine: A4/4\n")
        self.assertEqual(len(chiptune.render_score(score)), 22050 + int(0.25 * 44100))

    def test_note_frequencies(self):
        self.assertAlmostEqual(chiptune.note_frequency("A", None, 4), 440.0)
        self.assertAlmostEqual(chiptune.note_frequency("C", "#", 5), 554.365, places=2)
        self.assertAlmostEqual(chiptune.note_frequency("E", "b", 4), 311.127, places=2)

    def test_dotted_notes_and_repeated_track_lines(self):
        score = chiptune.parse_score("track a square: C4/4.\ntrack a square: R/8 | x/4\n")
        self.assertEqual([beats for _, beats in score["tracks"]["a"]["notes"]], [1.5, 0.5, 1.0])

    def test_bad_note_is_reported_with_line(self):
        path = os.path.join(self.directory, "bad.score")
        with open(path, "w", encoding="utf-8") as score_file:
            score_file.write("bpm: 100\ntrack a square: C4/4 H9/3\n")
        code, output = run_quietly(chiptune.main, ["render", path, "--out", os.path.join(self.directory, "x.wav")])
        self.assertEqual(code, 1)
        self.assertIn("line 2", output)


if __name__ == "__main__":
    unittest.main()
