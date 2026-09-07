"""Split every section recording that has arrived, in one pass.

Figures out which section each file is by the number she wrote at the front of
the name — that number is the section number printed in the booklet, so
"11_OBJECTION_CALL.m4a" is section 11, objection. Case, spaces and wording
after the number are ignored, because they will never match exactly.

    ./venv/bin/python scripts/split_all.py            # report only
    ./venv/bin/python scripts/split_all.py --write    # save the good ones
    ./venv/bin/python scripts/split_all.py --write --skip 11=4,23=7,9

A section whose segment count does not match the script is reported and left
alone; nothing is written for it until you resolve it with --skip.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


splitter = _load("splitter", "scripts/split_session_recording.py")
builder = _load("builder", "scripts/build_voice_script.py")

AUDIO = {".wav", ".m4a", ".mp3", ".flac", ".aac", ".ogg", ".mp4"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="voice_data/raw")
    ap.add_argument("--out", default="voice_data/wavs")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--skip", default="",
                    help="bad takes per section, e.g. 11=4,23=7,9")
    args = ap.parse_args()

    # --skip 11=4,23=7,9  ->  {11: {4}, 23: {7, 9}}
    skips, current = {}, None
    for part in args.skip.replace(" ", "").split(","):
        if not part:
            continue
        if "=" in part:
            sec, val = part.split("=", 1)
            current = int(sec)
            skips.setdefault(current, set()).add(int(val))
        elif current is not None:
            skips[current].add(int(part))

    sections = list(builder.by_section(builder.load()).keys())
    raw = Path(args.raw)
    files = sorted(p for p in raw.iterdir()
                   if p.suffix.lower() in AUDIO and not p.stem.endswith("_converted"))

    print(f"\n{len(files)} recordings in {raw}\n")
    print(f"  {'file':30s} {'section':13s} {'found':>6s} {'want':>5s}  status")
    print(f"  {'-'*30} {'-'*13} {'-'*6} {'-'*5}  {'-'*28}")

    written = problems = 0
    for f in files:
        m = re.match(r"(\d+)", f.name)
        if not m or not (1 <= int(m.group(1)) <= len(sections)):
            print(f"  {f.name[:30]:30s} {'?':13s} {'':>6s} {'':>5s}  no section number in the name")
            problems += 1
            continue
        num = int(m.group(1))
        section = sections[num - 1]
        rows = splitter.sentences(section)

        try:
            x, sr = sf.read(f, dtype="float64", always_2d=True)
        except Exception:
            converted = splitter._to_wav(f)
            if converted is None:
                print(f"  {f.name[:30]:30s} {section:13s} {'':>6s} {'':>5s}  cannot read this file")
                problems += 1
                continue
            x, sr = sf.read(converted, dtype="float64", always_2d=True)
            converted.unlink(missing_ok=True)
        x = x.mean(axis=1)

        segs = splitter.find_segments(x, sr)
        drop = skips.get(num, set())
        kept = [s for i, s in enumerate(segs, 1) if i not in drop]
        note = f" (-{len(drop)})" if drop else ""

        # Silence alone rarely lands on the right count once the gaps get
        # short. Fall back to fitting the script's expected durations.
        if len(kept) != len(rows) and not drop:
            expected = [len(r["text"]) / 11.0 for r in rows]
            aligned = splitter.align_to_script(x, sr, expected)
            if aligned:
                kept, note = aligned, " aligned"

        if len(kept) != len(rows):
            diff = len(kept) - len(rows)
            print(f"  {f.name[:30]:30s} {section:13s} {len(segs):>6d}{note} {len(rows):>5d}  "
                  f"MISMATCH {diff:+d} — inspect")
            problems += 1
            continue

        if args.write:
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            for (a, b), row in zip(kept, rows):
                lo = max(0, int((a - splitter.KEEP) * sr))
                hi = min(len(x), int((b + splitter.KEEP) * sr))
                sf.write(out / f"{row['id']}.wav", x[lo:hi], sr, subtype="PCM_24")
            written += len(kept)
            print(f"  {f.name[:30]:30s} {section:13s} {len(segs):>6d}{note} {len(rows):>5d}  written")
        else:
            print(f"  {f.name[:30]:30s} {section:13s} {len(segs):>6d}{note} {len(rows):>5d}  ok")

    print()
    if problems:
        print(f"  {problems} section(s) need attention. Inspect one with:")
        print(f"    ./venv/bin/python scripts/split_session_recording.py "
              f"<file> --section <name>")
    if args.write:
        print(f"  {written} clips written to {args.out}")
    else:
        print("  Nothing written. Re-run with --write once the mismatches are resolved.")
    print()
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
