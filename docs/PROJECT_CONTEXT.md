# LAMPOSE Voice Agent — full project context

Written 1 September 2026, at the end of the build-and-tune phase, so that this
project can be picked up cold — by a new session, or by you after a break.

---

## 1. What this is

A Telugu-speaking AI phone agent ("Kavya") that calls property owners — PG,
hostel, To-Let, hotel — and onboards them onto LAMPOSE, a stay-booking
platform. It handles both directions, qualifies the owner, pitches, answers
objections, books callbacks, records outcomes, and transfers hot leads to a
human on **+91 9398334115**.

Target volume is 100–500 calls/day. Conversation design comes from the
"LAMPOSE 30-Day Owner Acquisition & Booking Manual".

Everything is self-hosted on the user's own VPS. Nothing runs on a third-party
voice-agent platform, deliberately: control, Telugu quality, and roughly a
quarter of the cost.

---

## 2. Current status

**Working and proven on real calls:** call setup, personalised Google-Maps
opening, barge-in, objection handling, callback scheduling, outcome + reason
codes, stereo recording, per-call cost logging, automatic scorecards, and the
whole audio path (this took the longest — see §6).

**Latency:** median ~2.2 s from end-of-speech to first audio; best turns 1.6 s.
Good commercial agents run 1.0–1.5 s. A competitor measured head-to-head in
this project ran 1.5 s. Usable, noticeably behind best-in-class — see §9.

**Never tested in production:** `transfer_to_sales` (hot-lead handoff), the
callback loop end-to-end, and anything at concurrency > 1. The auto-dialer has
never run a real batch — `DIALER_ENABLED=false`.

**Known-imperfect:** conversation scorecards sit at 5–6/10. The machinery works;
whether the script actually converts owners is still unproven.

---

## 3. Architecture

```
Twilio PSTN  ──8 kHz μ-law──▶  /ws  ──▶  Pipecat pipeline  ──▶  Twilio
                                            │
   transport.input()                        │  the order matters
     → Sarvam STT (saaras:v3, te-IN pinned) │
     → EmotionMonitor (voice energy)        │
     → UserSpeechLogger  ── echo / backchannel / closing filters
     → user context aggregator              │
     → Claude Haiku 4.5 (+ 8 tools)         │
     → UsageTracker (cost + latency marks)  │
     → Sarvam TTS (bulbul:v3)               │
     → BotSpeechLogger  ── greeting + filler audio injected HERE
     → OutputGain (−5 dB)                   │
     → transport.output()  ── ambient bed mixed in by the transport
     → SentMediaMonitor  ── what was really delivered
     → AudioBufferProcessor (stereo recording)
     → assistant context aggregator
```

**Why the injection point matters:** pipecat's `STTService` transcribes *any*
audio frame it sees, including our own. Greeting audio queued at the pipeline
source was fed to Sarvam STT as if the owner had spoken it, which triggered a
false barge-in that killed the greeting. Bot audio therefore enters at
`bot_logger`, downstream of the STT.

---

## 4. File map

| File | Responsibility |
|---|---|
| `app/bot.py` | The per-call pipeline. Service setup, all custom processors, greeting, filler, post-call summary + scorecard. The heart of the system. |
| `app/turn_taking.py` | `TurnTimes` (latency marks) and `TeluguFastTurnStopStrategy` — custom end-of-turn detection. |
| `app/tools.py` | `CallState` + the 8 tools Claude can call. |
| `app/prompts.py` | System prompt, opening scripts, objection playbook, hard rules. **Changes here change what real owners hear.** |
| `app/main.py` | FastAPI: dashboard, leads/CSV, test call, recordings, Audio Check, voice samples, Twilio webhooks, `/ws`. |
| `app/dialer.py` | Outbound dialling loop, retries, callbacks, status callbacks. |
| `app/db.py` | MongoDB access (leads, calls). |
| `app/voices.py` | 44-voice catalogue, 10 expressiveness presets, cached samples. |
| `app/ambient.py` | Background beds — real recordings + synthesized fallback + upload conversion. |
| `app/telugu_text.py` | Last-mile spoken-text clean-up (digits, half-transliterated tokens). |
| `app/filler.py` | Pre-synthesized clips (greeting + fillers), disk-cached. |
| `app/static/dashboard.html` | The whole dashboard, single file. |
| `deploy/` | systemd unit, nginx site, VPS setup script. |
| `tests/` | Six suites — see §10. |

---

## 5. Key decisions, and the evidence behind them

- **Sarvam over ElevenLabs/Google/Azure.** Benchmarks put Bulbul v3 ahead for
  Indian languages, and it is Telugu-native. Round-trip testing confirmed it
  pronounces our English loanwords correctly inside Telugu.
- **Claude Haiku 4.5 with prompt caching.** ~₹1.5–1.8 per call. Caching cut
  12k fresh tokens per turn to ~50.
- **Greet-first with a pre-synthesized clip.** The greeting is generated while
  the phone rings, so audio starts ~0.3 s after connect instead of ~2 s.
- **Custom turn-taking.** Sarvam's transcript p99 is 1.17 s; the stock strategy
  waited for it. Ours fires as soon as the turn analyser says COMPLETE, which
  took `stt→turn` from 0.97 s to 0.00 s.
- **English stays in Latin script.** Round-trip test: 97% vs 98% for Telugu
  spelling — no meaningful difference, so rewriting was dropped.
- **Pace 1.0, not 1.15.** Measured, 1.15 speaks 27% faster than the recording.
- **Ambient is opt-in and capped at 0.25.** At 1.00 a real call was destroyed:
  her words garbled and the owner's handset echoed the bed into our STT.

---

## 6. Gotchas that cost real time

Each of these produced a broken production call before it was understood.

1. **`create_transport()` parses the Twilio handshake.** Reading
   `runner_args.call_data` before calling it yields `sid=none`, and the
   security guard then rejects every call. Parse explicitly with
   `parse_telephony_websocket()` first — it is cached and safe to call twice.
2. **`STTService` transcribes any audio frame**, including our own output.
3. **Sarvam TTS `min_buffer_size` floor is 30.** At 10 the socket closes
   silently and the entire call is mute (call ACVPS5).
4. **Short sentences hang** until 30 characters accumulate — a natural short
   opener waited 0.9–1.7 s. Fixed by flushing, which then created gotcha 5.
5. **Each flush emits a `final`**, which pipecat treats as end-of-context, so
   every sentence after a flushed one was dropped (ACVPS8).
   `SarvamTTSFlushShort` counts and swallows its own finals.
6. **Frame types are load-bearing.** Only `TTSAudioRawFrame` and
   `SpeechOutputAudioRawFrame` are speech; plain `OutputAudioRawFrame` is the
   ambient bed. Counting all of them inflated a 3.3 s greeting to 6.3 s.
7. **NLTK `punkt_tab`** must be installed system-wide (`/usr/local/share/nltk_data`)
   because the service user is sandboxed; without it Telugu chunks mid-grapheme.
8. **RNNoise (`NOISE_FILTER=true`) crashes** the input task on this pipecat
   version. Keep it false.
9. **Sarvam sync STT refuses audio over 30 s** — Audio Check splits long
   stretches at their quietest point.
10. **A slot leak blocks everything.** `run_call` holds one of
    `MAX_ACTIVE_PIPELINES`; a set-up failure used to leak it permanently, and
    six leaks would refuse every future call. Now released in a `finally`.

---

## 7. Conversation design

The system prompt (`app/prompts.py`) carries identity, voice rules, LAMPOSE
facts, a compliance NEVER-list, the call flow, an objection playbook, and
**seven hard rules** — each added after a specific call went wrong:

1. Never ask for a phone number by voice; only confirm the current one.
2. Accept "I'll call you back" the first time; never re-pitch.
3. Call `schedule_callback` the moment a time is mentioned.
4. One fixed answer on commercial terms; never invent assurances.
5. Never claim an action is already done ("WhatsApp లో message పెట్టాను" was
   said on a real call without the tool ever being called).
6. One fixed answer when asked for our number.
7. "సరే థాంక్యూ" after a pitch means the owner is closing the call.

Plus a spoken-length limit: **never more than ~8 seconds at a stretch**. The
opening scripts were measured through Sarvam and cut to fit (13.7 s → 7.4 s,
13.4 s → 6.6 s).

**Tools:** `record_qualification`, `capture_property_details`,
`request_whatsapp_details`, `schedule_callback`, `set_lead_outcome`,
`transfer_to_sales`, `mark_do_not_contact`, `end_call`.

**Outcomes:** hot / warm / cold / lost with R-codes (R01 no interest, R02 no
vacancy, R06 wants WhatsApp, R07 needs partner approval, R14 callback set,
R16 do-not-contact…). R06 and R14 are rejected unless the matching tool
actually ran, and a post-call grader assigns an outcome if the agent recorded
none.

---

## 8. Voice and audio settings

| Setting | Value | Notes |
|---|---|---|
| Voice | `kavya` | 44 available — audition in the Voices tab |
| Pace | 1.0 | 1.15 = 27% faster than recorded |
| Expressiveness | `warm` (0.45) | 10 presets over `temperature`, real range 0.01–1.0 |
| Output gain | −5 dB | was peaking at −0.6 dBFS |
| Ambient | off | beds: quiet/office/call_center/cafe/street; ≤0.25 |
| Sample rate | 8 kHz | fixed by PSTN — the hard ceiling on quality |

**Note for the custom-model plan:** the swap-in point is
`SarvamTTSFlushShort` in `app/bot.py` (streaming path) plus `app/filler.py`
(pre-synthesized clips) and `app/voices.py` (samples). Anything that speaks
8 kHz mono PCM and streams can replace Sarvam without touching the pipeline.

---

## 9. Latency

Measured median **~2.2 s** end-of-speech → first audio.

| Stage | Time |
|---|---|
| speech end → final transcript | 0.35–0.55 s |
| turn decision | 0.00 s (already optimal) |
| → Claude's first token | 0.70–0.95 s |
| → first complete sentence | 0.30 s |
| → first audio from Sarvam | 0.25 s |

Intermittently `stt→turn` costs 0.97 s when the turn model judges a sentence
incomplete. Three levers, in order: (1) find out how much of Claude's time is
India→US network and consider a Mumbai region, (2) lower the filler threshold
so she *feels* instant, (3) tune the incomplete-grace — at the cost of cutting
owners off.

---

## 10. Tests

Six suites, all must pass before pushing:

| Suite | Covers |
|---|---|
| `test_turn_taking.py` | 9 end-of-turn scenarios |
| `test_speech_filters.py` | echo guard, backchannel, closing; speech-only duration |
| `test_call_session.py` | Twilio handshake ordering, per-call settings, slot accounting |
| `test_ambient.py` | bed quality, mixer compatibility, volume ceiling, Audio Check impact |
| `test_audio_check.py` | segmentation and the 30 s STT limit |
| `test_voice_text.py` | spoken-text clean-up, expressiveness presets |

---

## 11. Operations

**Deploy** (the user runs this; there is no SSH access from the assistant side):

```bash
cd /var/www/lampose-ai-voice-agent && git pull && chown -R lampose:lampose . \
  && systemctl restart lampose-voice
```

**Watch a call:** `journalctl -u lampose-voice -f | grep STEP`
**Errors:** `journalctl -u lampose-voice --since "10 min ago" | grep -v STEP`
**Deep log:** `logs/agent.log` (DEBUG, rotates at 20 MB, keeps 10)

The step log is the primary debugging tool: `19-USER-SAID` / `20-BOT-SAID` for
the conversation, `22-TURN-TIMING` for latency, `AUDIO-COMPLETE` for delivery,
`34-CLAUDE-USAGE` for cost, `37-SCORECARD` for quality.

**Audio Check** (dashboard tab) is the ground truth: drop a recording in and it
transcribes each channel separately into a timestamped chat. If the log says
she said something and Audio Check disagrees, believe Audio Check.

**Infrastructure:** Ubuntu 24.04 VPS in Kolkata, 2 vCPU / 8 GB, MongoDB 8 local,
systemd `lampose-voice` (Restart=always), nginx + certbot on voice.lampose.com,
private GitHub repo `Hosanna-Mosa/lampose-ai-voice-agent`.

---

## 12. Open items before real calling

| # | Item | Why it matters |
|---|---|---|
| 1 | Rotate Twilio token, Sarvam key, dashboard password | All were pasted into chat; the dashboard password is still the default |
| 2 | Nightly `mongodump` | Leads, transcripts and outcomes have no backup |
| 3 | Recording retention | ~1 GB/day at 500 calls; the disk fills and takes MongoDB with it |
| 4 | Indian caller ID | A US number calling Indian mobiles answers poorly and reads as spam. Indian telemarketing rules need checking by someone qualified |
| 5 | 3-concurrent load test | Never run above one call at a time |
| 6 | One live transfer + one live callback | The two money paths have never executed |
| 7 | Verify the latest conversation fixes on a call | Shipped, not yet observed live |

---

## 13. Roadmap

- **Custom Telugu TTS model** (in progress, ~5 days) — replaces Sarvam TTS at
  the seam described in §8.
- **Dashboard build-out** (next 5 days) — the current dashboard has Overview,
  Leads, Calls, Recordings, Audio Check, Voices, Test Call. Natural additions:
  a live-calls view, campaign management, outcome analytics, scorecard trends,
  a prompt/script editor with versioning, per-lead call history, and A/B
  comparison of voice or script variants.
- **Latency** — see §9.

---

## 14. Working agreements

- The user runs every VPS command themselves; they do not share SSH access.
- One step at a time, not a batch of instructions.
- Ask before changing prompts; they affect real owners.
- Secrets never enter git. `.env` is gitignored; `.env.example` documents keys.
