"""Check a voice dataset before training on it.

Five days of training on a flawed dataset produces a flawed voice, and you only
find out at the end. This looks at every recording for the faults that actually
ruin a model — clipping, room noise, level drift between sessions, a line read
wrong — and refuses to write metadata until they are fixed.

    ./venv/bin/python scripts/check_recordings.py voice_data/wavs
    ./venv/bin/python scripts/check_recordings.py voice_data/wavs --write-metadata
"""

import argparse
import csv
import math
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "docs" / "voice_training" / "script_te.tsv"

MIN_RATE = 24000
CHARS_PER_SECOND = 11.0          # measured from Sarvam output at natural pace
GOOD_PEAK_DB = (-14.0, -2.0)     # too quiet loses detail; too loud clips
NOISE_FLOOR_DB = -45.0           # quietest moment should be at least this low
PAD_SECONDS = (0.05, 1.5)        # silence at each end


def db(x):
    return 20 * math.log10(max(float(x), 1e-9))


def load_script():
    with open(SCRIPT, encoding="utf-8") as f:
        return {r["id"]: r["text"] for r in csv.DictReader(f, delimiter="\t")}


def edges(x, sr, threshold):
    """Where speech starts and ends, in seconds."""
    win = max(1, int(sr * 0.01))
    frames = x[:len(x) // win * win].reshape(-1, win)
    loud = np.sqrt((frames ** 2).mean(axis=1)) > threshold
    if not loud.any():
        return None, None
    first, last = int(np.argmax(loud)), len(loud) - int(np.argmax(loud[::-1]))
    return first * 0.01, last * 0.01


def inspect(path, text):
    """Everything worth knowing about one recording."""
    x, sr = sf.read(path, dtype="float64", always_2d=True)
    issues, warnings = [], []
    channels = x.shape[1]
    x = x.mean(axis=1)

    if channels != 1:
        warnings.append(f"{channels} channels (mono expected; averaged for these checks)")
    if sr < MIN_RATE:
        issues.append(f"sample rate {sr} Hz — too low to train on, re-record")

    peak = float(np.abs(x).max()) if len(x) else 0.0
    clipped = int((np.abs(x) >= 0.999).sum())
    if clipped > 8:
        issues.append(f"clipped ({clipped} samples at full scale) — lower the gain and re-record")
    elif db(peak) > GOOD_PEAK_DB[1]:
        warnings.append(f"very hot ({db(peak):.1f} dBFS)")
    elif db(peak) < GOOD_PEAK_DB[0]:
        warnings.append(f"quiet ({db(peak):.1f} dBFS) — move closer or raise the gain")

    duration = len(x) / sr
    speech_start, speech_end = edges(x, sr, max(peak * 0.05, 1e-4))
    if speech_start is None:
        issues.append("silent file — nothing was recorded")
        return dict(path=path, sr=sr, duration=duration, rms=0.0, peak=peak,
                    noise=-99.0, issues=issues, warnings=warnings)

    # Room noise: the quietest tenth of a second anywhere in the file.
    win = max(1, int(sr * 0.1))
    frames = x[:len(x) // win * win].reshape(-1, win)
    noise = db(np.sqrt((frames ** 2).mean(axis=1)).min())
    if noise > NOISE_FLOOR_DB:
        warnings.append(f"background noise {noise:.0f} dBFS — fan, AC or traffic?")

    if speech_start < PAD_SECONDS[0]:
        warnings.append("starts abruptly — leave a moment of silence before speaking")
    if duration - speech_end < PAD_SECONDS[0]:
        warnings.append("cut off at the end — wait a moment before stopping")
    if speech_start > PAD_SECONDS[1] or duration - speech_end > PAD_SECONDS[1]:
        warnings.append("long silence at an end — trim it")

    spoken = speech_end - speech_start
    expected = len(text) / CHARS_PER_SECOND
    if spoken < expected * 0.5:
        issues.append(f"only {spoken:.1f}s for a line that needs ~{expected:.1f}s "
                      f"— truncated or the wrong line")
    elif spoken > expected * 2.2:
        warnings.append(f"{spoken:.1f}s for a ~{expected:.1f}s line — long pause or a stumble?")

    return dict(path=path, sr=sr, duration=duration, peak=peak, noise=noise,
                rms=float(np.sqrt((x ** 2).mean())), speech=spoken,
                issues=issues, warnings=warnings)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="directory of recordings named after script IDs")
    ap.add_argument("--write-metadata", action="store_true",
                    help="write metadata.csv (only if nothing is broken)")
    args = ap.parse_args()

    script = load_script()
    folder = Path(args.folder)
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in (".wav", ".flac") and p.stem in script)
    if not files:
        sys.exit(f"No recordings in {folder} whose names match IDs in {SCRIPT.name}. "
                 f"Files must be named A001.wav, A002.wav, …")

    results = [inspect(p, script[p.stem]) for p in files]

    broken = [r for r in results if r["issues"]]
    flagged = [r for r in results if r["warnings"] and not r["issues"]]
    total = sum(r["duration"] for r in results)

    print(f"\n{len(files)} recordings, {total/60:.1f} minutes")
    rates = Counter(r["sr"] for r in results)
    print(f"  sample rates: " + ", ".join(f"{k} Hz ×{v}" for k, v in rates.items()))
    if len(rates) > 1:
        print("  ⚠ mixed sample rates — resample everything to the highest before training")

    # Level drift is the fault people never notice: session 3 recorded closer to
    # the mic than session 1 teaches the model two different voices.
    levels = np.array([db(r["rms"]) for r in results if r["rms"] > 0])
    if len(levels):
        median = float(np.median(levels))
        drift = [r for r in results if r["rms"] > 0 and abs(db(r["rms"]) - median) > 6]
        print(f"  level: median {median:.1f} dBFS, spread "
              f"{levels.max()-levels.min():.1f} dB")
        if drift:
            print(f"  ⚠ {len(drift)} recordings more than 6 dB from the median "
                  f"— mic distance changed between takes")
            for r in drift[:5]:
                print(f"      {r['path'].name}  {db(r['rms']):.1f} dBFS")

    noises = [r["noise"] for r in results if r["noise"] > -99]
    if noises:
        print(f"  background noise: median {np.median(noises):.0f} dBFS, "
              f"worst {max(noises):.0f} dBFS")

    missing = [i for i in script if i not in {p.stem for p in files}]
    if missing:
        print(f"\n  {len(missing)} sentences not yet recorded: "
              f"{', '.join(missing[:12])}{' …' if len(missing) > 12 else ''}")

    if broken:
        print(f"\nMUST FIX — {len(broken)} recording(s):")
        for r in broken:
            print(f"  {r['path'].name}")
            for m in r["issues"]:
                print(f"      {m}")
    if flagged:
        print(f"\nWorth a look — {len(flagged)} recording(s):")
        for r in flagged[:20]:
            print(f"  {r['path'].name}: {'; '.join(r['warnings'])}")
        if len(flagged) > 20:
            print(f"  … and {len(flagged) - 20} more")

    if not broken and not flagged:
        print("\n  Nothing wrong found.")

    if args.write_metadata:
        if broken:
            sys.exit("\nRefusing to write metadata while recordings are broken. "
                     "Fix them and run again.")
        out = folder.parent / "metadata.csv"
        with open(out, "w", encoding="utf-8", newline="") as f:
            for r in results:
                f.write(f"{r['path'].stem}|{script[r['path'].stem]}\n")
        print(f"\n  Wrote {out} ({len(results)} lines, id|text)")

    print()
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
