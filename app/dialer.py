"""Outbound call queue: paces calls, respects calling hours & concurrency,
handles retries and scheduled callbacks."""

import asyncio
from datetime import datetime, timedelta

from loguru import logger
from twilio.rest import Client as TwilioClient

from app import config, db
from app.filler import FillerAudio
from app.logsetup import step
from app.prompts import build_opening_line


async def _pregen_greeting(lead: dict, voice: str):
    try:
        clip = await FillerAudio(voice or config.TTS_VOICE).get(build_opening_line(lead))
        if clip:
            step("09-GREETING-PREGEN", f"greeting audio cached before answer "
                 f"({len(clip)//16}ms, voice={voice or config.TTS_VOICE})")
    except Exception as e:
        logger.warning(f"greeting pregen failed: {e}")

_enabled: bool = config.DIALER_ENABLED_DEFAULT


def _twilio() -> TwilioClient:
    return TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def is_enabled() -> bool:
    return _enabled


async def set_enabled(value: bool):
    global _enabled
    _enabled = value
    await db.db().settings.update_one(
        {"_id": "dialer"}, {"$set": {"enabled": value}}, upsert=True
    )
    logger.info(f"Dialer enabled={value}")


async def load_state():
    global _enabled
    doc = await db.db().settings.find_one({"_id": "dialer"})
    if doc is not None:
        _enabled = bool(doc.get("enabled", _enabled))


def in_calling_hours() -> bool:
    now_ist = datetime.now(config.TZ)
    return config.CALLING_HOURS_START <= now_ist.hour < config.CALLING_HOURS_END


async def dial_lead(lead: dict, direction: str = "outbound", voice: str = "") -> str:
    """Originate one call to a lead. Returns the Twilio call SID."""
    phone = lead["phone"]

    def _create():
        return _twilio().calls.create(
            to=phone,
            from_=config.TWILIO_NUMBER,
            url=f"{config.SERVER_URL}/twiml/outbound",
            method="POST",
            status_callback=f"{config.SERVER_URL}/twilio/status",
            status_callback_method="POST",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            timeout=30,
        )

    step("10-DIAL-START", f"calling {phone} from {config.TWILIO_NUMBER} ({direction})")
    # call preparation: greeting audio is synthesized while the phone rings
    asyncio.create_task(_pregen_greeting(lead, voice))
    call = await asyncio.to_thread(_create)
    await db.create_call(call.sid, lead["_id"], direction, phone, voice=voice)
    await db.update_lead(lead["_id"], {"status": "dialing"})
    step("10-DIAL-CREATED", f"Twilio accepted, call_sid={call.sid} — phone should ring now")
    return call.sid


async def _due_leads(limit: int) -> list:
    now = db.now()
    cursor = db.db().leads.find({
        "$or": [
            {"status": {"$in": ["new", "queued", "retry"]},
             "next_attempt_at": {"$lte": now},
             "attempts": {"$lt": config.MAX_ATTEMPTS}},
            # owner-requested callbacks always ring, regardless of attempts
            {"status": "callback", "callback_at": {"$lte": now}},
        ],
    }).sort("next_attempt_at", 1).limit(limit)
    return [d async for d in cursor]


async def dialer_loop():
    """Background task; started from the FastAPI lifespan."""
    await load_state()
    step("03-DIALER-LOOP", f"auto-dialer polling every 20s (enabled={_enabled}, "
         f"hours {config.CALLING_HOURS_START}-{config.CALLING_HOURS_END} IST, "
         f"max {config.MAX_CONCURRENT_CALLS} concurrent)")
    while True:
        try:
            await asyncio.sleep(20)
            if not _enabled or not in_calling_hours():
                continue
            active = await db.count_active_calls()
            capacity = config.MAX_CONCURRENT_CALLS - active
            if capacity <= 0:
                continue
            due = await _due_leads(capacity)
            if due:
                step("DIALER-BATCH", f"{len(due)} due lead(s), capacity {capacity}")
            for lead in due:
                try:
                    await db.update_lead(lead["_id"], {
                        "attempts": int(lead.get("attempts", 0)) + 1,
                    })
                    await dial_lead(lead)
                except Exception as e:
                    logger.error(f"Dial failed for {lead.get('phone')}: {e}")
                    await db.update_lead(lead["_id"], {
                        "status": "retry",
                        "next_attempt_at": db.now() + timedelta(hours=config.RETRY_DELAY_HOURS),
                    })
                await asyncio.sleep(2)  # pacing between originations
        except asyncio.CancelledError:
            logger.info("Dialer loop stopped")
            return
        except Exception as e:
            logger.error(f"Dialer loop error: {e}")


async def handle_status_callback(form: dict):
    """Process Twilio call status webhooks (retries on no-answer etc.)."""
    call_sid = form.get("CallSid", "")
    status = form.get("CallStatus", "")
    if not call_sid:
        return
    update = {"status": status}
    if form.get("CallDuration") is not None:
        try:
            update["duration"] = int(form["CallDuration"])
        except (TypeError, ValueError):
            pass
    terminal = status in ("completed", "busy", "no-answer", "failed", "canceled")
    if terminal:
        update["ended_at"] = db.now()
    await db.update_call(call_sid, update)

    if not terminal:
        return
    call = await db.get_call_by_sid(call_sid)
    if not call or not call.get("lead_id") or call.get("direction") == "test":
        return
    lead = await db.get_lead(call["lead_id"])
    if not lead:
        return
    if status in ("busy", "no-answer", "failed", "canceled"):
        if int(lead.get("attempts", 0)) >= config.MAX_ATTEMPTS:
            step("21-GAVE-UP", f"{lead['phone']} {status} after {lead.get('attempts')} attempts")
            await db.update_lead(lead["_id"], {"status": "failed", "reason_code": "R17"})
        else:
            step("21-RETRY-SET", f"{lead['phone']} {status} — retry in {config.RETRY_DELAY_HOURS}h")
            await db.update_lead(lead["_id"], {
                "status": "retry",
                "next_attempt_at": db.now() + timedelta(hours=config.RETRY_DELAY_HOURS),
            })
    elif status == "completed" and lead.get("status") in ("dialing",):
        # Connected at Twilio level but bot never got an outcome
        await db.update_lead(lead["_id"], {"status": "contacted"})
