"""Turn a real ambience recording into a call-ready bed.

    ./venv/bin/python scripts/import_ambience.py office /path/to/recording.mp3

Writes app/ambience/<name>.wav — 8 kHz mono, 20 s, seamlessly looping, at our
standard level. Run it locally and commit the result: the VPS then needs no
audio tooling and plays exactly what you approved.

The two beds shipped with the repo came from archive.org recordings marked
Public Domain (CC PD Mark 1.0), so they carry no attribution requirement:
  office — archive.org/details/aporee_18927_21953
  street — archive.org/details/aporee_15453_17996
"""

import sys
import wave
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import ambient  # noqa: E402

if len(sys.argv) != 3:
    sys.exit(__doc__)
name, src = sys.argv[1], sys.argv[2]

x, sr = sf.read(src, dtype="float64")
bed = ambient.prepare_bed(x, sr)
out = ambient.RECORDED_DIR / f"{name}.wav"
out.parent.mkdir(exist_ok=True)
with wave.open(str(out), "wb") as wf:
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(ambient.SR)
    wf.writeframes(bed.tobytes())

seam = abs(float(bed[0]) - float(bed[-1])) / (np.abs(np.diff(bed.astype(float))).mean() + 1e-9)
print(f"{out}  {len(bed)/ambient.SR:.0f}s  rms {np.sqrt((bed.astype(float)**2).mean()):.0f}  "
      f"loop seam {seam:.2f}x a normal step")
