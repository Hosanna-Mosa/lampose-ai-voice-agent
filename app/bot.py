"""Per-call Pipecat pipeline: Twilio Media Streams <-> Sarvam STT ->
Claude -> Sarvam Bulbul TTS, with tools, transcript capture and
post-call summarization."""

import array
import asyncio
import io
import wave
from pathlib import Path
import json
import math
import random
import time
from typing import Optional

from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    FunctionCallInProgressFrame,
    InputAudioRawFrame,
    LLMFullResponseStartFrame,
    OutputAudioRawFrame,
    SpeechOutputAudioRawFrame,
    TTSAudioRawFrame,
    LLMRunFrame,
    LLMTextFrame,
    MetricsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    TTSTextFrame,
    UserStartedSpeakingFrame,
)
from pipecat.metrics.metrics import LLMUsageMetricsData, TTFBMetricsData
from pipecat.turns.user_start.min_words_user_turn_start_strategy import (
    MinWordsUserTurnStartStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.pipeline.pipeline import Pipeline
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.sarvam.stt import SarvamSTTService
from pipecat.services.sarvam.tts import SarvamTTSService
from pipecat.transcriptions.language import Language
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams

from app import config, db
from app.logsetup import step
from app.filler import FillerAudio, pick_phrase
from app.prompts import build_opening_line, build_system_prompt
from app.tools import CallState, build_tools
from app.turn_taking import TeluguFastTurnStopStrategy, TurnTimes

def _transport_params() -> FastAPIWebsocketParams:
    kwargs = dict(audio_in_enabled=True, audio_out_enabled=True)
    if config.NOISE_FILTER:
        # RNNoise denoise on caller audio. CPU ≈0.6x realtime per call —
        # great for noisy environments, enable only when capacity allows.
        from pipecat.audio.filters.rnnoise_filter import RNNoiseFilter
        kwargs["audio_in_filter"] = RNNoiseFilter()
    return FastAPIWebsocketParams(**kwargs)


TRANSPORT_PARAMS = {"twilio": _transport_params}

RECORDINGS_DIR = Path(__file__).parent.parent / "recordings"

_ACTIVE_PIPELINES = 0  # cost-abuse guard: hard cap on concurrent live calls


# USD per 1M tokens: (input, output, cache_write, cache_read)
LLM_PRICES = {
    "claude-haiku-4-5": (1.0, 5.0, 1.25, 0.10),
    "claude-sonnet": (3.0, 15.0, 3.75, 0.30),
    "claude-opus": (15.0, 75.0, 18.75, 1.50),
}
USD_TO_INR = 88.0
_session_usd = 0.0  # cumulative Claude spend since server start


def _price_for(model: str):
    for prefix, p in LLM_PRICES.items():
        if model.startswith(prefix):
            return p
    return LLM_PRICES["claude-haiku-4-5"]


_LATENCY_LABELS = {
    "SarvamSTTService": "STT",
    "AnthropicLLMService": "LLM",
    "SarvamTTSService": "TTS",
}

# emoji planes only — Telugu block (0C00-0C7F) and ZWJ/ZWNJ are untouched
import re
_EMOJI_RE = re.compile("[\U0001F000-\U0001FBFF\u2600-\u27BF\uFE0F]")


class UsageTracker(FrameProcessor):
    """Accumulates Claude token usage + per-component response times."""

    def __init__(self, turn_times=None):
        super().__init__()
        self._turn_times = turn_times
        self.prompt = 0
        self.completion = 0
        self.cache_read = 0
        self.cache_write = 0
        self.latency: dict = {"STT": [], "LLM": [], "TTS": []}
        self._turn: dict = {}
        self._awaiting_first_token = False

    @property
    def cost_usd(self) -> float:
        i, o, w, r = _price_for(config.ANTHROPIC_MODEL)
        return (self.prompt * i + self.completion * o
                + self.cache_write * w + self.cache_read * r) / 1_000_000

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMFullResponseStartFrame):
            # pushed by AnthropicLLMService BEFORE the API request is built —
            # this is the request start, NOT the first token
            if self._turn_times:
                self._turn_times.mark("LLM_REQUEST_START")
            self._awaiting_first_token = True
        if isinstance(frame, FunctionCallInProgressFrame) and self._turn_times:
            self._turn_times.last_tool_wall = time.time()
        if isinstance(frame, TextFrame) and not isinstance(frame, TranscriptionFrame):
            if (self._awaiting_first_token and self._turn_times
                    and isinstance(frame, LLMTextFrame)):
                # first streamed TEXT FRAME of this response (post-adapter)
                self._turn_times.mark("LLM_FIRST_TEXT_FRAME")
                self._awaiting_first_token = False
            # the LLM occasionally emits an emoji despite the prompt — strip it
            # before TTS tries to pronounce it
            cleaned = _EMOJI_RE.sub("", frame.text)
            if cleaned != frame.text:
                frame.text = cleaned
        if isinstance(frame, MetricsFrame):
            for d in frame.data:
                if isinstance(d, LLMUsageMetricsData):
                    u = d.value
                    self.prompt += u.prompt_tokens
                    self.completion += u.completion_tokens
                    self.cache_read += u.cache_read_input_tokens or 0
                    self.cache_write += u.cache_creation_input_tokens or 0
                elif isinstance(d, TTFBMetricsData) and d.value:
                    name = (d.processor or "").split("#")[0]
                    label = _LATENCY_LABELS.get(name)
                    if label:
                        self.latency[label].append(d.value)
                        self._turn[label] = d.value
                        if (label == "LLM" and self._turn_times
                                and "LLM_REQUEST_START" in self._turn_times.marks
                                and "LLM_FIRST_TOKEN" not in self._turn_times.marks):
                            # true first token = request start + measured TTFB
                            self._turn_times.mark(
                                "LLM_FIRST_TOKEN",
                                self._turn_times.marks["LLM_REQUEST_START"] + d.value)
                        if label == "LLM":  # Sarvam TTS reports no TTFB -> emit here
                            parts = [f"{k} {self._turn[k]:.2f}s"
                                     for k in ("STT", "LLM") if k in self._turn]
                            total = sum(self._turn.get(k, 0) for k in ("STT", "LLM"))
                            step("21-LATENCY", " → ".join(parts) + f"  (reply ≈{total:.2f}s + speech)")
                            self._turn = {}
        await self.push_frame(frame, direction)


class EmotionMonitor(FrameProcessor):
    """Emotion v1 (Vapi analog): realtime voice-energy analysis.

    Tracks an EMA of the caller's speech loudness; when it crosses the
    'agitated' threshold (or calms back down), a silent developer note is
    added to the LLM context so the agent adapts its tone. Energy only —
    honest about what it is; no fake emotion classes.
    """

    SILENCE_RMS = 250

    def __init__(self, context, loud_rms: int = 4000):
        super().__init__()
        self._context = context
        self._loud = loud_rms
        self._ema = 0.0
        self._voice_state = "normal"

    def _rms(self, audio: bytes) -> float:
        samples = array.array("h", audio[: len(audio) // 2 * 2])
        if not samples:
            return 0.0
        return math.sqrt(sum(s * s for s in samples) / len(samples))

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputAudioRawFrame):
            rms = self._rms(frame.audio)
            if rms > self.SILENCE_RMS:  # only track while there is voice
                self._ema = 0.9 * self._ema + 0.1 * rms
        elif isinstance(frame, TranscriptionFrame):
            state = "agitated" if self._ema > self._loud else "normal"
            if state != self._voice_state:
                self._voice_state = state
                step("25-EMOTION", f"voice energy -> {state} (rms≈{int(self._ema)})")
                note = (
                    "Voice-analysis note: the owner now sounds loud/agitated. "
                    "Stay extra calm, be brief, do not push — offer a callback "
                    "if irritation continues."
                    if state == "agitated" else
                    "Voice-analysis note: the owner sounds calm again. Continue normally."
                )
                self._context.add_message({"role": "developer", "content": note})
        await self.push_frame(frame, direction)


class SentMediaMonitor(FrameProcessor):
    """Placed AFTER the output transport: frames arriving here were actually
    written to the Twilio websocket (real-time paced). Emits honest
    media-delivery events (AUDIO-FIRST-MEDIA / AUDIO-COMPLETE per utterance)
    and the redesigned startup timing. 'Sent' — not 'heard'; Twilio Media
    Streams gives no playback acknowledgment, so we never claim one."""

    def __init__(self, turn_times):
        super().__init__()
        self._tt = turn_times
        self._uid = 0
        self._frames = 0
        self._startup_done = False

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        su = getattr(self._tt, "startup", None) or {}
        if isinstance(frame, BotStartedSpeakingFrame):
            self._uid += 1
            self._frames = 0
            if "FIRST_MEDIA_SENT" not in su:
                su["FIRST_MEDIA_SENT"] = time.time()
            step("AUDIO-FIRST-MEDIA", f"utterance=U{self._uid} — media flowing to Twilio")
        elif isinstance(frame, OutputAudioRawFrame):
            self._frames += 1
        elif isinstance(frame, BotStoppedSpeakingFrame):
            dur = self._frames * 0.02
            step("AUDIO-COMPLETE", f"utterance=U{self._uid} sent={dur:.2f}s "
                 f"({self._frames} frames)")
            if not self._startup_done and su.get("FIRST_MEDIA_SENT"):
                self._startup_done = True
                def _d(a, b):
                    return (su[b] - su[a]) if su.get(a) and su.get(b) else None
                parts = []
                for n, a, b in [("answered→ws", "CALL_ANSWERED", "WS_CONNECTED"),
                                ("ws→pipeline", "WS_CONNECTED", "PIPELINE_READY"),
                                ("pipeline→greeting_start", "PIPELINE_READY", "GREETING_PLAY_START"),
                                ("greeting_start→first_media", "GREETING_PLAY_START", "FIRST_MEDIA_SENT"),
                                ("answered→first_media_sent", "CALL_ANSWERED", "FIRST_MEDIA_SENT")]:
                    v = _d(a, b)
                    if v is not None:
                        parts.append(f"{n} {v:.2f}s")
                parts.append(f"first_utterance_media_duration {dur:.2f}s")
                step("27-STARTUP-TIMING", " | ".join(parts))
        await self.push_frame(frame, direction)


class UserSpeechLogger(FrameProcessor):
    """[STEP 19-USER-SAID] — every final owner utterance from Sarvam STT."""

    def __init__(self, turn_times=None):
        super().__init__()
        self._turn_times = turn_times

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            if self._turn_times:
                self._turn_times.mark("STT_FINAL")
                self._turn_times.last_user_text = frame.text
            step("19-USER-SAID", frame.text)
        await self.push_frame(frame, direction)


class BotSpeechLogger(FrameProcessor):
    """[STEP 20-BOT-SAID] — what the agent actually spoke via Bulbul TTS."""

    def __init__(self, turn_times=None):
        super().__init__()
        self._turn_times = turn_times
        self._buf = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        tt = self._turn_times
        if isinstance(frame, TTSStartedFrame) and tt:
            if "TTS_REQUEST_START" not in tt.marks:
                tt.mark("TTS_REQUEST_START")
        elif isinstance(frame, TTSAudioRawFrame) and tt:
            if ("TTS_REQUEST_START" in tt.marks
                    and "TTS_FIRST_AUDIO" not in tt.marks):
                tt.mark("TTS_FIRST_AUDIO")
        elif isinstance(frame, BotStartedSpeakingFrame) and tt:
            tt.bot_speaking = True
            if tt.suppress_next_bot_start > 0:
                tt.suppress_next_bot_start -= 1  # filler clip, not the response
            else:
                tt.mark("BOT_AUDIO_START")
                tt.emit_if_complete()
        elif isinstance(frame, BotStoppedSpeakingFrame) and tt:
            tt.bot_speaking = False
            tt.last_bot_audio_end = time.time()
        if isinstance(frame, TTSTextFrame):
            self._buf.append(frame.text)
        elif isinstance(frame, TTSStoppedFrame) and self._buf:
            step("20-BOT-SAID", " ".join(t.strip() for t in self._buf if t.strip()))
            self._buf = []
        elif isinstance(frame, UserStartedSpeakingFrame) and self._buf:
            step("20-BOT-SAID", " ".join(t.strip() for t in self._buf if t.strip())
                 + "  ⚡(owner interrupted)")
            self._buf = []
        await self.push_frame(frame, direction)


def _extract_transcript(context: LLMContext) -> list:
    """Pull a clean [{role, content}] transcript out of the LLM context."""
    try:
        messages = context.get_messages()
    except Exception:
        messages = getattr(context, "messages", []) or []
    out = []
    for m in messages:
        role = m.get("role") if isinstance(m, dict) else getattr(m, "role", "")
        if role not in ("user", "assistant"):
            continue
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", "")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts.append(p.get("text", ""))
            text = " ".join(parts)
        if text.strip():
            out.append({"role": role, "content": text.strip()})
    return out


async def _summarize(call_sid: str, transcript: list, state: CallState):
    """Post-call: store transcript + short English summary on the call doc."""
    await db.update_call(call_sid, {"transcript": transcript, "ended_at": db.now()})
    step("31-TRANSCRIPT-SAVED", f"{len(transcript)} turns stored in MongoDB")
    if not transcript or not config.ANTHROPIC_API_KEY:
        return
    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=config.ANTHROPIC_API_KEY)
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in transcript)[-8000:]
        resp = await client.messages.create(
            model=config.ANTHROPIC_MODEL,
            max_tokens=450,
            messages=[{
                "role": "user",
                "content": (
                    "This is a phone call transcript (mostly Telugu) between a "
                    "LAMPOSE property-onboarding agent and a property owner. "
                    "Return ONLY a JSON object, no markdown fences, with keys: "
                    "summary (2-3 plain English sentences for the sales team: "
                    "owner situation, interest, next step), "
                    "score (integer 1-10, overall call quality), "
                    "opening_followed (bool: did the agent do identity check then "
                    "Google Maps hook then permission ask), "
                    "compliance_ok (bool: no guarantees, no earnings numbers, "
                    "no fake features promised), "
                    "owner_sentiment (one word: positive/neutral/annoyed/angry), "
                    "next_action (one short sentence).\n\n" + convo
                ),
            }],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        summary, scorecard = raw, {}
        try:
            cleaned = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            data = json.loads(cleaned)
            summary = data.pop("summary", raw)
            scorecard = data
        except (json.JSONDecodeError, AttributeError):
            pass
        await db.update_call(call_sid, {"summary": summary, "scorecard": scorecard})
        step("32-SUMMARY-SAVED", summary[:120])
        if scorecard:
            step("37-SCORECARD", f"score {scorecard.get('score', '?')}/10 | "
                 f"opening={scorecard.get('opening_followed')} "
                 f"compliance={scorecard.get('compliance_ok')} "
                 f"sentiment={scorecard.get('owner_sentiment')} | "
                 f"next: {scorecard.get('next_action', '')}")
        if state.lead_id and summary:
            await db.update_lead(state.lead_id, {"notes": summary[:600]})
    except Exception as e:
        logger.warning(f"Summary failed for {call_sid}: {e}")


async def run_call(runner_args: RunnerArguments):
    """Entry point per WebSocket connection from Twilio."""
    transport = await create_transport(runner_args, TRANSPORT_PARAMS)

    call_data = getattr(runner_args, "call_data", None)
    call_sid = getattr(call_data, "call_id", None) or ""
    call_doc = await db.get_call_by_sid(call_sid) if call_sid else None
    if call_doc is None:
        # SECURITY: only sessions for calls WE created (dialer) or that arrived
        # through the signed inbound webhook are allowed. Anything else is a
        # forged websocket session trying to burn STT/LLM/TTS credits.
        step("SECURITY", f"rejected unknown call session sid={call_sid or 'none'}")
        return
    global _ACTIVE_PIPELINES
    if _ACTIVE_PIPELINES >= config.MAX_ACTIVE_PIPELINES:
        step("SECURITY", f"rejected call sid={call_sid}: pipeline capacity "
             f"({_ACTIVE_PIPELINES}/{config.MAX_ACTIVE_PIPELINES}) exhausted")
        return
    _ACTIVE_PIPELINES += 1
    direction = (call_doc or {}).get("direction", "inbound")
    lead: Optional[dict] = None
    if call_doc and call_doc.get("lead_id"):
        lead = await db.get_lead(call_doc["lead_id"])

    ws_wall = time.time()
    step("13-CALL-CONTEXT", f"sid={call_sid} direction={direction} "
         f"lead={lead.get('phone') if lead else 'unknown'} "
         f"name={lead.get('name') if lead else '-'}")

    # --- services ---
    stt_settings = SarvamSTTService.Settings(model=config.STT_MODEL_NAME)
    if config.STT_LANGUAGE and not config.STT_MODEL_NAME.startswith("saaras:v2"):
        # Pin the language (saaras:v3 & saarika support it) — prevents short
        # sounds like "aa"/"haan" being auto-detected as Tamil/Punjabi/etc.
        try:
            stt_settings.language = Language(config.STT_LANGUAGE)
        except ValueError:
            stt_settings.language = config.STT_LANGUAGE
    stt = SarvamSTTService(
        api_key=config.SARVAM_API_KEY,
        mode=config.STT_MODE or None,
        settings=stt_settings,
    )
    step("14-STT-READY", f"Sarvam {config.STT_MODEL_NAME} streaming STT "
         f"(mode={config.STT_MODE or 'default'})")

    voice = (call_doc or {}).get("voice") or config.TTS_VOICE
    tts = SarvamTTSService(
        api_key=config.SARVAM_API_KEY,
        settings=SarvamTTSService.Settings(
            model=config.TTS_MODEL,
            voice=voice,
            language=Language.TE_IN,
            pace=config.TTS_PACE,
            # start synthesis sooner on short sentences; shrinks the audible
            # "stitched" gaps between sentences inside one agent turn
            min_buffer_size=30,
        ),
    )
    step("15-TTS-READY", f"Sarvam {config.TTS_MODEL} voice={voice} pace={config.TTS_PACE}")

    system_prompt = build_system_prompt(lead, direction)
    llm = AnthropicLLMService(
        api_key=config.ANTHROPIC_API_KEY,
        retry_timeout_secs=config.LLM_RETRY_TIMEOUT_SECS,
        retry_on_timeout=True,
        settings=AnthropicLLMService.Settings(
            model=config.ANTHROPIC_MODEL,
            system_instruction=system_prompt,
            enable_prompt_caching=True,
        ),
    )
    step("16-LLM-READY", f"Anthropic {config.ANTHROPIC_MODEL} "
         f"(system prompt {len(system_prompt)} chars, 8 tools)")

    state = CallState(call_sid=call_sid,
                      lead_id=(call_doc or {}).get("lead_id"),
                      direction=direction)
    greeting_state = {"sent": False}  # exactly one initial greeting per call
    turn_times = TurnTimes()
    turn_times.startup = {
        "CALL_ANSWERED": (call_doc or {}).get("answered_wall"),
        "WS_CONNECTED": ws_wall,
    }
    state.turn_times = turn_times
    stop_strategy = TeluguFastTurnStopStrategy(
        turn_analyzer=LocalSmartTurnAnalyzerV3(),
        turn_times=turn_times,
        incomplete_grace_secs=1.5,
        backchannel_after_secs=config.BACKCHANNEL_AFTER_SECS,
        backchannel_max_per_turn=config.BACKCHANNEL_MAX_PER_TURN,
    )
    context = LLMContext(tools=build_tools(state))
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(confidence=0.8, start_secs=0.35)
            ),
            # Barge-in only on 2+ transcribed words; noise/"హ్మ్" no longer
            # cuts the agent off. When the bot is silent, 1 word still works.
            user_turn_strategies=UserTurnStrategies(
                start=[MinWordsUserTurnStartStrategy(min_words=2)],
                # same smart-turn model, with two latency fixes: a transcript
                # arriving after speech end completes COMPLETE turns instantly,
                # and INCOMPLETE turns resolve 1.5s after TRUE speech end
                stop=[stop_strategy],
            ),
            # end the user's turn sooner when the smart-turn model is unsure
            # (it is English-trained and often says INCOMPLETE for Telugu)
            user_turn_stop_timeout=1.5,
        ),
    )

    usage = UsageTracker(turn_times)
    pipeline_stages = [
        transport.input(),
        stt,
    ]
    if config.EMOTION_ENABLED:
        pipeline_stages.append(EmotionMonitor(context, loud_rms=config.EMOTION_LOUD_RMS))
    audiobuffer = (AudioBufferProcessor(num_channels=2, auto_start_recording=True)
                   if config.RECORD_CALLS else None)
    bot_logger = BotSpeechLogger(turn_times)
    pipeline_stages += [
        UserSpeechLogger(turn_times),
        user_aggregator,
        llm,
        usage,
        tts,
        bot_logger,
        transport.output(),
        SentMediaMonitor(turn_times),
    ]
    if audiobuffer is not None:
        pipeline_stages.append(audiobuffer)
    pipeline_stages.append(assistant_aggregator)
    pipeline = Pipeline(pipeline_stages)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        idle_timeout_secs=getattr(runner_args, "pipeline_idle_timeout_secs", 300),
    )
    state.worker = worker

    filler_audio = FillerAudio(voice)
    if config.FILLER_ENABLED:
        asyncio.create_task(filler_audio.prewarm())  # clips ready before first use
    filler_used_turns: set = set()

    async def _filler_watchdog(tc: float):
        def _still_needed() -> bool:
            m = turn_times.marks
            return (m.get("TURN_COMPLETE") == tc
                    and "LLM_FIRST_TOKEN" not in m
                    and "LLM_FIRST_TEXT_FRAME" not in m
                    and tc not in filler_used_turns          # max one per turn
                    and not state.transferred
                    and not state.closing                    # no filler in goodbye
                    and not state.outcome                    # closing sequence begun
                    and not stop_strategy._own_vad_speaking  # user talking
                    and not turn_times.bot_speaking)         # bot already talking
        await asyncio.sleep(config.FILLER_DELAY_SECS)
        if not _still_needed():
            return
        context_name, phrase = pick_phrase(
            turn_times.last_user_text,
            tool_recent=(time.time() - turn_times.last_tool_wall) < 4.0,
        )
        clip = await filler_audio.get(phrase)  # cached after prewarm
        if clip is None:
            return
        # Final re-check right before injection — a first token arriving now
        # cancels the filler entirely (nothing was queued yet).
        if not _still_needed():
            step("23-FILLER-SKIPPED", "LLM responded during final check — filler cancelled")
            return
        filler_used_turns.add(tc)
        turn_times.suppress_next_bot_start += 1
        step("23-FILLER", f"LLM quiet {config.FILLER_DELAY_SECS}s — context="
             f"{context_name}: {phrase} ({len(clip)//16}ms pre-synth clip)")
        # Out-of-band injection directly before the output transport: raw PCM,
        # bypasses LLM context and the Sarvam TTS websocket completely, so it
        # can never interleave with or trail a real response.
        CHUNK = 320  # 20ms @ 8kHz s16 mono
        for i in range(0, len(clip), CHUNK):
            await bot_logger.push_frame(
                SpeechOutputAudioRawFrame(clip[i:i + CHUNK], 8000, 1))

    if config.FILLER_ENABLED:
        turn_times.on_turn_complete = (
            lambda tc: asyncio.create_task(_filler_watchdog(tc))
        )

    def _speak_backchannel():
        phrase = random.choice(config.BACKCHANNEL_PHRASES)
        step("24-BACKCHANNEL", f"owner speaking at length — ack: {phrase}")
        asyncio.create_task(worker.queue_frames([TTSSpeakFrame(phrase)]))

    if config.BACKCHANNEL_ENABLED:
        stop_strategy.on_backchannel = _speak_backchannel

    turn_times.startup["PIPELINE_READY"] = time.time()
    turn_times.startup["PROVIDERS_READY"] = time.time()  # services constructed above
    step("17-PIPELINE-READY", "audio pipeline built, waiting for Twilio audio")

    from pipecat.workers.runner import WorkerRunner

    runner = WorkerRunner(handle_sigint=False, force_gc=True)

    if audiobuffer is not None:

        @audiobuffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            if not audio:
                return
            try:
                RECORDINGS_DIR.mkdir(exist_ok=True)
                path = RECORDINGS_DIR / f"{call_sid or 'unknown'}.wav"

                def _write():
                    with io.BytesIO() as buf:
                        with wave.open(buf, "wb") as wf:
                            wf.setsampwidth(2)
                            wf.setnchannels(num_channels)
                            wf.setframerate(sample_rate)
                            wf.writeframes(audio)
                        path.write_bytes(buf.getvalue())

                await asyncio.to_thread(_write)
                size_kb = path.stat().st_size // 1024
                await db.update_call(call_sid, {"recording": path.name})
                step("38-RECORDING-SAVED", f"{path.name} ({size_kb} KB, "
                     f"stereo: owner=left, agent=right)")
            except Exception as e:
                logger.error(f"Recording save failed for {call_sid}: {e}")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        t_live = time.time()
        step("18-CALL-LIVE", f"audio stream connected (direction={direction})")
        # (recording auto-starts with the processor — no pre-start race)
        if state.lead_id:
            await db.update_lead(state.lead_id, {"status": "in_call"})
        await db.update_call(call_sid, {"status": "in-progress"})
        if direction == "inbound":
            # Inbound: unchanged — the LLM composes the inbound greeting.
            context.add_message({
                "role": "developer",
                "content": "The call just connected. Greet the caller now as instructed.",
            })
            await worker.queue_frames([LLMRunFrame()])
            step("18-GREETING", "inbound call — agent greets first")
        else:
            # Outbound/test: WE placed the call, so WE speak first — from the
            # PRE-GENERATED clip cached at dial time (no LLM, no live TTS on
            # the critical path). Bookkeeping happens AFTER audio is queued.
            if greeting_state["sent"]:
                step("18-INITIAL-GREETING", "duplicate call-start event — ignored")
                return
            greeting_state["sent"] = True
            greeting = build_opening_line(lead)
            context.add_message({"role": "assistant", "content": greeting})
            clip = await filler_audio.get(greeting) if greeting_state.get("cache_ok", True) else None
            turn_times.startup["GREETING_READY"] = time.time()
            turn_times.startup["GREETING_PLAY_START"] = time.time()
            if clip:
                step("18-INITIAL-GREETING",
                     f"cached greeting playing (call_live_to_greeting="
                     f"{time.time() - t_live:.2f}s): {greeting}")
                # Through the WORKER QUEUE, not a direct push: queued frames
                # are processed strictly after StartFrame, so they cannot be
                # dropped by the not-started guard (the VPS silent-greeting bug).
                CHUNK = 320
                frames = [SpeechOutputAudioRawFrame(clip[i:i + CHUNK], 8000, 1)
                          for i in range(0, len(clip), CHUNK)]
                await worker.queue_frames(frames)
                turn_times.startup["FIRST_AUDIO_SENT"] = time.time()
                step("20-BOT-SAID", f"{greeting}  (pre-generated clip)")
            else:
                step("18-INITIAL-GREETING",
                     f"cache MISS — live TTS greeting (call_live_to_greeting="
                     f"{time.time() - t_live:.2f}s): {greeting}")
                await worker.queue_frames([TTSSpeakFrame(greeting)])
            step("18-WAITING", "initial greeting playing — waiting for owner response")

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        step("30-CALL-DISCONNECTED", f"audio stream closed sid={call_sid}")
        await runner.cancel()

    try:
        await runner.add_workers(worker)
        await runner.run()
    finally:
        # --- post-call bookkeeping ---
        try:
            transcript = _extract_transcript(context)
            await _summarize(call_sid, transcript, state)
            if state.lead_id and not state.transferred:
                lead_now = await db.get_lead(state.lead_id) or {}
                if lead_now.get("status") in ("in_call", "dialing"):
                    # LLM never recorded an outcome (hangup / drop)
                    await db.update_lead(state.lead_id, {
                        "status": "contacted" if transcript else "retry",
                        "last_outcome": "call ended without recorded outcome",
                    })
        except Exception as e:
            logger.error(f"Post-call bookkeeping failed for {call_sid}: {e}")
        global _session_usd
        call_usd = usage.cost_usd
        _session_usd += call_usd
        lat_avg = {k: round(sum(v) / len(v), 3) for k, v in usage.latency.items() if v}
        if lat_avg:
            step("35-LATENCY-AVG", " | ".join(
                f"{k} avg {v:.2f}s (n={len(usage.latency[k])})" for k, v in lat_avg.items()))
        turn_avg = turn_times.averages()
        if turn_avg:
            step("36-TURN-TIMING-AVG", " | ".join(f"{k} {v:.2f}s" for k, v in turn_avg.items()))
        await db.update_call(call_sid, {"latency": lat_avg, "turn_timing": turn_avg})
        await db.update_call(call_sid, {"llm_usage": {
            "input_tokens": usage.prompt,
            "output_tokens": usage.completion,
            "cache_read": usage.cache_read,
            "cache_write": usage.cache_write,
            "cost_usd": round(call_usd, 4),
        }})
        step("34-CLAUDE-USAGE",
             f"this call: ${call_usd:.4f} (~₹{call_usd * USD_TO_INR:.2f}) | "
             f"fresh in={usage.prompt:,} cached={usage.cache_read:,} "
             f"out={usage.completion:,} | "
             f"session total: ${_session_usd:.3f} (~₹{_session_usd * USD_TO_INR:.1f})")
        _ACTIVE_PIPELINES = max(0, _ACTIVE_PIPELINES - 1)
        step("33-CALL-DONE", f"sid={call_sid} outcome={state.outcome or 'none recorded'} "
             f"reason={state.reason_code or '-'} transferred={state.transferred}")
