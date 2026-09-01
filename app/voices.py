"""Sarvam voice catalogue + on-demand samples for the dashboard's Voices tab.

We shipped one voice (kavya) and never auditioned the rest. Bulbul v3 has 37,
and they differ a lot in how native the Telugu sounds — which is exactly the
thing you can only judge by ear, so this module exists to let a human listen.

Samples are cached on disk: the same voice/pace/temperature/text/rate is
synthesized once and replayed from then on.
"""

from __future__ import annotations

import base64
import hashlib
import wave
from pathlib import Path

import aiohttp

from app import config

CACHE_DIR = Path(__file__).parent.parent / "voice_cache"

# Bulbul v3 speakers, per Sarvam's API reference. Gender is by name and is only
# a grouping aid for the picker — trust your ears, not the label.
V3_FEMALE = ["kavya", "shreya", "pooja", "ritu", "priya", "neha", "simran",
             "ishita", "tanya", "roopa", "shruti", "suhani", "kavitha", "rupali"]
V3_MALE = ["shubh", "aditya", "rahul", "rohan", "amit", "dev", "ratan", "varun",
           "manan", "sumit", "kabir", "aayan", "ashutosh", "advait", "anand",
           "tarun", "sunny", "mani", "gokul", "vijay", "mohit", "rehan", "soham"]
# The older model. Different recordings entirely, so worth hearing before ruling out.
V2_FEMALE = ["anushka", "manisha", "vidya", "arya"]
V2_MALE = ["abhilash", "karun", "hitesh"]

ALL = {v: ("bulbul:v3", "female") for v in V3_FEMALE}
ALL.update({v: ("bulbul:v3", "male") for v in V3_MALE})
ALL.update({v: ("bulbul:v2", "female") for v in V2_FEMALE})
ALL.update({v: ("bulbul:v2", "male") for v in V2_MALE})

# The line owners actually hear first — judge voices on the real script.
SAMPLE_TEXT = ("హలో నమస్తే సర్! మీరు Sri Sai owner గారేనా? "
               "నేను Kavya, LAMPOSE నుంచి — ఒక్క నిమిషం మాట్లాడొచ్చా సర్?")


def catalogue() -> list[dict]:
    """Every voice, in the order the picker should show them."""
    out = []
    for name in V3_FEMALE + V3_MALE + V2_FEMALE + V2_MALE:
        model, gender = ALL[name]
        out.append({"name": name, "model": model, "gender": gender})
    return out


async def sample(voice: str, text: str = "", pace: float | None = None,
                 temperature: float | None = None, sample_rate: int = 8000) -> bytes:
    """WAV bytes for one voice, synthesized once and cached.

    Raises KeyError for an unknown voice (so callers can pass user input
    straight in) and RuntimeError with Sarvam's own message on failure.
    """
    if voice not in ALL:
        raise KeyError(voice)
    model, _ = ALL[voice]
    text = (text or SAMPLE_TEXT).strip()[:400]
    pace = config.TTS_PACE if pace is None else pace
    key = hashlib.md5(
        f"{voice}|{model}|{pace}|{temperature}|{sample_rate}|{text}".encode()
    ).hexdigest()
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{key}.wav"
    if path.exists():
        return path.read_bytes()

    body = {"text": text, "target_language_code": "te-IN", "speaker": voice,
            "model": model, "pace": pace, "speech_sample_rate": sample_rate}
    # v3 takes temperature (expressiveness); v2 has no such knob.
    if temperature is not None and model == "bulbul:v3":
        body["temperature"] = temperature
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.sarvam.ai/text-to-speech",
                          headers={"api-subscription-key": config.SARVAM_API_KEY},
                          json=body, timeout=aiohttp.ClientTimeout(total=60)) as r:
            payload = await r.json()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}: {str(payload)[:200]}")
    wav = base64.b64decode(payload["audios"][0])
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(wav)
    tmp.replace(path)
    return wav


def duration_secs(wav: bytes) -> float:
    """Spoken length of a WAV, for showing how much pace changes things."""
    import io
    with wave.open(io.BytesIO(wav)) as wf:
        return wf.getnframes() / wf.getframerate()
