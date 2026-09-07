"""Turn the verified clips into a dataset Piper can train on.

    ./venv/bin/python scripts/piper/prepare_dataset.py

Produces voice_data/piper/ — wavs at 16 kHz mono plus metadata.csv in the
LJSpeech format Piper expects (id|text). Zip that folder and upload it to Colab.

Three things happen to the audio and text on the way:

* **English is rewritten in Telugu script.** espeak-ng switches to English
  phonemes for Latin script — /pɹˈɒpəti/ — but she said ప్రాపర్టీ. Training on
  that mismatch would teach the wrong sounds on every code-mixed line.
* **16 kHz, mono.** Piper's `low` quality trains at 16 kHz. A phone call is
  8 kHz, so nothing above that survives anyway; a bigger model would only cost
  CPU on the VPS for fidelity no owner can hear.
* **Levels matched.** Peak-normalised per clip so the model is not also
  learning that some sentences were louder than others.
"""

import csv
import re
import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).parent))
from translit import MAP                                        # noqa: E402

TSV = ROOT / "docs" / "voice_training" / "script_te.tsv"
CLIPS = ROOT / "voice_data" / "wavs"
OUT = ROOT / "voice_data" / "piper"
TARGET_SR = 16000
PEAK = 0.95

_WORD = re.compile(r"[A-Za-z][A-Za-z\-]*")


def to_telugu(text):
    """Rewrite the English words we know; report any we do not."""
    unknown = []

    def swap(m):
        w = m.group(0)
        if w in MAP:
            return MAP[w]
        if w.lower() in MAP:
            return MAP[w.lower()]
        if w.capitalize() in MAP:
            return MAP[w.capitalize()]
        unknown.append(w)
        return w

    return _WORD.sub(swap, text), unknown


def resample(x, sr_in, sr_out):
    """Band-limited resample: truncating the spectrum is the anti-alias filter."""
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    X = np.fft.rfft(x)
    Y = np.zeros(n_out // 2 + 1, dtype=complex)
    keep = min(len(X), len(Y))
    Y[:keep] = X[:keep]
    return np.fft.irfft(Y, n_out) * (n_out / len(x))


def main():
    rows = {r["id"]: r for r in csv.DictReader(open(TSV, encoding="utf-8"),
                                               delimiter="\t")}
    clips = sorted(CLIPS.glob("*.wav"))
    if not clips:
        sys.exit(f"No clips in {CLIPS}. Run split_all.py --write then verify_clips.py.")

    wav_dir = OUT / "wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)
    for old in wav_dir.glob("*.wav"):
        old.unlink()

    lines, total, unknown_all, skipped = [], 0.0, {}, 0
    for clip in clips:
        row = rows.get(clip.stem)
        if row is None:
            skipped += 1
            continue

        x, sr = sf.read(clip, dtype="float64", always_2d=True)
        x = x.mean(axis=1)
        x = resample(x, sr, TARGET_SR)
        peak = np.abs(x).max()
        if peak > 0:
            x *= PEAK / peak
        pcm = np.clip(x * 32767, -32768, 32767).astype(np.int16)

        with wave.open(str(wav_dir / f"{clip.stem}.wav"), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(TARGET_SR)
            wf.writeframes(pcm.tobytes())

        text, unknown = to_telugu(row["text"])
        for w in unknown:
            unknown_all[w] = unknown_all.get(w, 0) + 1
        lines.append(f"{clip.stem}|{text}")
        total += len(pcm) / TARGET_SR

    (OUT / "metadata.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"\n  {len(lines)} clips -> {OUT}")
    print(f"  {total/60:.1f} minutes at {TARGET_SR} Hz mono")
    if skipped:
        print(f"  {skipped} clips skipped (no matching sentence in the script)")
    if unknown_all:
        print(f"\n  {len(unknown_all)} English words have no Telugu spelling yet — "
              f"espeak will pronounce these with an English accent:")
        for w, n in sorted(unknown_all.items(), key=lambda kv: -kv[1])[:20]:
            print(f"    {w}  ×{n}")
        print("  Add them to scripts/piper/translit.py and re-run.")
    else:
        print("\n  Every English word has a Telugu spelling.")
    print(f"\n  Next: zip {OUT.relative_to(ROOT)} and upload it to Colab.\n")


if __name__ == "__main__":
    main()
