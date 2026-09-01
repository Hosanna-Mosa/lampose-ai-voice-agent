"""Audio Check: turning a stereo recording into a readable chat transcript.

Covers the two ways it has actually failed:
  * a long unbroken stretch of speech exceeding Sarvam's 30s sync limit
    (ACVPS11: the agent talked 0:03–0:55 and the bubble showed an HTTP 400)
  * a channel that is never digitally silent, e.g. with background sound on

Run: PYTHONPATH=. ./venv/bin/python tests/test_audio_check.py
"""

import array
import glob
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import _speech_segments, _split_for_stt, _STT_MAX_SECS   # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


recs = sorted(glob.glob("recordings/*.wav"), key=os.path.getmtime)
if not recs:
    print("no recordings to test against"); sys.exit(0)
with wave.open(recs[-1]) as wf:
    sr = wf.getframerate()
    pcm = array.array("h", wf.readframes(wf.getnframes()))
agent = pcm[1::2]

print("\nLong stretches are split to fit the STT API")
parts = _split_for_stt(agent, sr, 0.0, 55.0)
check("no piece exceeds Sarvam's 30s limit",
      all(b - a <= 30.0 for a, b in parts),
      ", ".join(f"{b-a:.1f}s" for a, b in parts))
check("pieces are contiguous (nothing dropped)",
      all(abs(parts[i][1] - parts[i + 1][0]) < 1e-9 for i in range(len(parts) - 1)))
check("pieces cover the whole stretch",
      abs(parts[0][0]) < 1e-9 and abs(parts[-1][1] - 55.0) < 1e-9)

x = np.frombuffer(agent, dtype=np.int16).astype(np.float64)
speech_level = np.abs(x[x != 0]).mean()
for a, b in parts[:-1]:
    level = np.abs(x[int((b - 0.02) * sr):int(b * sr)]).mean()
    check(f"cut at {b:.1f}s falls in a pause, not mid-word",
          level < speech_level * 0.5, f"level {level:.0f} vs {speech_level:.0f} while speaking")

check("segments already short enough are left untouched",
      _split_for_stt(agent, sr, 3.0, 3.0 + _STT_MAX_SECS) == [(3.0, 3.0 + _STT_MAX_SECS)])
check("a segment just over the limit splits in two",
      len(_split_for_stt(agent, sr, 0.0, _STT_MAX_SECS + 2.0)) == 2)

print("\nEvery segment the segmenter emits is transcribable")
for label, ch in (("agent", agent), ("owner", pcm[0::2])):
    segs = _speech_segments(ch, sr)
    pieces = [p for a, b in segs for p in _split_for_stt(ch, sr, a, b)]
    check(f"{label}: {len(segs)} segments -> {len(pieces)} requests, all within the limit",
          all(b - a <= 30.0 for a, b in pieces),
          f"longest {max((b-a for a, b in pieces), default=0):.1f}s")

print(f"\n{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
