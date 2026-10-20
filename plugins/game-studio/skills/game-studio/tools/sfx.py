#!/usr/bin/env python3
"""sfxr-style procedural SFX: JSON parameters → 44.1 kHz 16-bit mono WAV. Also analyzes WAV files."""

import argparse
import json
import math
import os
import random
import struct
import sys
import wave

SAMPLE_RATE = 44100
PEAK_LIMIT = 0.95
SILENCE_DBFS = -60.0

PRESETS = {
    "jump": {"wave": "square", "duration": 0.25, "freq": 300, "freq_end": 720, "duty": 0.5,
             "attack": 0.005, "decay": 0.06, "sustain": 0.55, "release": 0.1, "volume": 0.6},
    "coin": {"wave": "square", "duration": 0.3, "freq": 988, "duty": 0.5,
             "arpeggio": [{"at": 0.07, "mult": 1.335}],
             "attack": 0.002, "decay": 0.05, "sustain": 0.5, "release": 0.15, "volume": 0.5},
    "laser": {"wave": "saw", "duration": 0.22, "freq": 1400, "freq_end": 180,
              "attack": 0.002, "decay": 0.05, "sustain": 0.4, "release": 0.1, "lowpass": 6000, "volume": 0.5},
    "explosion": {"wave": "noise", "duration": 0.7, "freq": 900, "freq_end": 120,
                  "attack": 0.004, "decay": 0.2, "sustain": 0.35, "release": 0.35, "lowpass": 2500,
                  "volume": 0.8, "seed": 7},
    "hit": {"wave": "noise", "duration": 0.14, "freq": 2400, "freq_end": 600,
            "attack": 0.001, "decay": 0.04, "sustain": 0.3, "release": 0.08, "lowpass": 5000,
            "volume": 0.7, "seed": 3},
    "powerup": {"wave": "square", "duration": 0.55, "freq": 330, "freq_end": 880, "duty": 0.35,
                "vibrato": {"depth": 0.04, "rate": 14},
                "attack": 0.01, "decay": 0.1, "sustain": 0.6, "release": 0.2, "volume": 0.5},
    "click": {"wave": "sine", "duration": 0.035, "freq": 1600, "freq_end": 1000,
              "attack": 0.001, "decay": 0.01, "sustain": 0.4, "release": 0.015, "volume": 0.6},
    "blip": {"wave": "square", "duration": 0.08, "freq": 880, "duty": 0.25,
             "attack": 0.002, "decay": 0.02, "sustain": 0.5, "release": 0.03, "volume": 0.45},
}

DEFAULTS = {"wave": "square", "duration": 0.3, "freq": 440, "freq_end": None, "duty": 0.5,
            "attack": 0.01, "decay": 0.05, "sustain": 0.6, "release": 0.1, "vibrato": None,
            "arpeggio": [], "lowpass": 0, "volume": 0.6, "seed": 1}
WAVES = ("square", "saw", "triangle", "sine", "noise")


def resolve(params):
    merged = dict(DEFAULTS)
    if params.get("preset"):
        if params["preset"] not in PRESETS:
            raise ValueError("no preset %r, available: %s" % (params["preset"], ", ".join(sorted(PRESETS))))
        merged.update(PRESETS[params["preset"]])
    merged.update({key: value for key, value in params.items() if key != "preset"})
    if merged["wave"] not in WAVES:
        raise ValueError("wave must be one of %s" % ", ".join(WAVES))
    if merged["duration"] <= 0 or merged["freq"] <= 0:
        raise ValueError("duration and freq must be > 0")
    if merged["freq_end"] is not None and merged["freq_end"] <= 0:
        raise ValueError("freq_end must be > 0")
    return merged


def envelope(time, params):
    duration = params["duration"]
    attack = max(params["attack"], 0.0)
    decay = max(params["decay"], 0.0)
    release = max(params["release"], 0.0)
    sustain = min(max(params["sustain"], 0.0), 1.0)
    stages = attack + decay + release
    if stages > duration:
        squeeze = duration / stages
        attack, decay, release = attack * squeeze, decay * squeeze, release * squeeze
    release_start = duration - release
    if time < attack:
        return time / attack if attack else 1.0
    if time < attack + decay:
        return 1.0 - (1.0 - sustain) * (time - attack) / decay if decay else sustain
    if time < release_start:
        return sustain
    return sustain * max(0.0, (duration - time) / release) if release else 0.0


def frequency_at(time, params):
    start = params["freq"]
    end = params["freq_end"] or start
    progress = time / params["duration"]
    # An exponential sweep sounds even in pitch; a linear one rushes through the low end.
    frequency = start * (end / start) ** progress
    for step in sorted(params["arpeggio"] or [], key=lambda item: item["at"]):
        if time >= step["at"]:
            frequency = frequency * step["mult"]
    vibrato = params["vibrato"]
    if vibrato:
        frequency *= 1.0 + vibrato.get("depth", 0) * math.sin(2 * math.pi * vibrato.get("rate", 0) * time)
    return frequency


def synthesize(params):
    params = resolve(params)
    generator = random.Random(params["seed"])
    total = int(params["duration"] * SAMPLE_RATE)
    phase = 0.0
    noise_value = generator.uniform(-1, 1)
    lowpass_alpha = 1 - math.exp(-2 * math.pi * params["lowpass"] / SAMPLE_RATE) if params["lowpass"] else None
    filtered = 0.0
    samples = []
    for index in range(total):
        time = index / SAMPLE_RATE
        phase += frequency_at(time, params) / SAMPLE_RATE
        if phase >= 1.0:
            phase -= math.floor(phase)
            noise_value = generator.uniform(-1, 1)
        kind = params["wave"]
        if kind == "square":
            value = 1.0 if phase < params["duty"] else -1.0
        elif kind == "saw":
            value = 2.0 * phase - 1.0
        elif kind == "triangle":
            value = 4.0 * phase - 1.0 if phase < 0.5 else 3.0 - 4.0 * phase
        elif kind == "sine":
            value = math.sin(2 * math.pi * phase)
        else:
            value = noise_value
        if lowpass_alpha is not None:
            filtered += lowpass_alpha * (value - filtered)
            value = filtered
        samples.append(value * envelope(time, params) * params["volume"])
    return limit(samples)


def limit(samples, peak_limit=PEAK_LIMIT):
    peak = max((abs(value) for value in samples), default=0.0)
    if peak > peak_limit:
        factor = peak_limit / peak
        samples = [value * factor for value in samples]
    return samples


def write_wav(path, samples):
    with wave.open(path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(b"".join(
            struct.pack("<h", int(round(max(-1.0, min(1.0, value)) * 32767))) for value in samples))


def read_wav_samples(path):
    with wave.open(path, "rb") as wav_file:
        channels = wav_file.getnchannels()
        width = wav_file.getsampwidth()
        rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())
    if width == 1:
        values = [byte - 128 for byte in frames]
        full_scale = 128
    elif width == 2:
        values = list(struct.unpack("<%dh" % (len(frames) // 2), frames))
        full_scale = 32768
    elif width == 3:
        values = [int.from_bytes(frames[i:i + 3], "little", signed=True) for i in range(0, len(frames), 3)]
        full_scale = 8388608
    elif width == 4:
        values = list(struct.unpack("<%di" % (len(frames) // 4), frames))
        full_scale = 2147483648
    else:
        raise ValueError("unsupported sample width: %d bytes" % width)
    return values, full_scale, rate, channels


def to_dbfs(value):
    return round(20 * math.log10(value), 2) if value > 0 else float("-inf")


def analyze(path):
    values, full_scale, rate, channels = read_wav_samples(path)
    frame_count = len(values) // channels if channels else 0
    report = {"file": path, "duration": round(frame_count / rate, 4) if rate else 0.0,
              "sample_rate": rate, "channels": channels}
    if not values:
        report.update({"peak_dbfs": None, "rms_dbfs": None, "clipped": 0,
                       "silence_start": 0.0, "silence_end": 0.0, "problems": ["empty file"]})
        return report
    peak = max(abs(value) for value in values) / full_scale
    rms = math.sqrt(sum(value * value for value in values) / len(values)) / full_scale
    clip_level = full_scale - 1
    clipped = sum(1 for value in values if value >= clip_level or value <= -full_scale)
    threshold = full_scale * 10 ** (SILENCE_DBFS / 20)
    head = next((index for index, value in enumerate(values) if abs(value) > threshold), len(values))
    tail = next((index for index, value in enumerate(reversed(values)) if abs(value) > threshold), len(values))
    problems = []
    if clipped:
        problems.append("clipping: %d samples" % clipped)
    if peak == 0:
        problems.append("complete silence")
    report.update({
        "peak_dbfs": to_dbfs(peak),
        "rms_dbfs": to_dbfs(rms),
        "clipped": clipped,
        "silence_start": round(head / channels / rate, 4),
        "silence_end": round(tail / channels / rate, 4),
        "problems": problems,
    })
    return report


def print_reports(reports, as_json):
    if as_json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return
    for report in reports:
        if report["peak_dbfs"] is None:
            print("%s: %s" % (report["file"], "; ".join(report["problems"])))
            continue
        status = "OK" if not report["problems"] else "PROBLEM: " + "; ".join(report["problems"])
        print("%s: %.3f s, peak %s dBFS, RMS %s dBFS, clipped %d, leading silence %.3f s, trailing %.3f s — %s" % (
            report["file"], report["duration"], report["peak_dbfs"], report["rms_dbfs"], report["clipped"],
            report["silence_start"], report["silence_end"], status))


def analyze_command(paths, as_json):
    reports = []
    for path in paths:
        try:
            reports.append(analyze(path))
        except (wave.Error, EOFError, ValueError) as error:
            reports.append({"file": path, "peak_dbfs": None, "problems": ["unreadable: %s" % error]})
    print_reports(reports, as_json)
    return 1 if any(report["problems"] for report in reports) else 0


def render_command(path, out):
    with open(path, encoding="utf-8") as preset_file:
        data = json.load(preset_file)
    if "sounds" in data:
        os.makedirs(out, exist_ok=True)
        written = []
        for name, params in data["sounds"].items():
            target = os.path.join(out, name + ".wav")
            write_wav(target, synthesize(params))
            written.append(target)
        print("\n".join(written))
        return 0
    if os.path.isdir(out):
        out = os.path.join(out, os.path.splitext(os.path.basename(path))[0] + ".wav")
    write_wav(out, synthesize(data))
    print(out)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Procedural sound effects: JSON → WAV, WAV analysis.")
    commands = parser.add_subparsers(dest="command", required=True)

    render_parser = commands.add_parser("render", help="synthesize a sound or a sound pack")
    render_parser.add_argument("file", help="JSON parameters or a pack {\"sounds\": {...}}")
    render_parser.add_argument("--out", required=True, help="WAV for a single sound, directory for a pack")

    preset_parser = commands.add_parser("preset", help="print a built-in preset as JSON")
    preset_parser.add_argument("name", help="preset name (see list)")

    commands.add_parser("list", help="list built-in presets")

    analyze_parser = commands.add_parser("analyze", help="duration, peak, RMS, clipping, silence")
    analyze_parser.add_argument("wavs", nargs="+", help="WAV files")
    analyze_parser.add_argument("--json", action="store_true", help="print JSON")

    args = parser.parse_args(argv)
    if args.command == "list":
        print("\n".join(sorted(PRESETS)))
        return 0
    if args.command == "preset":
        if args.name not in PRESETS:
            print("no preset %r, available: %s" % (args.name, ", ".join(sorted(PRESETS))), file=sys.stderr)
            return 1
        print(json.dumps(PRESETS[args.name], ensure_ascii=False, indent=2))
        return 0
    if args.command == "analyze":
        return analyze_command(args.wavs, args.json)
    try:
        return render_command(args.file, args.out)
    except ValueError as error:
        print("bad parameters: %s" % error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
