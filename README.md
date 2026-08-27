# LAMPOSE Voice AI — Telugu Owner-Acquisition Call Agent

AI voice agent that calls PG / hostel / To-Let / hotel owners in **natural
Telugu**, pitches LAMPOSE, qualifies them, handles objections, collects
onboarding details, schedules callbacks, and live-transfers hot leads to the
sales team. Handles **outbound + inbound** on your Twilio number.

## Stack

| Layer | Tech |
|---|---|
| Telephony | Twilio Programmable Voice + Media Streams |
| Orchestration | Pipecat 1.7 (Python 3.11+) |
| STT (Telugu) | Sarvam **Saaras realtime** (streaming WebSocket) |
| Brain | **Claude Haiku 4.5** (Anthropic) — swap via `ANTHROPIC_MODEL` |
| TTS (Telugu) | Sarvam **Bulbul v3** (streaming, voice picker on dashboard) |
| Data | MongoDB (leads, calls, transcripts) |
| Server | FastAPI + uvicorn behind nginx (TLS) on your KVM VPS |

```
Owner's phone ↔ Twilio ↔ wss://voice.lampose.in/ws ↔ Pipecat pipeline
                                     STT → Claude(+tools) → TTS
                                            │
             MongoDB ← leads/calls/transcripts/callbacks
             Dashboard: https://voice.lampose.in  (Basic auth)
```

## Deploy (one time, ~10 minutes)

1. **DNS**: add A-record `voice.lampose.in` → your VPS IP.
2. **.env**: fill `ANTHROPIC_API_KEY` (console.anthropic.com) and change
   `DASHBOARD_PASSWORD`. Twilio + Sarvam keys are already set.
3. Copy this folder to the VPS and run:
   ```bash
   scp -r . root@YOUR_VPS_IP:/root/lampose-voice
   ssh root@YOUR_VPS_IP
   cd /root/lampose-voice && sudo bash deploy/setup_vps.sh
   ```
   Installs Python 3.11/3.12, MongoDB 7, nginx + Let's Encrypt TLS,
   creates the `lampose-voice` systemd service, opens the firewall.
4. **Twilio console** → Phone Numbers → your number → Voice Configuration:
   - A call comes in → Webhook → `https://voice.lampose.in/twiml/inbound` (POST)
5. **Twilio console** → Voice → Settings → Geo Permissions → enable **India**.

## Use

- Open `https://voice.lampose.in` (login from `.env`).
- **Test Call** tab → your number + a voice (kavya/shreya/pooja/ritu female,
  shubh/gokul/aditya/rohan male) → the agent calls you. Audition and set the
  winner as `TTS_VOICE` in `.env`, then `systemctl restart lampose-voice`.
- **Leads** tab → add manually or upload CSV
  (`phone,name,property_name,property_type,area,notes`).
- **Overview** tab → switch the dialer **ON** to auto-call new/retry/callback
  leads during calling hours (10:00–19:00 IST, 3 concurrent, 3 attempts,
  4h retry gap — all tunable in `.env`).
- **Calls** tab → live statuses, Telugu transcripts, English summaries.

### Platform integration (when your Node backend is ready)
POST new owners to the same API the dashboard uses:
```bash
curl -u lampose:PASSWORD https://voice.lampose.in/api/leads \
  -H 'Content-Type: application/json' \
  -d '{"phone":"9876543210","name":"Ramesh","property_type":"PG","area":"Gachibowli","source":"platform"}'
```

## Ops

```bash
journalctl -u lampose-voice -f        # live logs
systemctl restart lampose-voice       # after .env changes
mongosh lampose_voice                 # inspect data
```

## Costs (approx, per connected minute)
Twilio US→India ~$0.03–0.10 · Sarvam STT+TTS ~₹2–6 · Claude Haiku ~₹0.5–1.
At 2–3 min avg and 300 calls/day expect roughly ₹3–8k/day all-in; watch the
first week's bills and tune.

## Security notes
- `.env` is chmod 600 and git-ignored. **Rotate the Twilio auth token and
  Sarvam key after go-live** (they were shared in chat during setup).
- Dashboard + API are HTTP Basic-auth protected; Twilio webhooks are open
  endpoints by design (add signature validation before scaling up).
- Caller ID shows +1 (US). If pickup rates suffer, port the same stack to
  Exotel/Plivo India numbers — only the telephony layer changes.

## Conversation design
See `docs/telugu_script.md` — greeting, pitch, qualifying questions, the full
objection playbook and compliance rules (from the 30-Day Owner Acquisition
Manual). The live system prompt is `app/prompts.py`.

## Local testing (before VPS deploy)

```bash
bash scripts/run_local.sh
```
This starts MongoDB, opens a **Cloudflare quick tunnel** (no signup), points the
Twilio number's inbound webhook at it, and starts the server with step-by-step
logs. Dashboard: the printed tunnel URL, or http://localhost:7860.
`.env.local` overrides `.env` on the laptop only (dialer stays OFF locally).

### Step-log map (what you should see, in order)

| Step | Meaning |
|---|---|
| `01-SERVER-START → 04-READY` | server, MongoDB, dialer loop up |
| `TEST-CALL` / `MANUAL-DIAL` | a call was requested from the dashboard |
| `10-DIAL-START / 10-DIAL-CREATED` | Twilio accepted — phone rings |
| `11-TWIML-OUTBOUND/INBOUND` | call answered, Twilio asked how to connect |
| `12-WS-CONNECTED` | audio websocket open |
| `13-CALL-CONTEXT → 17-PIPELINE-READY` | lead loaded; STT, TTS, LLM ready |
| `18-CALL-LIVE` | two-way audio flowing |
| `19-USER-SAID` / `20-BOT-SAID` | live conversation, line by line |
| `TOOL-…` | agent actions (qualify, property, callback, transfer, outcome…) |
| `30…33` | hangup, transcript saved, English summary, final outcome |

Full debug detail (Sarvam/Twilio internals): `logs/agent.log`.
