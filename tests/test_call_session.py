"""Call session start-up: does a Twilio websocket become a running call?

This is the seam that broke in production — the CallSid is only available after
the handshake is parsed, and reading it too early rejected every single call
with "unknown call session sid=none". These tests fake the handshake so the
ordering is checked without a phone.

Run: PYTHONPATH=. ./venv/bin/python tests/test_call_session.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import bot   # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class FakeCallData:
    """What pipecat hands back once it has read Twilio's start message."""
    call_id = "CAtest123"


class FakeRunnerArgs:
    """Mirrors WebSocketRunnerArguments: call_data is None until the handshake
    is parsed — that is the whole point of these tests."""
    def __init__(self):
        self.websocket = object()
        self.call_data = None


class Sentinel(Exception):
    pass


def patched(get_call_by_sid, create_transport=None):
    """Swap out the network-facing pieces of run_call."""
    bot.parse_telephony_websocket = lambda ws: _async(("twilio", FakeCallData()))
    bot.db.get_call_by_sid = get_call_by_sid
    bot.create_transport = create_transport or (lambda *a, **k: _async(None))


async def _async(value):
    return value


_orig = (bot.parse_telephony_websocket, bot.db.get_call_by_sid, bot.create_transport)


# --- 1. the production bug ---------------------------------------------------
print("\n1. CallSid is resolved from the handshake")
seen = {}


async def record_sid(sid):
    seen["sid"] = sid
    return None            # unknown call -> run_call returns after the guard


patched(record_sid)
asyncio.run(bot.run_call(FakeRunnerArgs()))
check("looks the call up by its real CallSid (was 'none' in production)",
      seen.get("sid") == "CAtest123", f"got {seen.get('sid')!r}")


# --- 2. per-call ambient reaches the transport -------------------------------
print("\n2. Per-call background sound reaches the transport")
captured = {}


async def one_call(sid):
    return {"direction": "test", "lead_id": None, "voice": "kavya",
            "ambient": "cafe", "ambient_volume": 0.12,
            "pace": 1.0, "temperature": 0.45}


def capture_transport(runner_args, params_map):
    captured["params"] = params_map["twilio"]()
    raise Sentinel                      # stop before real Sarvam/Anthropic setup


bot._ACTIVE_PIPELINES = 0
patched(one_call, capture_transport)
try:
    asyncio.run(bot.run_call(FakeRunnerArgs()))
except Sentinel:
    pass
mixer = getattr(captured.get("params"), "audio_out_mixer", None)
check("transport is built with the call's own bed", mixer is not None)
if mixer:
    check("correct bed selected", "cafe" in mixer._sound_files, str(list(mixer._sound_files)))
    check("correct volume applied", abs(mixer._volume - 0.12) < 1e-9, f"{mixer._volume}")


# --- 3. capacity accounting --------------------------------------------------
print("\n3. Concurrency slots are returned when set-up fails")
check("failed set-up does not leak a slot (6 leaks would block every call)",
      bot._ACTIVE_PIPELINES == 0, f"counter at {bot._ACTIVE_PIPELINES}")

bot._ACTIVE_PIPELINES = bot.config.MAX_ACTIVE_PIPELINES
patched(one_call, capture_transport)
asyncio.run(bot.run_call(FakeRunnerArgs()))     # rejected: at capacity
check("at capacity, the call is refused rather than started",
      bot._ACTIVE_PIPELINES == bot.config.MAX_ACTIVE_PIPELINES)
bot._ACTIVE_PIPELINES = 0

bot.parse_telephony_websocket, bot.db.get_call_by_sid, bot.create_transport = _orig
print(f"\n{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
