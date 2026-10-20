#!/usr/bin/env python3
"""Chiptune from a text score *.score → WAV loop."""

import argparse
import math
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sfx  # noqa: E402

SAMPLE_RATE = sfx.SAMPLE_RATE
TARGET_PEAK = 10 ** (-1 / 20)
NON_LOOP_TAIL = 0.25
WAVES = ("square", "saw", "triangle", "sine", "noise")
SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
TRACK_LINE = re.compile(r"^track\s+(\S+)\s+(\w+)((?:\s+\w+=\S+)*)\s*:\s*(.*)$")
NOTE_TOKEN = re.compile(r"^(?:(?P<rest>R)|(?P<hit>x)|(?P<name>[A-G])(?P<accidental>#|b)?(?P<octave>-?\d))"
                        r"/(?P<division>\d+)(?P<dot>\.)?$")


class ScoreError(Exception):
    pass


def note_frequency(name, accidental, octave):
    semitone = SEMITONES[name] + (1 if accidental == "#" else -1 if accidental == "b" else 0)
    midi = (octave + 1) * 12 + semitone
    return 440.0 * 2 ** ((midi - 69) / 12)


def parse_notes(text, line_number):
    notes = []
    for token in text.split():
        if token == "|":
            continue
        match = NOTE_TOKEN.match(token)
        if not match:
            raise ScoreError("line %d: cannot parse note %r (examples: C#5/8, Eb4/4., R/4, x/8)" % (line_number, token))
        beats = 4.0 / int(match.group("division"))
        if match.group("dot"):
            beats *= 1.5
        if match.group("rest"):
            notes.append((None, beats))
        elif match.group("hit"):
            notes.append(("hit", beats))
        else:
            notes.append((note_frequency(match.group("name"), match.group("accidental"),
                                         int(match.group("octave"))), beats))
    return notes


def parse_score(text):
    score = {"bpm": 120.0, "loop": False, "tracks": {}}
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("bpm:"):
            score["bpm"] = float(line.split(":", 1)[1])
            continue
        if line.startswith("loop:"):
            score["loop"] = line.split(":", 1)[1].strip().lower() in ("true", "yes", "1")
            continue
        match = TRACK_LINE.match(line)
        if not match:
            raise ScoreError("line %d: expected bpm:, loop: or track …: %r" % (line_number, line))
        name, wave_kind, options_text, notes_text = match.groups()
        if wave_kind not in WAVES:
            raise ScoreError("line %d: wave %r, expected one of %s" % (line_number, wave_kind, ", ".join(WAVES)))
        options = dict(option.split("=", 1) for option in options_text.split())
        track = score["tracks"].setdefault(name, {
            "wave": wave_kind,
            "volume": float(options.get("vol", 0.4)),
            "duty": float(options.get("duty", 0.5)),
            "envelope": options.get("env", "pluck"),
            "notes": [],
        })
        if track["envelope"] not in ("pluck", "sustain"):
            raise ScoreError("line %d: env must be pluck or sustain" % line_number)
        track["notes"].extend(parse_notes(notes_text, line_number))
    if not score["tracks"]:
        raise ScoreError("the score has no track lines")
    return score


def oscillator(kind, phase, duty, generator):
    if kind == "square":
        return 1.0 if phase < duty else -1.0
    if kind == "saw":
        return 2.0 * phase - 1.0
    if kind == "triangle":
        return 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
    if kind == "sine":
        return math.sin(2 * math.pi * phase)
    return generator.uniform(-1, 1)


def note_envelope(position, length, envelope_kind):
    attack = min(0.005, length / 4)
    # A short fade at the end of every note removes clicks between notes.
    release = min(0.02, length / 4)
    if position < attack:
        level = position / attack
    elif envelope_kind == "pluck":
        level = math.exp(-5.0 * (position - attack) / max(length, 1e-6))
    else:
        level = 0.8
    if position > length - release:
        level *= max(0.0, (length - position) / release)
    return level


def render_track(track, seconds_per_beat, generator):
    samples = []
    for frequency, beats in track["notes"]:
        count = int(round(beats * seconds_per_beat * SAMPLE_RATE))
        if frequency is None:
            samples.extend([0.0] * count)
            continue
        length = count / SAMPLE_RATE
        phase = 0.0
        for index in range(count):
            position = index / SAMPLE_RATE
            if frequency == "hit":
                value = generator.uniform(-1, 1) * math.exp(-30.0 * position)
            else:
                phase = (phase + frequency / SAMPLE_RATE) % 1.0
                value = oscillator(track["wave"], phase, track["duty"], generator)
                value *= note_envelope(position, length, track["envelope"])
            samples.append(value * track["volume"])
    return samples


def render_score(score, seed=1):
    generator = random.Random(seed)
    seconds_per_beat = 60.0 / score["bpm"]
    rendered = [render_track(track, seconds_per_beat, generator) for track in score["tracks"].values()]
    total = max(len(samples) for samples in rendered)
    if not score["loop"]:
        total += int(NON_LOOP_TAIL * SAMPLE_RATE)
    mix = [0.0] * total
    for samples in rendered:
        for index, value in enumerate(samples):
            mix[index] += value
    peak = max((abs(value) for value in mix), default=0.0)
    if peak > 0:
        factor = TARGET_PEAK / peak
        mix = [value * factor for value in mix]
    return mix


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chiptune: *.score → WAV, WAV analysis.")
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render", help="render a score to WAV")
    render_parser.add_argument("score", help="path to *.score")
    render_parser.add_argument("--out", required=True, help="output WAV")
    render_parser.add_argument("--seed", type=int, default=1, help="random seed for drum noise")
    analyze_parser = commands.add_parser("analyze", help="analyze WAV, same as sfx.py")
    analyze_parser.add_argument("wavs", nargs="+", help="WAV files")
    analyze_parser.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return sfx.analyze_command(args.wavs, args.json)
    with open(args.score, encoding="utf-8") as score_file:
        text = score_file.read()
    try:
        score = parse_score(text)
    except (ScoreError, ValueError) as error:
        print("%s: %s" % (args.score, error), file=sys.stderr)
        return 1
    samples = render_score(score, args.seed)
    sfx.write_wav(args.out, samples)
    print("%s (%.2f s, %d track(s), loop=%s)" % (
        args.out, len(samples) / SAMPLE_RATE, len(score["tracks"]), "yes" if score["loop"] else "no"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
