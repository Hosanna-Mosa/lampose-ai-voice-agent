"""Ambient background sound ("background sound" in other platforms).

A bare phone line sounds dead: our voice arrives on absolute digital silence,
which reads as synthetic. A faint room bed under the agent makes the call feel
like a person sitting somewhere real.

The beds are SYNTHESIZED here rather than shipped as audio files: no licensing,
no downloads, byte-identical on every machine (fixed seeds), and exactly the
8 kHz mono s16 that pipecat's SoundfileMixer requires (it will not resample —
it just warns and plays nothing).

Every bed is built from *circular* noise: random-phase spectra passed through
an inverse FFT wrap around perfectly, so a looping bed has no click at the
seam. Modulation envelopes and one-shot events wrap the same way.

Nothing here synthesizes intelligible speech. The "chatter" layers are
band-limited noise shaped like the rhythm of distant conversation — it reads as
a room with people in it, never as words you could mistake for a real person.
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

SR = 8000              # phone rate; must equal the transport's rate
SECONDS = 20           # loop length
TARGET_RMS = 2000.0    # every bed normalised here, so AMBIENT_VOLUME means
                       # the same loudness whichever bed is selected

CACHE_DIR = Path(__file__).parent.parent / "ambient_cache"

# name -> (human description, builder)
_BUILDERS: dict = {}

BEDS: dict[str, str] = {}


def _bed(name: str, description: str):
    def deco(fn):
        _BUILDERS[name] = fn
        BEDS[name] = description
        return fn
    return deco


# ----------------------------------------------------------------- primitives

def _circular_noise(n: int, rng: np.random.Generator, shape) -> np.ndarray:
    """Noise with an arbitrary spectral shape that loops seamlessly.

    Built in the frequency domain (random phase, magnitude = shape(freq)) and
    inverse-FFT'd, so the result is periodic over exactly n samples.
    """
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    mag = shape(freqs).astype(np.float64)
    phase = rng.uniform(0.0, 2.0 * np.pi, len(freqs))
    spec = mag * np.exp(1j * phase)
    spec[0] = 0.0  # no DC
    x = np.fft.irfft(spec, n)
    peak = np.max(np.abs(x))
    return x / peak if peak else x


def _band(low: float, high: float, tilt: float = 0.0):
    """Spectral shape: flat between low..high with soft edges, optional tilt
    (dB/octave, negative = darker)."""
    def shape(f):
        m = np.ones_like(f)
        m *= 1.0 / (1.0 + (np.maximum(f, 1e-6) / max(high, 1e-6)) ** 4)     # low-pass edge
        m *= 1.0 - 1.0 / (1.0 + (np.maximum(f, 1e-6) / max(low, 1e-6)) ** 4)  # high-pass edge
        if tilt:
            m *= (np.maximum(f, 20.0) / 1000.0) ** (tilt / 6.0)
        return m
    return shape


def _rumble(cut: float = 120.0):
    """1/f low-frequency energy — HVAC, traffic, building hum."""
    def shape(f):
        m = 1.0 / np.maximum(f, 1.0)
        return m / (1.0 + (np.maximum(f, 1e-6) / cut) ** 3)
    return shape


def _slow_envelope(n: int, rng: np.random.Generator, rate: float,
                   depth: float = 1.0) -> np.ndarray:
    """Positive, slowly wandering gain curve that also loops seamlessly."""
    e = _circular_noise(n, rng, _band(rate * 0.25, rate))
    e = (e - e.min()) / (e.max() - e.min() + 1e-9)   # 0..1
    return 1.0 - depth + depth * e


def _events(n: int, rng: np.random.Generator, count: int, make) -> np.ndarray:
    """Scatter `count` one-shot sounds around the loop, wrapping at the seam."""
    out = np.zeros(n)
    for _ in range(count):
        ev = make(rng)
        pos = rng.integers(0, n)
        idx = (np.arange(len(ev)) + pos) % n     # wrap => loop stays clean
        np.add.at(out, idx, ev)
    return out


def _click(rng: np.random.Generator, ms: float, tone: float, decay: float) -> np.ndarray:
    """Short damped burst — keyboard tap, cup on a saucer, a door."""
    n = int(SR * ms / 1000.0)
    t = np.arange(n) / SR
    body = np.sin(2 * np.pi * tone * t) * 0.5 + rng.standard_normal(n) * 0.5
    return body * np.exp(-t * decay)


def _chatter(n: int, rng: np.random.Generator, layers: int, depth: float) -> np.ndarray:
    """Distant conversation: speech-band noise whose loudness rises and falls
    at conversational rhythm. Deliberately word-free."""
    out = np.zeros(n)
    for _ in range(layers):
        voice = _circular_noise(n, rng, _band(rng.uniform(250, 400),
                                              rng.uniform(2200, 3200), tilt=-3))
        syllables = _slow_envelope(n, rng, rng.uniform(3.0, 6.0), depth=0.9)
        phrases = _slow_envelope(n, rng, rng.uniform(0.12, 0.4), depth=depth)
        out += voice * syllables * phrases
    return out / max(layers, 1)


def _normalise(x: np.ndarray) -> np.ndarray:
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms > 0:
        x = x * (TARGET_RMS / rms)
    return np.clip(x, -32768, 32767).astype(np.int16)


# ------------------------------------------------ real recordings -> loops

def _resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    """Band-limited resample. Truncating the spectrum is the anti-alias filter,
    which matters going 48k -> 8k: without it, traffic and chatter fold back as
    metallic hiss."""
    if sr_in == sr_out:
        return x
    n_out = int(round(len(x) * sr_out / sr_in))
    X = np.fft.rfft(x)
    Y = np.zeros(n_out // 2 + 1, dtype=complex)
    keep = min(len(X), len(Y))
    Y[:keep] = X[:keep]
    return np.fft.irfft(Y, n_out) * (n_out / len(x))


def _steadiest_window(x: np.ndarray, sr: int, seconds: int) -> np.ndarray:
    """Pick the stretch that sounds most like a room and least like an event.

    Field recordings contain doors, shouts and passing sirens; under a sales
    call those read as something happening, which is worse than silence."""
    need = seconds * sr
    if len(x) <= need:
        return np.pad(x, (0, max(0, need - len(x))), mode="wrap")
    W = int(sr * 0.05)
    frames = x[:len(x) // W * W].reshape(-1, W)
    rms = np.sqrt((frames ** 2).mean(axis=1)) + 1e-9
    per_win = need // W
    # score = spikiness of the window (peak vs typical); lower is steadier
    scores = [(np.percentile(rms[i:i + per_win], 99) / np.median(rms[i:i + per_win]), i)
              for i in range(0, len(rms) - per_win, max(1, per_win // 4))]
    _, best = min(scores)
    start = best * W
    return x[start:start + need]


def prepare_bed(x: np.ndarray, sr: int, seconds: int = SECONDS) -> np.ndarray:
    """Turn any recording into a seamless 8 kHz mono loop at our standard level."""
    if x.ndim > 1:
        x = x.mean(axis=1)
    x = np.asarray(x, dtype=np.float64)
    x = _steadiest_window(x, sr, seconds + 1)          # +1s of crossfade material
    x = _resample(x, sr, SR)
    n, fade = seconds * SR, SR // 2
    if len(x) < n + fade:
        x = np.pad(x, (0, n + fade - len(x)), mode="wrap")
    # Real recordings do not wrap around like synthesized ones, so blend the
    # tail back over the head — otherwise the loop clicks every 20 seconds.
    ramp = np.linspace(0.0, 1.0, fade)
    out = x[:n].copy()
    out[:fade] = x[:fade] * ramp + x[n:n + fade] * (1 - ramp)
    return _normalise(out)


# ---------------------------------------------------------------------- beds

@_bed("quiet", "Quiet room — barely-there room tone, just enough to feel live")
def _quiet(rng):
    n = SECONDS * SR
    return (_circular_noise(n, rng, _band(200, 3400, tilt=-6)) * 0.6
            + _circular_noise(n, rng, _rumble(80)) * 0.4)


@_bed("office", "Office — air-conditioning hum with the odd keyboard tap")
def _office(rng):
    n = SECONDS * SR
    bed = (_circular_noise(n, rng, _rumble(140)) * 1.0
           + _circular_noise(n, rng, _band(300, 3200, tilt=-4)) * 0.25)
    bed *= _slow_envelope(n, rng, 0.15, depth=0.25)
    taps = _events(n, rng, 26, lambda r: _click(r, 18, r.uniform(900, 2600), 260)
                   * r.uniform(0.05, 0.16))
    return bed + taps


@_bed("call_center", "Call centre — a room of people on calls, heard from a distance")
def _call_center(rng):
    n = SECONDS * SR
    bed = (_circular_noise(n, rng, _rumble(150)) * 0.8
           + _circular_noise(n, rng, _band(300, 3200, tilt=-4)) * 0.2)
    return bed + _chatter(n, rng, layers=6, depth=0.75) * 1.15


@_bed("cafe", "Café — low chatter, cups and cutlery")
def _cafe(rng):
    n = SECONDS * SR
    bed = (_circular_noise(n, rng, _rumble(120)) * 0.9
           + _chatter(n, rng, layers=4, depth=0.85) * 0.9)
    clinks = _events(n, rng, 14, lambda r: _click(r, 90, r.uniform(1800, 3200), 55)
                     * r.uniform(0.05, 0.14))
    return bed + clinks


@_bed("street", "Street — traffic passing outside")
def _street(rng):
    n = SECONDS * SR
    bed = _circular_noise(n, rng, _rumble(200)) * 1.0
    for _ in range(5):                      # vehicles passing
        pass_by = _circular_noise(n, rng, _band(120, 1400, tilt=-3))
        bed += pass_by * _slow_envelope(n, rng, rng.uniform(0.1, 0.25), depth=1.0) ** 3
    bed += _circular_noise(n, rng, _band(400, 3000, tilt=-6)) * 0.15
    return bed


# --------------------------------------------------------------------- public

# Fixed seed per bed: the VPS generates byte-identical audio, so what you
# audition locally is exactly what callers hear.
_SEEDS = {"quiet": 11, "office": 22, "call_center": 33, "cafe": 44, "street": 55}

def available() -> list[str]:
    """Bed names, in a sensible order for a dropdown: anything you recorded
    yourself first, then the built-ins."""
    custom = sorted(p.stem for p in CUSTOM_DIR.glob("*.wav")) if CUSTOM_DIR.exists() else []
    return custom + ["quiet", "office", "call_center", "cafe", "street"]


def describe(name: str) -> str:
    if name in BEDS:
        return BEDS[name] + (" — real recording" if (RECORDED_DIR / f"{name}.wav").exists()
                             else " — synthesized")
    return "Your own recording"


def is_recorded(name: str) -> bool:
    return ((CUSTOM_DIR / f"{name}.wav").exists()
            or (RECORDED_DIR / f"{name}.wav").exists())


RECORDED_DIR = Path(__file__).parent / "ambience"      # real recordings, in git
CUSTOM_DIR = Path(__file__).parent.parent / "ambient_custom"   # uploaded on the server


def bed_path(name: str) -> Path:
    """Path to the 8 kHz mono WAV for `name`.

    A real recording always wins over the synthesized version: noise shaped to
    look like a room still sounds like noise, and owners hear the difference.
    Raises KeyError for an unknown name (callers pass user input straight in,
    so this doubles as the whitelist).
    """
    for folder in (CUSTOM_DIR, RECORDED_DIR):
        real = folder / f"{name}.wav"
        if real.exists():
            return real
    if name not in _BUILDERS:
        raise KeyError(name)
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{name}.wav"
    if not path.exists():
        # Fixed seed per bed: the VPS generates byte-identical audio, so what
        # you audition locally is exactly what callers hear.
        rng = np.random.default_rng(_SEEDS[name])
        pcm = _normalise(_BUILDERS[name](rng))
        tmp = path.with_suffix(".tmp")
        with wave.open(str(tmp), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SR)
            wf.writeframes(pcm.tobytes())
        tmp.replace(path)   # atomic: two calls starting at once can't tear it
    return path


def save_custom(name: str, x: np.ndarray, sr: int) -> Path:
    """Store an uploaded recording as a ready-to-play bed."""
    CUSTOM_DIR.mkdir(exist_ok=True)
    path = CUSTOM_DIR / f"{name}.wav"
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(prepare_bed(x, sr).tobytes())
    tmp.replace(path)
    return path


def prewarm() -> None:
    """Generate every bed up front (called at server start)."""
    for name in available():
        bed_path(name)
