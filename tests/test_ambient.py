"""Ambient background sound: bed quality, mixer compatibility, and the two
things it could plausibly break — call setup and the Audio Check transcript.

Run: PYTHONPATH=. ./venv/bin/python tests/test_ambient.py
"""

import asyncio
import glob
import os
import sys
import wave

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ambient, config          # noqa: E402
from app.main import _speech_segments    # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def read_bed(name):
    with wave.open(str(ambient.bed_path(name))) as wf:
        meta = (wf.getnchannels(), wf.getsampwidth(), wf.getframerate())
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
    return meta, pcm


# --- A. the beds themselves --------------------------------------------------
print("\nA. Bed generation")
for name in ambient.available():
    meta, pcm = read_bed(name)
    x = pcm.astype(np.float64)
    rms = np.sqrt((x ** 2).mean())
    # A loop clicks when the jump from last sample to first is much larger than
    # a normal sample-to-sample step.
    seam = abs(x[0] - x[-1]) / (np.abs(np.diff(x)).mean() + 1e-9)
    check(f"{name}: 8kHz mono s16, {len(x)/8000:.0f}s", meta == (1, 2, 8000) and len(x) == 8000 * 20)
    check(f"{name}: normalised (rms {rms:.0f})", 1900 <= rms <= 2100)
    check(f"{name}: no clipping (peak {np.abs(x).max():.0f})", np.abs(x).max() < 32000)
    check(f"{name}: loop seam clean ({seam:.2f}x a normal step)", seam < 3.0)

print("\nA2. Real recordings are used in place of the synthesized fallback")
for name in ("office", "street"):
    check(f"{name}: ships as a real recording", ambient.is_recorded(name),
          ambient.describe(name))
    meta, pcm = read_bed(name)
    x = pcm.astype(np.float64)
    seam = abs(x[0] - x[-1]) / (np.abs(np.diff(x)).mean() + 1e-9)
    check(f"{name}: 8kHz mono 20s loop at our level", meta == (1, 2, 8000)
          and len(x) == 8000 * 20 and 1900 <= np.sqrt((x ** 2).mean()) <= 2100)
    check(f"{name}: crossfaded loop does not click ({seam:.2f}x)", seam < 3.0)

print("\nA3. Any recording can be turned into a bed (the upload path)")
rng = np.random.default_rng(7)
for sr_in, secs, label in ((44100, 40, "phone memo 44.1kHz"), (48000, 25, "48kHz stereo"),
                           (8000, 30, "already 8kHz")):
    raw = rng.standard_normal(sr_in * secs) * 0.05
    if label.endswith("stereo"):
        raw = np.stack([raw, raw], axis=1)
    bed = ambient.prepare_bed(raw, sr_in)
    seam = abs(float(bed[0]) - float(bed[-1])) / (np.abs(np.diff(bed.astype(float))).mean() + 1e-9)
    check(f"{label} -> 20s 8kHz loop, level matched, no click",
          len(bed) == 8000 * 20 and 1900 <= np.sqrt((bed.astype(float) ** 2).mean()) <= 2100
          and seam < 3.0)

print("\nB. Deterministic across machines (same bytes when regenerated)")
for name in ("quiet", "call_center"):
    # Only ever delete a SYNTHESIZED bed: bed_path now resolves real
    # recordings first, and an earlier version of this test happily unlinked
    # the committed app/ambience/office.wav.
    assert not ambient.is_recorded(name), f"{name} is a real recording — do not unlink it"
    before = ambient.bed_path(name).read_bytes()
    ambient.bed_path(name).unlink()
    after = ambient.bed_path(name).read_bytes()
    check(f"{name}: regenerates byte-identically", before == after)


# --- C. pipecat's mixer accepts them ----------------------------------------
print("\nC. SoundfileMixer compatibility")
from pipecat.audio.mixers.soundfile_mixer import SoundfileMixer   # noqa: E402


async def mixer_checks():
    mixer = SoundfileMixer(sound_files={"office": str(ambient.bed_path("office"))},
                           default_sound="office", volume=0.08, loop=True)
    await mixer.start(8000)
    # If the sample rate were wrong, pipecat logs a warning and loads nothing.
    check("bed loaded by the mixer (would be dropped on a rate mismatch)",
          "office" in mixer._sounds)

    silence = b"\x00" * 320                      # one 20 ms frame
    out = np.frombuffer(await mixer.mix(silence), dtype=np.int16)
    rms = np.sqrt((out.astype(np.float64) ** 2).mean())
    check(f"ambient audible over silence (rms {rms:.0f} ≈ 25 dB under speech)", 80 < rms < 400)

    # Bot speech at full tilt + ambient must not wrap around into crackle.
    loud = (np.ones(160, dtype=np.int16) * 32000).tobytes()
    mixed = np.frombuffer(await mixer.mix(loud), dtype=np.int16)
    check("loud speech + ambient stays clipped, never wraps", mixed.min() >= 0)

    # A full loop's worth of frames keeps flowing (no stall at the seam).
    total = 0
    for _ in range(8000 // 160 * 21):            # 21 s > one 20 s loop
        total += len(await mixer.mix(silence))
    check("plays continuously past the loop point", total == 320 * (8000 // 160 * 21))

asyncio.run(mixer_checks())


# --- D. per-call resolution --------------------------------------------------
print("\nD. Which bed a call gets")
from app.bot import _resolve_ambient, _transport_params   # noqa: E402

config.AMBIENT_ENABLED, config.AMBIENT_SOUND = False, "office"
check("config off, no per-call choice -> silent", _resolve_ambient({}) == "")
check("config off, call asks for cafe -> cafe", _resolve_ambient({"ambient": "cafe"}) == "cafe")
config.AMBIENT_ENABLED = True
check("config on -> config bed", _resolve_ambient({}) == "office")
check("call says off -> silent", _resolve_ambient({"ambient": "off"}) == "")
check("unknown bed name -> silent, not an error", _resolve_ambient({"ambient": "../etc"}) == "")
check("no call doc at all -> config bed", _resolve_ambient(None) == "office")
config.AMBIENT_ENABLED = False

print("\n   volume ceiling (a bed near speech level ruins the call)")
for asked, want in ((0.08, 0.08), (config.AMBIENT_MAX_VOLUME, config.AMBIENT_MAX_VOLUME),
                    (1.0, config.AMBIENT_MAX_VOLUME), (-1, 0.0)):
    got = _transport_params("office", asked).audio_out_mixer._volume
    check(f"asked {asked} -> plays at {got}", abs(got - want) < 1e-9)

params = _transport_params("office")
check("transport gets a mixer when a bed is set", params.audio_out_mixer is not None)
check("no mixer when silent", _transport_params("").audio_out_mixer is None)
check("bad bed name degrades to no mixer (call still runs)",
      _transport_params("nope").audio_out_mixer is None)


# --- E. Audio Check still works with ambient underneath ----------------------
print("\nE. Audio Check segmentation with a background bed")
recs = sorted(glob.glob("recordings/*.wav"), key=os.path.getmtime)
if not recs:
    print("  (skipped — no local recordings to test against)")
else:
    with wave.open(recs[-1]) as wf:
        sr, nch = wf.getframerate(), wf.getnchannels()
        pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).reshape(-1, nch)
    agent = pcm[:, 1].astype(np.float64)
    _, bed = read_bed("office")

    def mask(segs, dur):
        m = np.zeros(int(dur * 50) + 1, bool)
        for a, b in segs:
            m[int(a * 50):int(b * 50)] = True
        return m

    def agreement(x, vol):
        """How closely the transcript segments match what the SAME audio gives
        with no bed underneath. 1.00 = the bed is invisible to Audio Check."""
        dur = len(x) / sr
        clean = mask(_speech_segments(x.astype(np.int16), sr), dur)
        mixed = np.clip(x + np.resize(bed.astype(np.float64), len(x)) * vol, -32768, 32767)
        got = mask(_speech_segments(mixed.astype(np.int16), sr), dur)
        return (clean & got).sum() / max((clean | got).sum(), 1)

    # Real call, plus the case that breaks percentile-based thresholds: the
    # agent talking for ~90% of the call (our talk share has hit 83% live).
    frames = agent[:len(agent) // 160 * 160].reshape(-1, 160)
    talky = np.concatenate([frames[np.sqrt((frames ** 2).mean(axis=1)) > 350].ravel(),
                            np.zeros(sr * 3)])
    for label, x in (("real call", agent), ("agent talks 90% of the call", talky)):
        for vol in (0.08, 0.20):
            score = agreement(x, vol)
            check(f"{label} @ volume {vol}: segments unchanged by the bed",
                  score >= 0.85, f"agreement {score:.2f}")
    # And the bed must not change anything when it is not in use.
    dur = len(agent) / sr
    check("no ambient: identical to before this feature",
          agreement(agent, 0.0) == 1.0)

print(f"\n{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
