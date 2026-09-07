"""Cut one long section recording into one file per sentence.

Asking someone with no studio experience to create, stop, save and name 475
files is how a recording session falls apart. It is far easier for her to press
record once per section, read the sentences with a clear gap between them, and
stop. This does the rest: finds the gaps, cuts on them, and names each piece
after the sentence it belongs to.

    ./venv/bin/python scripts/split_session_recording.py 01_vowels.wav --section vowels
    ./venv/bin/python scripts/split_session_recording.py 01_vowels.wav --section vowels --write

Without --write it only reports, so a mismatch is caught before anything is
saved. If she re-read a line, there will be one segment too many; the report
shows exactly where, and --skip lets you drop the bad take.
"""

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
TSV = ROOT / "docs" / "voice_training" / "script_te.tsv"

GAP = 0.7          # silence this long or longer is treated as "next sentence"
KEEP = 0.25        # silence kept at each end of a cut piece
MIN_SPEECH = 0.4   # anything shorter is a cough, not a sentence


def sentences(section):
    with open(TSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f, delimiter="\t")
                if not section or r["section"] == section]
    if not rows:
        sys.exit(f"No sentences for section '{section}'. Sections are in {TSV.name}.")
    return rows


def find_segments(x, sr):
    """Stretches of speech, separated by gaps of at least GAP seconds."""
    win = max(1, int(sr * 0.02))
    frames = x[:len(x) // win * win].reshape(-1, win)
    level = np.sqrt((frames ** 2).mean(axis=1))
    # Threshold above the room, well below speech: the quiet 20% is the room.
    floor = float(np.percentile(level, 20))
    peak = float(np.percentile(level, 95))
    thresh = max(floor * 3.0, peak * 0.06, 1e-5)

    loud = level > thresh
    gap_frames = int(GAP / 0.02)
    segs, start, silence = [], None, 0
    for i, is_loud in enumerate(loud):
        if is_loud:
            if start is None:
                start = i
            silence = 0
        elif start is not None:
            silence += 1
            if silence >= gap_frames:
                segs.append((start * 0.02, (i - silence) * 0.02))
                start = None
    if start is not None:
        segs.append((start * 0.02, len(loud) * 0.02))
    return [(a, b) for a, b in segs if b - a >= MIN_SPEECH]


def align_to_script(x, sr, expected_secs, min_pause=0.12):
    """Cut into exactly len(expected_secs) pieces, using the script as a guide.

    Silence alone stops working once the speaker pauses for 0.4s between
    sentences and 0.4s for breath inside them — no single threshold separates
    those, and for one section no threshold gave the right count at all.

    But we know how long each sentence *should* take (characters / 11). So:
    take every pause as a candidate boundary, then choose the combination that
    best matches the expected run of durations. Dynamic programming, so it is
    the globally best fit rather than a greedy walk.
    """
    win = max(1, int(sr * 0.02))
    frames = x[:len(x) // win * win].reshape(-1, win)
    level = np.sqrt((frames ** 2).mean(axis=1))
    thresh = max(float(np.percentile(level, 20)) * 3.0,
                 float(np.percentile(level, 95)) * 0.06, 1e-5)
    loud = level > thresh
    step = win / sr

    # Speech time between two frames, in O(1).
    cum = np.concatenate([[0.0], np.cumsum(loud) * step])
    speech = lambda i, j: cum[j] - cum[i]

    # Candidate boundaries: the middle of every pause long enough to be one.
    # Remember how long each pause was — a long pause is far more likely to be
    # a sentence end than a breath, and ignoring that was what put the first
    # attempt one sentence out of step.
    cands, pause_len, run = [0], [99.0], 0
    for i, is_loud in enumerate(loud):
        if not is_loud:
            run += 1
        else:
            if run * step >= min_pause:
                cands.append(i - run // 2)
                pause_len.append(run * step)
            run = 0
    cands.append(len(loud))
    pause_len.append(99.0)

    n, m = len(expected_secs), len(cands)
    if m < n + 1:
        return None                      # fewer pauses than sentences: unsalvageable

    # Her real speaking rate, not an assumed one: total speech over total text.
    total_speech = speech(0, len(loud))
    scale = total_speech / max(sum(expected_secs), 1e-6)
    want_secs = [e * scale for e in expected_secs]

    # A pause at the 80th percentile or longer is "clearly a sentence end".
    real = [p for p in pause_len if p < 99.0] or [min_pause]
    long_pause = float(np.percentile(real, 80))
    pause_bonus = [0.0 if p >= long_pause else 0.6 * (1 - p / long_pause)
                   for p in pause_len]

    INF = float("inf")
    cost = np.full((n + 1, m), INF)
    back = np.zeros((n + 1, m), dtype=int)
    cost[0][0] = 0.0
    for k in range(1, n + 1):
        want = want_secs[k - 1]
        for j in range(k, m):
            best, arg = INF, -1
            # cutting at j costs more when j is a short pause
            penalty = pause_bonus[j] if j < m - 1 else 0.0
            for i in range(k - 1, j):
                if cost[k - 1][i] == INF:
                    continue
                got = speech(cands[i], cands[j])
                if got < 0.15:           # a piece with no speech in it
                    continue
                c = cost[k - 1][i] + abs(got - want) / want + penalty
                if c < best:
                    best, arg = c, i
            cost[k][j] = best
            back[k][j] = arg

    end = int(np.argmin(cost[n]))
    if cost[n][end] == INF:
        return None
    bounds, k = [end], n
    while k > 0:
        end = back[k][end]
        bounds.append(end)
        k -= 1
    bounds.reverse()

    # Trim each piece to where speech actually starts and stops inside it.
    out = []
    for a, b in zip(bounds, bounds[1:]):
        lo, hi = cands[a], cands[b]
        idx = np.nonzero(loud[lo:hi])[0]
        if len(idx) == 0:
            return None
        out.append(((lo + idx[0]) * step, (lo + idx[-1] + 1) * step))
    return out


def _to_wav(src: Path):
    """Convert a phone recording to WAV next to it. Returns the new path."""
    import shutil as _sh
    import subprocess
    out = src.with_name(src.stem + "_converted.wav")
    if _sh.which("afconvert"):                     # built into macOS
        cmd = ["afconvert", "-f", "WAVE", "-d", "LEI16", str(src), str(out)]
    elif _sh.which("ffmpeg"):
        cmd = ["ffmpeg", "-y", "-i", str(src), "-c:a", "pcm_s16le", str(out)]
    else:
        return None
    if subprocess.run(cmd, capture_output=True).returncode != 0 or not out.exists():
        return None
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("recording")
    ap.add_argument("--section", required=True, help="which section she read, e.g. vowels")
    ap.add_argument("--out", default=None, help="where to write pieces (default: wavs/)")
    ap.add_argument("--write", action="store_true", help="actually save the pieces")
    ap.add_argument("--skip", default="", help="1-based segment numbers to discard, "
                                               "e.g. 4,5 for two bad takes")
    args = ap.parse_args()

    rows = sentences(args.section)

    recording = Path(args.recording)
    if not recording.exists():
        near = sorted(p.name for p in recording.parent.glob("*")
                      if p.suffix.lower() in (".wav", ".m4a", ".mp3", ".flac", ".aac"))
        msg = [f"Cannot find '{recording}'.", ""]
        if near:
            msg += ["Audio files in that folder:"] + [f"    {n}" for n in near[:10]]
        else:
            msg += [f"There are no audio files in {recording.parent.resolve()}.",
                    "",
                    "This step comes AFTER she records. Give her",
                    "docs/voice_training/script_te.html first; when a section",
                    "recording comes back, save it as 01_vowels.wav and run this",
                    "again with the path to it."]
        sys.exit("\n".join(msg))

    try:
        x, sr = sf.read(recording, dtype="float64", always_2d=True)
    except Exception as first_error:
        # Phones hand back .m4a/.aac, which libsndfile cannot open. Converting
        # is lossless-enough and beats telling her to record it all again.
        converted = _to_wav(recording)
        if converted is None:
            sys.exit(f"Cannot read '{recording}': {first_error}\n"
                     f"WAV, FLAC, OGG and MP3 work directly. This looks like an "
                     f"m4a/aac and no converter (afconvert or ffmpeg) was found.")
        print(f"  converted {recording.suffix} -> wav for reading")
        x, sr = sf.read(converted, dtype="float64", always_2d=True)
    x = x.mean(axis=1)

    segs = find_segments(x, sr)
    skip = {int(s) for s in args.skip.replace(" ", "").split(",") if s}
    kept = [s for i, s in enumerate(segs, 1) if i not in skip]

    print(f"\n{Path(args.recording).name}: {len(x)/sr/60:.1f} min")
    print(f"  found {len(segs)} spoken stretches"
          + (f", keeping {len(kept)} after --skip" if skip else ""))
    print(f"  section '{args.section}' expects {len(rows)} sentences")

    if len(kept) != len(rows):
        extra = len(kept) - len(rows)
        print(f"\n  MISMATCH: {abs(extra)} {'too many' if extra > 0 else 'missing'}.")
        if extra > 0:
            print("  Usually a re-read. Listen at these times and pass the bad "
                  "take numbers to --skip:")
        else:
            print("  Usually two sentences run together, or one was skipped. "
                  "Check around these times:")
        for i, (a, b) in enumerate(kept[:len(rows) + 4], 1):
            text = rows[i - 1]["text"][:44] if i <= len(rows) else "—"
            print(f"    {i:3d}  {a:6.1f}s – {b:5.1f}s   {text}")
        if not args.write:
            print("\n  Nothing written. Fix the mismatch, then run with --write.\n")
        return 1

    print("  segment count matches the script exactly\n")
    for (a, b), row in zip(kept, rows):
        spoken = b - a
        expected = len(row["text"]) / 11.0
        flag = "  ← check this one" if spoken < expected * 0.5 or spoken > expected * 2.2 else ""
        print(f"    {row['id']}  {spoken:4.1f}s (expected ~{expected:.1f}s){flag}")

    if args.write:
        out = Path(args.out or Path(args.recording).parent / "wavs")
        out.mkdir(parents=True, exist_ok=True)
        for (a, b), row in zip(kept, rows):
            lo = max(0, int((a - KEEP) * sr))
            hi = min(len(x), int((b + KEEP) * sr))
            sf.write(out / f"{row['id']}.wav", x[lo:hi], sr, subtype="PCM_24")
        print(f"\n  Wrote {len(kept)} files to {out}\n")
    else:
        print("\n  Looks right. Run again with --write to save the pieces.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
