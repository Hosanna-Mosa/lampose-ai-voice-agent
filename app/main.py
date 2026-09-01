"""LAMPOSE Voice AI server.

- Dashboard + REST API (HTTP Basic auth)
- Twilio TwiML + status webhooks
- WebSocket endpoint for Twilio Media Streams -> Pipecat bot
- Background outbound dialer
"""

import array
import asyncio
import csv
import io
import json
import re
import secrets
import wave

import aiohttp
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, UploadFile, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from loguru import logger
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, Stream, VoiceResponse

from app import ambient, config, db, dialer
from app.logsetup import setup_logging, step

setup_logging()

STATIC_DIR = Path(__file__).parent / "static"

# ---------------------------------------------------------------- auth

security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    ok_user = secrets.compare_digest(credentials.username, config.DASHBOARD_USER)
    ok_pass = bool(config.DASHBOARD_PASSWORD) and secrets.compare_digest(
        credentials.password, config.DASHBOARD_PASSWORD
    )
    if not (ok_user and ok_pass):
        raise HTTPException(status_code=401, detail="Unauthorized",
                            headers={"WWW-Authenticate": "Basic"})
    return credentials.username


# ---------------------------------------------------------------- app

@asynccontextmanager
async def lifespan(app: FastAPI):
    step("01-SERVER-START", f"public URL: {config.SERVER_URL} | port {config.PORT} | "
         f"LLM: {config.ANTHROPIC_MODEL} | voice: {config.TTS_VOICE}")
    await db.init_indexes()
    step("02-DB-READY", f"MongoDB connected ({config.MONGO_URL}/{config.MONGO_DB})")
    try:
        await asyncio.to_thread(ambient.prewarm)   # ~0.4s, cached on disk after
        step("02-AMBIENT-READY", f"background beds ready ({', '.join(ambient.available())}) | "
             f"default={'off' if not config.AMBIENT_ENABLED else config.AMBIENT_SOUND} "
             f"volume={config.AMBIENT_VOLUME}")
    except Exception as e:
        step("02-AMBIENT-READY", f"background sound unavailable: {e}")
    task = asyncio.create_task(dialer.dialer_loop())
    step("04-READY", f"dashboard: {config.SERVER_URL}  |  waiting for calls")
    yield
    task.cancel()


app = FastAPI(title="LAMPOSE Voice AI", lifespan=lifespan)


def validate_indian_mobile(phone: str) -> str:
    """Return the normalized number or raise a clear 400 for bad input."""
    if not re.fullmatch(r"\+91[6-9]\d{9}", phone):
        digits = re.sub(r"\D", "", phone or "")
        raise HTTPException(400, (
            f"'{phone}' is not a valid Indian mobile number "
            f"(got {len(digits.removeprefix('91'))} digits — need 10, starting with 6-9). "
            "Example: 9398334115"
        ))
    return phone


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"[^\d+]", "", (raw or "").strip())
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return "+" + digits if digits else ""


# ---------------------------------------------------------------- dashboard

@app.get("/", response_class=HTMLResponse)
async def dashboard(user: str = Depends(require_auth)):
    return (STATIC_DIR / "dashboard.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- leads API

@app.get("/api/leads")
async def api_list_leads(status: Optional[str] = None, user: str = Depends(require_auth)):
    return JSONResponse(db.serialize(await db.list_leads(status)))


@app.post("/api/leads")
async def api_add_lead(payload: dict, user: str = Depends(require_auth)):
    """Add or update one lead. This same endpoint is the future webhook for
    the LAMPOSE platform backend."""
    phone = validate_indian_mobile(normalize_phone(payload.get("phone", "")))
    lead = await db.upsert_lead({
        "phone": phone,
        "name": payload.get("name", ""),
        "property_name": payload.get("property_name", ""),
        "property_type": payload.get("property_type", ""),
        "area": payload.get("area", ""),
        "rating": payload.get("rating", ""),
        "notes": payload.get("notes", ""),
        "source": payload.get("source", "api"),
    })
    step("LEAD-ADDED", f"{phone} ({payload.get('name') or 'no name'}) source={payload.get('source', 'api')}")
    return JSONResponse(db.serialize(lead))


@app.post("/api/leads/csv")
async def api_upload_csv(file: UploadFile, user: str = Depends(require_auth)):
    """CSV columns (header row required): phone, name, property_name,
    property_type, area, rating, notes — only phone is mandatory."""
    content = (await file.read()).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(content))
    added, skipped = 0, 0
    for row in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        phone = normalize_phone(row.get("phone") or row.get("mobile") or row.get("number") or "")
        if not re.fullmatch(r"\+91[6-9]\d{9}", phone):
            skipped += 1
            continue
        await db.upsert_lead({
            "phone": phone,
            "name": row.get("name", ""),
            "property_name": row.get("property_name", ""),
            "property_type": row.get("property_type", ""),
            "area": row.get("area", ""),
            "rating": row.get("rating", ""),
            "notes": row.get("notes", ""),
            "source": "csv",
        })
        added += 1
    step("CSV-IMPORT", f"{added} leads added, {skipped} rows skipped")
    return {"added": added, "skipped": skipped}


@app.patch("/api/leads/{lead_id}")
async def api_update_lead(lead_id: str, payload: dict, user: str = Depends(require_auth)):
    allowed = {k: v for k, v in payload.items() if k in
               ("name", "property_name", "property_type", "area", "notes", "status")}
    await db.update_lead(lead_id, allowed)
    return {"ok": True}


@app.post("/api/leads/{lead_id}/call")
async def api_call_now(lead_id: str, user: str = Depends(require_auth)):
    lead = await db.get_lead(lead_id)
    if not lead:
        raise HTTPException(404, "lead not found")
    validate_indian_mobile(lead["phone"])
    step("MANUAL-DIAL", f"dashboard requested call to {lead['phone']}")
    await db.update_lead(lead["_id"], {"attempts": int(lead.get("attempts", 0)) + 1})
    try:
        sid = await dialer.dial_lead(lead)
    except Exception as e:
        raise HTTPException(400, f"Twilio rejected the call: {e}")
    return {"call_sid": sid}


# ---------------------------------------------------------------- calls API

@app.get("/api/calls")
async def api_list_calls(user: str = Depends(require_auth)):
    calls = await db.list_calls()
    for c in calls:
        c.pop("transcript", None)
    return JSONResponse(db.serialize(calls))


@app.get("/api/calls/{call_sid}")
async def api_get_call(call_sid: str, user: str = Depends(require_auth)):
    call = await db.get_call_by_sid(call_sid)
    if not call:
        raise HTTPException(404, "call not found")
    return JSONResponse(db.serialize(call))


async def _sarvam_transcribe_wav(wav_bytes: bytes) -> str:
    """Transcribe one mono WAV via Sarvam REST (saarika, Telugu)."""
    form = aiohttp.FormData()
    form.add_field("file", wav_bytes, filename="chunk.wav", content_type="audio/wav")
    form.add_field("model", "saarika:v2.5")
    form.add_field("language_code", "te-IN")
    # Sarvam REST errors used to be swallowed (r.json() on an error body has no
    # "transcript") — every segment came back empty and the page reported
    # "0 speech segments" as if the recording were silent. Now: retry 429/5xx
    # with backoff, and raise with the real reason otherwise.
    last = ""
    for attempt in range(4):
        async with aiohttp.ClientSession() as s:
            async with s.post("https://api.sarvam.ai/speech-to-text",
                              headers={"api-subscription-key": config.SARVAM_API_KEY},
                              data=form, timeout=aiohttp.ClientTimeout(total=60)) as r:
                body = await r.text()
                if r.status == 200:
                    return (json.loads(body).get("transcript") or "").strip()
                last = f"HTTP {r.status}: {body[:160]}"
        if r.status == 429 or r.status >= 500:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        break
    raise RuntimeError(last)


def _mono_wav(samples, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _otsu_threshold(rms) -> float:
    """Split window loudness into background vs speech.

    A fixed threshold breaks once a channel has a noise floor — with ambient
    background sound the agent channel is never digitally silent, and the whole
    call collapses into one giant "speech" segment. A fixed percentile is no
    better: it assumes how much of the call is speech, and our talk share
    swings from 15% to 90%. Otsu's method finds the split that best separates
    the two loudness modes whatever their proportions, so it holds for a quiet
    caller, a talkative agent, and any ambient level. Measured against real
    recordings: stable to 0.89 IoU with a bed underneath (fixed threshold: 0.38)
    and identical to the old behaviour on clean audio.
    """
    import numpy as np
    loudness = np.log10(rms + 1.0)          # dB-like: modes are separable here
    hist, edges = np.histogram(loudness, bins=64)
    below = np.cumsum(hist)
    total = below[-1]
    if total == 0:
        return 0.0
    centres = (edges[:-1] + edges[1:]) / 2
    cum = np.cumsum(hist * centres)
    above = total - below
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_below = cum / below
        mean_above = (cum[-1] - cum) / above
        variance = below * above * (mean_below - mean_above) ** 2
    variance[~np.isfinite(variance)] = 0
    return float(10 ** centres[int(np.argmax(variance))] - 1.0)


def _speech_segments(ch, sr: int):
    """Energy-based speech segments for one channel: [(start_s, end_s), ...]."""
    import numpy as np
    x = np.asarray(ch, dtype=np.float32)
    W = int(sr * 0.02)
    m = len(x) // W * W
    if m == 0:
        return []
    rms = np.sqrt((x[:m].reshape(-1, W) ** 2).mean(axis=1))
    active = rms > max(350.0, _otsu_threshold(rms))
    segs, s = [], None
    for i, v in enumerate(active):
        if v and s is None:
            s = i
        elif not v and s is not None:
            segs.append([s * 0.02, i * 0.02]); s = None
    if s is not None:
        segs.append([s * 0.02, len(active) * 0.02])
    # merge close bursts, drop blips
    merged = []
    for a, b in segs:
        if merged and a - merged[-1][1] < 0.8:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(max(0.0, a - 0.1), b + 0.15) for a, b in merged if b - a >= 0.35]


@app.post("/api/audio-check")
async def api_audio_check(file: UploadFile, user: str = Depends(require_auth)):
    """Ground-truth chat reconstruction of a call recording: per-channel speech
    segments (owner=left ch, agent=right ch) transcribed individually via
    Sarvam STT and interleaved chronologically with timestamps."""
    raw = await file.read()
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(400, "file too large (60MB max)")
    try:
        with wave.open(io.BytesIO(raw)) as wf:
            nch, sw, sr = wf.getnchannels(), wf.getsampwidth(), wf.getframerate()
            n = wf.getnframes()
            pcm = wf.readframes(n)
    except Exception:
        raise HTTPException(400, "only WAV files are supported (use the app's recordings)")
    if sw != 2:
        raise HTTPException(400, f"expected 16-bit samples, got {sw*8}-bit")
    samples = array.array("h", pcm[: len(pcm) // 2 * 2])
    duration = n / sr
    if nch == 2:
        channels = [("owner", samples[0::2]), ("agent", samples[1::2])]
    else:
        channels = [("mono", samples)]

    jobs = []
    for speaker, ch in channels:
        for a, b in _speech_segments(ch, sr):
            piece = ch[int(a * sr):int(b * sr)]
            jobs.append({"speaker": speaker, "start": a, "end": b,
                         "wav": _mono_wav(piece, sr)})

    sem = asyncio.Semaphore(2)  # gentle on Sarvam's REST rate limit
    failures = []

    async def _run(job):
        async with sem:
            try:
                job["text"] = await _sarvam_transcribe_wav(job["wav"])
            except Exception as e:
                job["text"] = f"[transcription failed: {e}]"
                failures.append(str(e))
        del job["wav"]

    await asyncio.gather(*[_run(j) for j in jobs])
    segments = [{"speaker": j["speaker"], "start": round(j["start"], 1),
                 "end": round(j["end"], 1), "text": j["text"]}
                for j in sorted(jobs, key=lambda j: j["start"]) if j["text"]]
    step("AUDIO-CHECK", f"{file.filename}: {duration:.1f}s, {nch}ch, "
         f"{len(jobs)} speech segments found, {len(segments)} transcribed"
         + (f", {len(failures)} FAILED — first: {failures[0][:120]}" if failures else ""))
    return {"filename": file.filename, "duration": round(duration, 1),
            "sample_rate": sr, "segments": segments}


@app.get("/api/recordings")
async def api_recordings_list(user: str = Depends(require_auth)):
    """All calls that have a saved recording, newest first, with metadata."""
    rec_dir = Path(__file__).parent.parent / "recordings"
    out = []
    cursor = db.db().calls.find({"recording": {"$exists": True, "$ne": ""}}).sort("started_at", -1).limit(200)
    async for c in cursor:
        path = rec_dir / c["recording"]
        size = path.stat().st_size if path.exists() else 0
        lead = await db.get_lead(c["lead_id"]) if c.get("lead_id") else None
        out.append({
            "call_sid": c["call_sid"],
            "phone": c.get("phone", ""),
            "direction": c.get("direction", ""),
            "started_at": c.get("started_at"),
            "duration": c.get("duration"),
            "outcome": c.get("outcome", ""),
            "summary": c.get("summary", ""),
            "scorecard": c.get("scorecard") or {},
            "size_kb": size // 1024,
            "exists": path.exists(),
            "lead_name": (lead or {}).get("name", ""),
            "property_name": (lead or {}).get("property_name", ""),
        })
    return JSONResponse(db.serialize([r for r in out if r["exists"]]))


@app.get("/api/recordings/{call_sid}")
async def api_recording(call_sid: str, user: str = Depends(require_auth)):
    path = Path(__file__).parent.parent / "recordings" / f"{call_sid}.wav"
    if not re.fullmatch(r"CA[0-9a-f]{32}", call_sid) or not path.exists():
        raise HTTPException(404, "no recording for this call")
    return FileResponse(path, media_type="audio/wav", filename=f"{call_sid}.wav")


@app.post("/api/test-call")
async def api_test_call(payload: dict, user: str = Depends(require_auth)):
    """Call any number with a chosen voice, to audition voices / test the agent."""
    phone = validate_indian_mobile(normalize_phone(payload.get("phone", "")))
    voice = (payload.get("voice") or config.TTS_VOICE).strip().lower()
    amb = (payload.get("ambient") or "").strip().lower()
    if amb and amb != "off" and amb not in ambient.BEDS:
        raise HTTPException(400, f"unknown background sound '{amb}'")
    amb_vol = payload.get("ambient_volume")
    amb_vol = max(0.0, min(1.0, float(amb_vol))) if amb_vol not in (None, "") else None
    # upsert with the personalization fields so the opening uses them
    lead = await db.upsert_lead({
        "phone": phone,
        "name": payload.get("name", ""),
        "property_name": payload.get("property_name", ""),
        "property_type": payload.get("property_type", ""),
        "area": payload.get("area", ""),
        "rating": payload.get("rating", ""),
        "source": "test",
    })
    step("TEST-CALL", f"audition call to {phone} voice '{voice}' "
         f"property='{payload.get('property_name', '')}'")
    try:
        sid = await dialer.dial_lead(lead, direction="test", voice=voice,
                                     ambient=amb, ambient_volume=amb_vol)
    except Exception as e:
        raise HTTPException(400, f"Twilio rejected the call: {e}")
    return {"call_sid": sid, "voice": voice, "ambient": amb or "(config default)"}


# ---------------------------------------------------------------- dialer API

@app.get("/api/dialer")
async def api_dialer_status(user: str = Depends(require_auth)):
    return {
        "enabled": dialer.is_enabled(),
        "in_hours": dialer.in_calling_hours(),
        "active_calls": await db.count_active_calls(),
        "max_concurrent": config.MAX_CONCURRENT_CALLS,
        "hours": f"{config.CALLING_HOURS_START}:00-{config.CALLING_HOURS_END}:00 IST",
    }


@app.post("/api/dialer")
async def api_dialer_toggle(payload: dict, user: str = Depends(require_auth)):
    await dialer.set_enabled(bool(payload.get("enabled")))
    return {"enabled": dialer.is_enabled()}


@app.get("/api/stats")
async def api_stats(user: str = Depends(require_auth)):
    return await db.stats()


@app.get("/api/config")
async def api_config(user: str = Depends(require_auth)):
    return {
        "voices": config.TEST_VOICES,
        "default_voice": config.TTS_VOICE,
        "twilio_number": config.TWILIO_NUMBER,
        "transfer_number": config.SALES_TRANSFER_NUMBER,
        "model": config.ANTHROPIC_MODEL,
        "ambient_beds": [{"name": n, "label": ambient.BEDS[n]} for n in ambient.available()],
        "ambient_enabled": config.AMBIENT_ENABLED,
        "ambient_default": config.AMBIENT_SOUND,
        "ambient_volume": config.AMBIENT_VOLUME,
    }


@app.get("/api/ambient/{name}.wav")
async def api_ambient_preview(name: str, volume: Optional[float] = None,
                              user: str = Depends(require_auth)):
    """Audition a background bed at exactly the level callers hear it, with a
    cached line of Kavya's voice over it so the balance is judgeable."""
    try:
        path = ambient.bed_path(name)          # also the name whitelist
    except KeyError:
        raise HTTPException(404, "unknown background sound")
    vol = config.AMBIENT_VOLUME if volume is None else max(0.0, min(1.0, volume))
    with wave.open(str(path)) as wf:
        sr = wf.getframerate()
        bed = array.array("h", wf.readframes(min(wf.getnframes(), 10 * sr)))
    mixed = array.array("h", (int(v * vol) for v in bed))
    # Overlay a voice clip we already have on disk (no new TTS calls).
    clips = sorted(Path(__file__).parent.parent.glob("filler_cache/*.pcm"),
                   key=lambda f: f.stat().st_size, reverse=True)
    if clips:
        voice_pcm = array.array("h", clips[0].read_bytes())
        gain = 10 ** (config.OUTPUT_GAIN_DB / 20)      # same level as on a call
        for i, v in enumerate(voice_pcm):
            j = i + sr                                  # start 1s in
            if j >= len(mixed):
                break
            mixed[j] = max(-32768, min(32767, mixed[j] + int(v * gain)))
    return Response(content=_mono_wav(mixed, sr), media_type="audio/wav")


# ---------------------------------------------------------------- Twilio webhooks

_twilio_validator = RequestValidator(config.TWILIO_AUTH_TOKEN)


async def _require_twilio_signature(request: Request, form) -> None:
    """403 unless the request carries a valid X-Twilio-Signature."""
    if not config.TWILIO_VALIDATE:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    url = config.SERVER_URL + request.url.path
    if not _twilio_validator.validate(url, dict(form), signature):
        step("SECURITY", f"rejected unsigned Twilio webhook: {request.url.path}")
        raise HTTPException(403, "invalid Twilio signature")



def _stream_twiml() -> str:
    ws_url = config.SERVER_URL.replace("https://", "wss://").replace("http://", "ws://")
    response = VoiceResponse()
    connect = Connect()
    connect.append(Stream(url=f"{ws_url}/ws"))
    response.append(connect)
    response.pause(length=10)
    return str(response)


@app.post("/twiml/outbound")
async def twiml_outbound(request: Request):
    form = await request.form()
    await _require_twilio_signature(request, form)
    sid = form.get("CallSid", "")
    if sid:
        import time as _t
        await db.update_call(sid, {"answered_wall": _t.time()})
    step("11-TWIML-OUTBOUND", f"Twilio asked for call instructions "
         f"(sid={form.get('CallSid', '?')}, status={form.get('CallStatus', '?')}) "
         f"— answered! connecting audio stream")
    return HTMLResponse(content=_stream_twiml(), media_type="application/xml")


@app.post("/twiml/inbound")
async def twiml_inbound(request: Request):
    form = await request.form()
    await _require_twilio_signature(request, form)
    call_sid = form.get("CallSid", "")
    from_number = normalize_phone(form.get("From", ""))
    lead = None
    if from_number:
        lead = await db.find_lead_by_phone(from_number) or await db.upsert_lead({
            "phone": from_number, "source": "inbound", "status": "new",
        })
    if call_sid and not await db.get_call_by_sid(call_sid):
        await db.create_call(call_sid, lead["_id"] if lead else None,
                             "inbound", from_number)
    step("11-TWIML-INBOUND", f"incoming call from {from_number} sid={call_sid} "
         f"— connecting audio stream")
    return HTMLResponse(content=_stream_twiml(), media_type="application/xml")


@app.post("/twilio/status")
async def twilio_status(request: Request):
    raw_form = await request.form()
    await _require_twilio_signature(request, raw_form)
    form = dict(raw_form)
    step("STATUS", f"{form.get('CallSid', '?')} -> {form.get('CallStatus', '?')}"
         + (f" ({form.get('CallDuration')}s)" if form.get('CallDuration') else ""))
    await dialer.handle_status_callback(form)
    return {"ok": True}


# ---------------------------------------------------------------- media stream WS

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    from pipecat.runner.types import WebSocketRunnerArguments

    from app.bot import run_call

    await websocket.accept()
    step("12-WS-CONNECTED", "Twilio media stream websocket accepted — starting the agent")
    try:
        runner_args = WebSocketRunnerArguments(websocket=websocket)
        await run_call(runner_args)
    except Exception as e:
        logger.exception(f"Error in call session: {e}")
        try:
            await websocket.close()
        except Exception:
            pass


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=config.PORT)
