"""Turn-taking regression suite for TeluguFastTurnStopStrategy.

Run:  PYTHONPATH=. ./venv/bin/python tests/test_turn_taking.py
"""
import asyncio, time, warnings
warnings.filterwarnings("ignore")

from pipecat.audio.turn.base_turn_analyzer import EndOfTurnState
from pipecat.frames.frames import (
    TranscriptionFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame,
)
from app.turn_taking import TeluguFastTurnStopStrategy


class StubTaskManager:
    def create_task(self, coro, name=""):
        return asyncio.get_event_loop().create_task(coro)
    async def cancel_task(self, task, timeout=None):
        task.cancel()
        try: await task
        except BaseException: pass


class StubAnalyzer:
    def __init__(self): self.verdict = EndOfTurnState.COMPLETE
    def update_vad_start_secs(self, x): pass
    def append_audio(self, a, s): return EndOfTurnState.INCOMPLETE
    async def analyze_end_of_turn(self): return (self.verdict, None)
    def clear(self): pass
    async def cleanup(self): pass
    def set_sample_rate(self, r): pass
    @property
    def params(self): return None


async def make(grace=0.3, p99=1.17, recency=2.5):
    an = StubAnalyzer()
    s = TeluguFastTurnStopStrategy(turn_analyzer=an, incomplete_grace_secs=grace,
                                   fast_fire_recency_secs=recency)
    await s.setup(StubTaskManager())
    s._stt_timeout = p99
    triggers = []
    orig = s.trigger_user_turn_stopped
    async def counting():
        triggers.append(time.time()); await orig()
    s.trigger_user_turn_stopped = counting
    return s, an, triggers


def vstop(): return VADUserStoppedSpeakingFrame(stop_secs=0.2, timestamp=time.time())
def vstart(): return VADUserStartedSpeakingFrame(start_secs=0.35, timestamp=time.time())
def tx(t="సరే సర్ చెప్పండి"): return TranscriptionFrame(t, "", "2026-08-26T12:00:00")


async def scenario_A():
    s, an, trig = await make()
    an.verdict = EndOfTurnState.COMPLETE
    t0 = time.time()
    await s.process_frame(vstop())
    assert s._timeout_task is not None and not trig
    await s.process_frame(tx())
    assert len(trig) == 1 and (trig[0] - t0) < 0.3
    assert s._timeout_task is None
    await s.handle_user_turn_stopped()
    print("A PASS  (COMPLETE fast path: immediate trigger)")


async def scenario_B():
    s, an, trig = await make(grace=0.4)
    an.verdict = EndOfTurnState.INCOMPLETE
    t0 = time.time()
    await s.process_frame(vstop()); await s.process_frame(tx())
    assert not trig
    await asyncio.sleep(0.6)
    assert len(trig) == 1 and 0.1 < (trig[0] - t0) < 0.55
    await s.handle_user_turn_stopped()
    print("B PASS  (INCOMPLETE grace anchored at speech end)")


async def scenario_C():
    s, an, trig = await make(grace=0.4)
    an.verdict = EndOfTurnState.INCOMPLETE
    await s.process_frame(vstop()); await s.process_frame(tx())
    await s.process_frame(vstart())
    assert s._incomplete_task is None
    await asyncio.sleep(0.6)
    assert not trig
    await s.handle_user_turn_stopped()
    print("C PASS  (resume cancels grace — pause protection)")


async def scenario_D():
    s, an, trig = await make(p99=0.4)
    an.verdict = EndOfTurnState.COMPLETE
    await s.process_frame(vstop())
    await asyncio.sleep(0.5)
    assert not trig
    await s.process_frame(tx())
    assert len(trig) == 1
    await s.handle_user_turn_stopped()
    print("D PASS  (p99 safety net intact for late transcripts)")


async def scenario_E():
    s, an, trig = await make(grace=0.2)
    an.verdict = EndOfTurnState.INCOMPLETE
    await s.process_frame(vstop()); await s.process_frame(tx())
    await asyncio.sleep(0.35)
    await s.process_frame(tx())
    await asyncio.sleep(0.3)
    assert len(trig) == 1
    await s.handle_user_turn_stopped()
    await asyncio.sleep(0.3)
    n = len(trig); await asyncio.sleep(0.3)
    assert len(trig) == n
    assert s._incomplete_task is None and s._timeout_task is None
    await s.cleanup()
    print("E PASS  (no duplicate/stale fires, no leaked timers)")


async def scenario_F():
    s, an, trig = await make()
    an.verdict = EndOfTurnState.COMPLETE
    await s.process_frame(vstart())
    t0 = time.time()
    await s.process_frame(vstop())
    await s.handle_user_turn_started()   # MinWords starts the turn ON the transcript
    assert not s._turn_complete
    await s.process_frame(tx())
    assert len(trig) == 1 and (trig[0] - t0) < 0.3
    await s.handle_user_turn_stopped()
    print("F PASS  (turn-start reset survived — no 0.97s fallback)")


async def scenario_G():
    s, an, trig = await make(grace=0.4)
    an.verdict = EndOfTurnState.INCOMPLETE
    await s.process_frame(vstart())
    t0 = time.time()
    await s.process_frame(vstop())
    await s.handle_user_turn_started()
    await s.process_frame(tx())
    assert not trig
    await asyncio.sleep(0.6)
    assert len(trig) == 1 and 0.1 < (trig[0] - t0) < 0.55
    await s.handle_user_turn_stopped()
    print("G PASS  (grace survives turn-start reset)")


async def scenario_H():
    s, an, trig = await make(recency=0.2)
    an.verdict = EndOfTurnState.COMPLETE
    await s.process_frame(vstart()); await s.process_frame(vstop())
    await asyncio.sleep(0.4)
    await s.handle_user_turn_started()
    await s.process_frame(tx())
    await asyncio.sleep(0.2)
    assert not trig
    await s.handle_user_turn_stopped(); await s.cleanup()
    print("H PASS  (stale-verdict guard)")


async def scenario_I():
    """Backchannel: monologue -> max acks; short answer -> none; cancel on stop."""
    s, an, trig = await make()
    s._backchannel_after = 0.2
    s._backchannel_max = 2
    acks = []
    s.on_backchannel = lambda: acks.append(1)
    await s.process_frame(vstart())
    await asyncio.sleep(0.55)
    assert len(acks) == 2, f"expected 2 acks, got {len(acks)}"
    await s.process_frame(vstop())
    await asyncio.sleep(0.3)
    assert len(acks) == 2
    await s.handle_user_turn_stopped()
    acks.clear()
    await s.process_frame(vstart())
    await asyncio.sleep(0.05)
    await s.process_frame(vstop())
    await asyncio.sleep(0.3)
    assert not acks
    await s.cleanup()
    print("I PASS  (backchannel: acks in monologue only, clean cancel)")


async def main():
    for sc in (scenario_A, scenario_B, scenario_C, scenario_D, scenario_E,
               scenario_F, scenario_G, scenario_H, scenario_I):
        await sc()
    await asyncio.sleep(0.2)
    print("\nALL TURN-TAKING TESTS PASSED ✔")

asyncio.run(main())
