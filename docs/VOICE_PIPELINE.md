# Voice pipeline — working notes

The call pipeline only: Twilio audio in, Telugu conversation, audio out.
Built and tuned over the session listed under *Session continuity* below.

Sister documents: `PROJECT_CONTEXT.md` (whole-project history, decisions and
open items) and, as they are written, one file per area — the dashboard build
belongs in its own, not here.

## Stack

Twilio Media Streams (8 kHz μ-law) → Pipecat 1.7.0 → Sarvam `saaras:v3` STT
(te-IN) → Claude Haiku 4.5 (prompt caching on) → Sarvam `bulbul:v3` TTS →
MongoDB + FastAPI dashboard. Python 3.12 in `venv/`.

## Commands

```bash
# tests — all six must pass before pushing
for t in tests/test_*.py; do PYTHONPATH=. ./venv/bin/python $t; done

# the user deploys by pulling on the VPS (they run it, no SSH access here)
cd /var/www/lampose-ai-voice-agent && git pull && chown -R lampose:lampose . \
  && systemctl restart lampose-voice

# what happened on a call
journalctl -u lampose-voice -f | grep STEP
journalctl -u lampose-voice --since "10 min ago" | grep -v STEP   # errors
```

## House rules learned the hard way

- **Verify every edit anchor.** A silently-failed `.replace()` once shipped a
  broken handler to production. Assert before writing.
- **Measure, don't assume.** Several confident hypotheses in this project were
  wrong (English words were fine; the ambient bed wasn't the cause; the docs'
  temperature range is wrong). Probe the real API before building on a belief.
- **One step at a time for the user.** They run every VPS command by hand and
  asked explicitly for a single instruction per message, not a batch.
- **Never claim a call behaviour is fixed until a real call proves it.** Every
  fix in this project is named after the call that exposed it (ACVPS6, 8, 10…).
- Ask before changing `app/prompts.py` — it changes what a real owner hears.

## Session continuity

The whole build-and-tune history lives in one Claude Code session:

```
13767859-a0f3-4121-aae1-ad50e2f99ec3
```

```bash
claude --resume 13767859-a0f3-4121-aae1-ad50e2f99ec3   # this exact session
claude --resume        # pick from a list for this folder
claude --continue      # the most recent session here
```

`/resume` switches session from inside a running one; the VS Code panel has the
same picker. The transcript is a plain file at
`~/.claude/projects/-Users-hosanna-LAMPOSE-ai-voice--1/<session-id>.jsonl` —
worth copying somewhere safe, since clearing `~/.claude` deletes it.

A resumed conversation is summarised as it grows, so fine detail compresses.
**These docs, not the transcript, are the durable record** — if something
matters later, write it here rather than relying on the chat.

## Landmines (each cost hours)

- `create_transport()` is what parses Twilio's handshake and fills
  `runner_args.call_data`. Read `call_data` before it and the CallSid is
  empty — every call gets rejected as an unknown session.
- Pipecat's `STTService` feeds **any** `AudioRawFrame` to the STT. Inject bot
  audio *downstream* of the STT (`bot_logger.push_frame`), never at the source.
- Sarvam TTS `min_buffer_size` floor is **30**. Lower values make Sarvam close
  the socket silently and the call goes mute.
- Every `{"type":"flush"}` produces a `final` event, which pipecat treats as
  end-of-context. `SarvamTTSFlushShort` counts and swallows its own.
- Only `TTSAudioRawFrame` / `SpeechOutputAudioRawFrame` are speech. Plain
  `OutputAudioRawFrame` is ambient bed — don't count it as spoken audio.
- Sarvam sync STT rejects audio over 30 s; long segments must be split.
- `temperature` really tops out at **1.0** (the docs say 2.0).
