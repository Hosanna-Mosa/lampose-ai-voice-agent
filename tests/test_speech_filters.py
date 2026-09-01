"""What reaches the LLM, and what the delivery log claims was spoken.

UserSpeechLogger decides which owner transcripts become turns. Three filters
sit there, each added after a real call went wrong:
  * echo guard      — our own voice coming back down the line
  * backchannel     — "ఆ ఆ" / "సరే సరే" while Kavya is mid-sentence
  * closing         — anything said after the goodbye (ACVPS12: two goodbyes)

Run: PYTHONPATH=. ./venv/bin/python tests/test_speech_filters.py
"""

import asyncio
import os
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger                                          # noqa: E402
logger.remove()

from pipecat.frames.frames import (BotStartedSpeakingFrame,        # noqa: E402
                                   BotStoppedSpeakingFrame, OutputAudioRawFrame,
                                   SpeechOutputAudioRawFrame, TranscriptionFrame,
                                   TTSAudioRawFrame)
from pipecat.pipeline.pipeline import Pipeline                     # noqa: E402
from pipecat.pipeline.runner import PipelineRunner                 # noqa: E402
from pipecat.pipeline.task import PipelineTask                     # noqa: E402
from pipecat.processors.frame_processor import FrameProcessor      # noqa: E402

from app import bot                                                # noqa: E402
from app.turn_taking import TurnTimes                              # noqa: E402

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"
_results = []


def check(name, cond, detail=""):
    _results.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


class Sink(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.heard = []

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame):
            self.heard.append(frame.text)
        await self.push_frame(frame, direction)


async def run_filters(script):
    """script: list of (setup_fn, text). Returns what reached the LLM."""
    tt = TurnTimes()
    sink = Sink()
    task = PipelineTask(Pipeline([bot.UserSpeechLogger(tt), sink]))

    async def drive():
        await asyncio.sleep(0.2)
        for setup, text in script:
            setup(tt)
            await task.queue_frames([TranscriptionFrame(text, "owner", "2026-09-01")])
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.2)
        await task.cancel()

    asyncio.get_running_loop().create_task(drive())
    await asyncio.wait_for(PipelineRunner(handle_sigint=False).run(task), timeout=20)
    return sink.heard


def speaking(tt):
    tt.bot_speaking, tt.closing = True, False


def listening(tt):
    tt.bot_speaking, tt.closing = False, False


def closing(tt):
    tt.bot_speaking, tt.closing = False, True


def echoing(tt):
    tt.bot_speaking, tt.closing = True, False
    tt.note_bot_text("హలో నమస్తే సర్! మీరు Suma owner గారేనా?")


print("\nWhich owner transcripts become turns")
heard = asyncio.run(run_filters([
    (listening, "అవును చెప్పండి."),                      # a real answer
    (speaking,  "ఆ ఆ ఓకే."),                             # backchannel over her voice
    (speaking,  "ఆగండి సర్, నాకు వద్దు."),                # real interruption
    (echoing,   "హలో నమస్తే సార్ మీరు సుమా ఓనర్ గారేనా"),  # our own greeting echoing back
    (listening, "సరే సరే."),                             # ack AFTER she finished = an answer
    (closing,   "సరే బాయ్"),                             # after the goodbye
    (closing,   "ఒక్క నిమిషం ఆగండి"),                     # still after the goodbye
]))
check("a real answer gets through", "అవును చెప్పండి." in heard)
check("backchannel over her voice is ignored", "ఆ ఆ ఓకే." not in heard)
check("a real interruption still gets through", "ఆగండి సర్, నాకు వద్దు." in heard)
check("our own greeting echoing back is dropped",
      "హలో నమస్తే సార్ మీరు సుమా ఓనర్ గారేనా" not in heard)
check("'సరే సరే' after she finishes counts as an answer", "సరే సరే." in heard)
check("nothing after the goodbye starts a new turn (two-goodbye bug)",
      "సరే బాయ్" not in heard and "ఒక్క నిమిషం ఆగండి" not in heard,
      f"reached the LLM: {heard}")


print("\nAUDIO-COMPLETE reports speech, not background sound")


async def measure(frames):
    tt = TurnTimes()
    mon = bot.SentMediaMonitor(tt)
    task = PipelineTask(Pipeline([mon]))

    async def drive():
        await asyncio.sleep(0.2)
        await task.queue_frames(frames)
        await asyncio.sleep(0.2)
        await task.cancel()

    asyncio.get_running_loop().create_task(drive())
    await asyncio.wait_for(PipelineRunner(handle_sigint=False).run(task), timeout=20)
    return mon


chunk = b"\x00" * 320                       # 20 ms at 8 kHz
speech = [TTSAudioRawFrame(chunk, 8000, 1) for _ in range(50)]        # 1.0 s spoken
bed = [OutputAudioRawFrame(chunk, 8000, 1) for _ in range(150)]       # 3.0 s of bed only
mon = asyncio.run(measure([BotStartedSpeakingFrame()] + speech + bed + [BotStoppedSpeakingFrame()]))
check("1.0s of speech followed by 3.0s of bed logs as 1.0s",
      abs(mon._bytes / 16000 - 1.0) < 1e-6, f"logged {mon._bytes / 16000:.2f}s")
mon = asyncio.run(measure([BotStartedSpeakingFrame()]
                          + [SpeechOutputAudioRawFrame(chunk, 8000, 1) for _ in range(25)]
                          + [BotStoppedSpeakingFrame()]))
check("greeting and filler clips are still counted",
      abs(mon._bytes / 16000 - 0.5) < 1e-6, f"logged {mon._bytes / 16000:.2f}s")

print(f"\n{sum(_results)}/{len(_results)} checks passed")
sys.exit(0 if all(_results) else 1)
