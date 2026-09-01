"""Latency-tuned turn taking for Telugu calls.

Two targeted fixes on top of pipecat's TurnAnalyzerUserTurnStopStrategy —
nothing else about turn detection is changed (same smart-turn model, same
VAD thresholds, same barge-in):

FIX 1 (STT safety-net gate): Sarvam never sets ``finalized=True`` on its
transcripts, so upstream waits the full ttfs_p99 (1.17s) after speech end
even when the transcript is already in hand. Here, a transcript that
arrives AFTER the user's true speech end — while VAD reports silence and
the analyzer said COMPLETE — is treated as final and triggers the turn
immediately. The p99 timer remains armed as the fallback for the
no-transcript case, and nothing fires if speech resumes (the inherited
``_discard_pending_end_of_turn`` clears all state on VAD start, and the
UserTurnController additionally refuses to stop a turn while the user is
audibly speaking).

FIX 2 (INCOMPLETE grace): when the analyzer says INCOMPLETE, resolution
used to come from the aggregator watchdog, which resets on every
transcription — so it fired ~transcript+1.5s (≈2.2s after speech end).
Here a grace timer is anchored at TRUE SPEECH END: if the user stays
silent for ``incomplete_grace_secs`` after they stopped talking and a
transcript exists, the turn ends. Speech resuming cancels the timer, so
natural pauses shorter than the grace window are still protected exactly
as before.
"""

import asyncio
import time
from typing import Optional

from loguru import logger
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    TranscriptionFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.turns.types import ProcessFrameResult
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)

from app.logsetup import step


class TurnTimes:
    """Per-call latency recorder shared by the pipeline processors.

    Marks wall-clock timestamps for the stages of one user->bot exchange and
    emits a single [STEP 22-TURN-TIMING] line when the bot starts speaking.
    """

    def __init__(self):
        self.marks: dict = {}
        self.history: list = []
        self.on_turn_complete = None  # optional sync callback(tc_timestamp)
        # live pipeline state used by filler + end-call drain
        self.last_user_text: str = ""
        self.bot_speaking: bool = False
        # True once end_call has begun closing: no further user turns are
        # accepted, so a goodbye can never be followed by a second one.
        self.closing: bool = False
        self.last_bot_audio_end: float = 0.0
        self.last_tool_wall: float = 0.0
        self.suppress_next_bot_start: int = 0  # filler audio must not count as the response
        self.startup: dict = {}  # call-startup wall times (answered/ws/greeting/first audio)
        self.bot_texts: list = []  # rolling (wall_time, text) of what the bot is saying

    def note_bot_text(self, text: str):
        now = time.time()
        self.bot_texts.append((now, text))
        self.bot_texts = [(t, x) for t, x in self.bot_texts if now - t < 20.0]

    def recent_bot_text(self, window: float = 15.0) -> str:
        now = time.time()
        return " ".join(x for t, x in self.bot_texts if now - t < window)

    def mark(self, name: str, t: Optional[float] = None):
        self.marks[name] = t if t is not None else time.time()
        if name == "TURN_COMPLETE" and self.on_turn_complete:
            try:
                self.on_turn_complete(self.marks[name])
            except Exception:
                pass

    @staticmethod
    def _d(a: Optional[float], b: Optional[float]) -> Optional[float]:
        return None if (a is None or b is None) else max(0.0, b - a)

    def emit_if_complete(self):
        """Called on BOT_SPEECH_START; logs the turn's segment breakdown."""
        m = self.marks
        if "TURN_COMPLETE" not in m:
            # bot audio while the user's turn is still open (backchannel/filler)
            # — not a response; keep the other marks for the real one
            m.pop("BOT_SPEECH_START", None)
            return
        end = m.get("USER_SPEECH_END")
        bot = m.get("BOT_AUDIO_START")
        if end is None or bot is None:
            self.marks = {}
            return
        segs = [
            ("end→stt", self._d(end, m.get("STT_FINAL"))),
            ("stt→turn", self._d(m.get("STT_FINAL"), m.get("TURN_COMPLETE"))),
            ("turn→llm_req", self._d(m.get("TURN_COMPLETE"), m.get("LLM_REQUEST_START"))),
            ("llm_req→first_token", self._d(m.get("LLM_REQUEST_START"), m.get("LLM_FIRST_TOKEN"))),
            ("first_token→first_text", self._d(m.get("LLM_FIRST_TOKEN"), m.get("LLM_FIRST_TEXT_FRAME"))),
            ("first_text→tts_req", self._d(m.get("LLM_FIRST_TEXT_FRAME"), m.get("TTS_REQUEST_START"))),
            ("tts_req→first_audio", self._d(m.get("TTS_REQUEST_START"), m.get("TTS_FIRST_AUDIO"))),
            ("first_audio→bot_audio", self._d(m.get("TTS_FIRST_AUDIO"), bot)),
        ]
        total = self._d(end, bot)
        parts = " | ".join(f"{n} {v:.2f}s" for n, v in segs if v is not None)
        step("22-TURN-TIMING", f"{parts} | TOTAL end→bot {total:.2f}s")
        self.history.append({n: v for n, v in segs if v is not None} | {"total": total})
        self.marks = {}

    def averages(self) -> dict:
        if not self.history:
            return {}
        keys = {k for h in self.history for k in h}
        return {k: round(sum(h[k] for h in self.history if k in h)
                         / max(1, sum(1 for h in self.history if k in h)), 3)
                for k in keys}


class TeluguFastTurnStopStrategy(TurnAnalyzerUserTurnStopStrategy):
    """TurnAnalyzer stop strategy with the two latency fixes above."""

    def __init__(self, *, turn_times: Optional[TurnTimes] = None,
                 incomplete_grace_secs: float = 1.5,
                 fast_fire_recency_secs: float = 2.5,
                 backchannel_after_secs: float = 4.0,
                 backchannel_max_per_turn: int = 2, **kwargs):
        super().__init__(**kwargs)
        self._turn_times = turn_times
        self._incomplete_grace = incomplete_grace_secs
        self._fast_fire_recency = fast_fire_recency_secs
        self._incomplete_task: Optional[asyncio.Task] = None
        # Backchanneling ("హా...", "ఊ...") while the user speaks at length.
        # bot.py sets on_backchannel; None disables the timers entirely.
        self.on_backchannel = None
        self._backchannel_after = backchannel_after_secs
        self._backchannel_max = backchannel_max_per_turn
        self._backchannel_task: Optional[asyncio.Task] = None
        self._backchannel_count = 0
        self._bot_speaking = False
        # Own copies of VAD/verdict state. The parent wipes ITS copies in
        # _reset() whenever a turn starts — and with MinWords as the only
        # start strategy, short utterances start the turn on the TRANSCRIPT,
        # i.e. AFTER the VAD stop, so the wipe destroys exactly the state
        # the fast path needs. These survive the reset by design.
        self._own_vad_speaking = False
        self._own_speech_end: float = 0.0
        self._own_verdict_complete = False

    # ------------------------------------------------------------- frames

    async def process_frame(self, frame: Frame) -> ProcessFrameResult:
        from pipecat.frames.frames import VADUserStartedSpeakingFrame

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
            await self._cancel_backchannel()
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
        elif isinstance(frame, VADUserStartedSpeakingFrame):
            self._own_vad_speaking = True
            self._own_verdict_complete = False
            await self._cancel_incomplete_grace()
            await self._arm_backchannel()
        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            self._own_speech_end = frame.timestamp - frame.stop_secs
            if self._turn_times:
                self._turn_times.mark("USER_SPEECH_END", self._own_speech_end)

        result = await super().process_frame(frame)

        if isinstance(frame, VADUserStoppedSpeakingFrame):
            # super() has run the analyzer; record its fresh verdict in
            # reset-proof storage.
            self._own_vad_speaking = False
            await self._cancel_backchannel()
            self._own_verdict_complete = self._turn_complete
            if self._own_verdict_complete:
                # covers transcript-arrived-before-VAD-stop ordering
                await self._try_fast_fire("vad-stop")
            else:
                await self._arm_incomplete_grace()
        elif isinstance(frame, TranscriptionFrame):
            # covers VAD-stop-before-transcript ordering (the common case for
            # short Telugu answers, where turn start wiped the parent's state)
            await self._try_fast_fire("transcript")

        return result

    async def _try_fast_fire(self, origin: str):
        """FIX 1: end a COMPLETE turn the moment transcript + silence align."""
        if (self._text
                and not self._own_vad_speaking
                and self._own_verdict_complete
                and not self._transcript_finalized
                and not self._timeout_expired
                and self._own_speech_end > 0
                and (time.time() - self._own_speech_end) < self._fast_fire_recency):
            self._turn_complete = True         # may have been wiped by _reset
            self._transcript_finalized = True
            logger.debug(f"{self}: fast turn-end ({origin}) — transcript in hand, "
                         f"verdict COMPLETE, skipping STT p99 wait")
            await self._maybe_trigger_user_turn_stopped()

    # ------------------------------------------------- FIX 2: grace timer

    async def _arm_incomplete_grace(self):
        await self._cancel_incomplete_grace()
        delay = max(0.0, (self._own_speech_end + self._incomplete_grace) - time.time())
        self._incomplete_task = self.task_manager.create_task(
            self._incomplete_grace_handler(delay), f"{self}::incomplete_grace"
        )

    async def _incomplete_grace_handler(self, delay: float):
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        finally:
            self._incomplete_task = None
        if self._own_vad_speaking or self._transcript_finalized:
            return  # user resumed, or the turn already ended
        if not self._text:
            return  # no transcript yet — leave resolution to the safety nets
        logger.debug(f"{self}: INCOMPLETE grace elapsed "
                     f"({self._incomplete_grace}s after speech end) — ending turn")
        self._turn_complete = True
        self._transcript_finalized = True
        await self._maybe_trigger_user_turn_stopped()

    async def _cancel_incomplete_grace(self):
        if self._incomplete_task:
            task, self._incomplete_task = self._incomplete_task, None
            await self.task_manager.cancel_task(task)

    # ----------------------------------------------------- state hygiene
    # NOTE: no _discard_pending_end_of_turn override — turn-start resets must
    # NOT cancel the grace timer (turn start is transcript-driven and would
    # kill it right when it is needed). Grace is cancelled on VAD speech
    # start, on turn stop, and on cleanup.

    # ------------------------------------------------------ backchannel

    async def _arm_backchannel(self):
        if self.on_backchannel is None or self._backchannel_count >= self._backchannel_max:
            return
        await self._cancel_backchannel()
        self._backchannel_task = self.task_manager.create_task(
            self._backchannel_handler(), f"{self}::backchannel"
        )

    async def _backchannel_handler(self):
        try:
            await asyncio.sleep(self._backchannel_after)
        except asyncio.CancelledError:
            return
        finally:
            self._backchannel_task = None
        if (self._own_vad_speaking and not self._bot_speaking
                and self._backchannel_count < self._backchannel_max):
            self._backchannel_count += 1
            try:
                self.on_backchannel()
            except Exception:
                pass
            await self._arm_backchannel()  # possible second ack later in a monologue

    async def _cancel_backchannel(self):
        if self._backchannel_task:
            task, self._backchannel_task = self._backchannel_task, None
            await self.task_manager.cancel_task(task)

    async def handle_user_turn_stopped(self):
        self._backchannel_count = 0
        await self._cancel_backchannel()
        await self._cancel_incomplete_grace()
        await super().handle_user_turn_stopped()

    async def cleanup(self):
        await self._cancel_backchannel()
        await self._cancel_incomplete_grace()
        await super().cleanup()

    # -------------------------------------------------------- observability

    async def trigger_user_turn_stopped(self):
        if self._turn_times:
            self._turn_times.mark("TURN_COMPLETE")
        await super().trigger_user_turn_stopped()
