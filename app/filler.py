"""Out-of-band filler audio (Vapi-style, done safely).

Fillers are PRE-SYNTHESIZED raw PCM clips injected directly into the output
transport — they never touch the LLM context, never enter the TTS websocket,
and therefore can never interleave with or trail behind a real response.
Selection is context-aware: tool wait / owner question / owner statement.
"""

import base64
import hashlib
import io
import wave
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger

from app import config

CACHE_DIR = Path(__file__).parent.parent / "filler_cache"

QUESTION_MARKERS = ("ఎంత", "ఎలా", "ఏంటి", "ఏమిటి", "ఎవరు", "ఎప్పుడు",
                    "ఎందుకు", "ఏమి", "ఎక్కడ", "?")


def pick_phrase(last_user_text: str, tool_recent: bool) -> tuple:
    """Return (context_name, phrase) for the current conversation state."""
    if tool_recent:
        return "tool", config.FILLER_BY_CONTEXT["tool"]
    text = last_user_text or ""
    if any(marker in text for marker in QUESTION_MARKERS):
        return "question", config.FILLER_BY_CONTEXT["question"]
    return "statement", config.FILLER_BY_CONTEXT["statement"]


class FillerAudio:
    """Synthesizes and caches 8kHz mono PCM clips per (voice, phrase)."""

    def __init__(self, voice: str):
        self._voice = voice
        self._mem: dict = {}

    def _key(self, phrase: str) -> str:
        return hashlib.md5(f"{self._voice}|{phrase}".encode()).hexdigest()

    async def get(self, phrase: str) -> Optional[bytes]:
        key = self._key(phrase)
        if key in self._mem:
            return self._mem[key]
        f = CACHE_DIR / f"{key}.pcm"
        if f.exists():
            data = f.read_bytes()
            self._mem[key] = data
            return data
        try:
            async with aiohttp.ClientSession() as s:
                r = await s.post(
                    "https://api.sarvam.ai/text-to-speech",
                    headers={"api-subscription-key": config.SARVAM_API_KEY},
                    json={
                        "text": phrase,
                        "target_language_code": "te-IN",
                        "speaker": self._voice,
                        "model": config.TTS_MODEL,
                        "speech_sample_rate": 8000,
                    },
                    timeout=aiohttp.ClientTimeout(total=8),
                )
                j = await r.json()
            wav_bytes = base64.b64decode(j["audios"][0])
            with wave.open(io.BytesIO(wav_bytes)) as wf:
                if wf.getframerate() != 8000 or wf.getnchannels() != 1:
                    logger.warning(f"filler synth unexpected format: "
                                   f"{wf.getframerate()}Hz ch={wf.getnchannels()}")
                    return None
                data = wf.readframes(wf.getnframes())
            CACHE_DIR.mkdir(exist_ok=True)
            f.write_bytes(data)
            self._mem[key] = data
            return data
        except Exception as e:
            logger.warning(f"filler synthesis failed ({phrase!r}): {e}")
            return None

    async def prewarm(self):
        """Synthesize all context phrases up front (fire-and-forget per call)."""
        for phrase in config.FILLER_BY_CONTEXT.values():
            await self.get(phrase)
