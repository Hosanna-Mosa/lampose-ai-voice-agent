"""MongoDB access layer (motor, async).

Collections
-----------
leads:  one document per property owner.
    { name, phone (E.164, unique), property_name, property_type, area, notes,
      status: new|queued|dialing|in_call|hot|warm|cold|lost|dnc|callback|retry|
              onboarding_started|failed,
      reason_code, attempts, next_attempt_at, callback_at, callback_note,
      wants_whatsapp, whatsapp_number, qualification{}, property_details{},
      last_outcome, source, created_at, updated_at }

calls: one document per phone call.
    { call_sid (unique), lead_id, direction: outbound|inbound|test,
      phone, status, voice, started_at, ended_at, duration,
      transcript: [ {role, content} ], summary, outcome }
"""

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app import config

_client: Optional[AsyncIOMotorClient] = None


def db():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(config.MONGO_URL, tz_aware=True)
    return _client[config.MONGO_DB]


async def init_indexes():
    await db().leads.create_index("phone", unique=True)
    await db().leads.create_index("status")
    await db().calls.create_index("call_sid", unique=True)
    await db().calls.create_index("lead_id")


def now() -> datetime:
    return datetime.now(timezone.utc)


def oid(value: str) -> ObjectId:
    return ObjectId(value)


def serialize(doc: Any) -> Any:
    """Make a Mongo document JSON-safe."""
    if isinstance(doc, list):
        return [serialize(d) for d in doc]
    if isinstance(doc, dict):
        return {k: serialize(v) for k, v in doc.items()}
    if isinstance(doc, ObjectId):
        return str(doc)
    if isinstance(doc, datetime):
        return doc.isoformat()
    return doc


# ---------- leads ----------

async def upsert_lead(data: dict) -> dict:
    """Insert a lead by phone, or update existing one. Returns the lead."""
    phone = data["phone"]
    existing = await db().leads.find_one({"phone": phone})
    if existing:
        update = {k: v for k, v in data.items() if v not in (None, "")}
        update["updated_at"] = now()
        await db().leads.update_one({"_id": existing["_id"]}, {"$set": update})
        return await db().leads.find_one({"_id": existing["_id"]})
    doc = {
        "name": data.get("name", ""),
        "phone": phone,
        "property_name": data.get("property_name", ""),
        "property_type": data.get("property_type", ""),
        "area": data.get("area", ""),
        "rating": data.get("rating", ""),
        "notes": data.get("notes", ""),
        "status": data.get("status", "new"),
        "reason_code": "",
        "attempts": 0,
        "next_attempt_at": now(),
        "callback_at": None,
        "callback_note": "",
        "wants_whatsapp": False,
        "whatsapp_number": "",
        "qualification": {},
        "property_details": {},
        "last_outcome": "",
        "source": data.get("source", "manual"),
        "created_at": now(),
        "updated_at": now(),
    }
    result = await db().leads.insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


async def get_lead(lead_id) -> Optional[dict]:
    if isinstance(lead_id, str):
        lead_id = ObjectId(lead_id)
    return await db().leads.find_one({"_id": lead_id})


async def find_lead_by_phone(phone: str) -> Optional[dict]:
    return await db().leads.find_one({"phone": phone})


async def update_lead(lead_id, update: dict):
    if isinstance(lead_id, str):
        lead_id = ObjectId(lead_id)
    update["updated_at"] = now()
    await db().leads.update_one({"_id": lead_id}, {"$set": update})


async def list_leads(status: Optional[str] = None, limit: int = 500) -> list:
    q = {"status": status} if status else {}
    cursor = db().leads.find(q).sort("updated_at", -1).limit(limit)
    return [d async for d in cursor]


# ---------- calls ----------

async def create_call(call_sid: str, lead_id, direction: str, phone: str,
                      voice: str = "", status: str = "initiated",
                      ambient: str = "", ambient_volume=None,
                      pace=None, temperature=None) -> dict:
    doc = {
        "call_sid": call_sid,
        "lead_id": lead_id,
        "direction": direction,
        "phone": phone,
        "voice": voice,
        # background sound for THIS call: "" = follow config, "off" = none,
        # otherwise a bed name (lets the dashboard audition beds live)
        "ambient": ambient,
        "ambient_volume": ambient_volume,
        # voice tuning for THIS call (None = follow config)
        "pace": pace,
        "temperature": temperature,
        "status": status,
        "started_at": now(),
        "ended_at": None,
        "duration": None,
        "transcript": [],
        "summary": "",
        "outcome": "",
    }
    await db().calls.insert_one(doc)
    return doc


async def get_call_by_sid(call_sid: str) -> Optional[dict]:
    return await db().calls.find_one({"call_sid": call_sid})


async def update_call(call_sid: str, update: dict):
    await db().calls.update_one({"call_sid": call_sid}, {"$set": update})


async def list_calls(limit: int = 200) -> list:
    cursor = db().calls.find({}).sort("started_at", -1).limit(limit)
    return [d async for d in cursor]


async def count_active_calls() -> int:
    return await db().calls.count_documents(
        {"status": {"$in": ["initiated", "ringing", "in-progress"]}}
    )


# ---------- stats (PDF Part 49 daily dashboard) ----------

async def stats() -> dict:
    leads_total = await db().leads.count_documents({})
    by_status: dict = {}
    async for row in db().leads.aggregate(
        [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]
    ):
        by_status[row["_id"] or "unknown"] = row["n"]
    calls_total = await db().calls.count_documents({})
    calls_completed = await db().calls.count_documents({"status": "completed"})
    return {
        "leads_total": leads_total,
        "leads_by_status": by_status,
        "calls_total": calls_total,
        "calls_completed": calls_completed,
    }
