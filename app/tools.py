"""LLM tools for the voice agent.

Tools are plain async closures (pipecat 1.x auto-generates their schemas
from signatures + docstrings) bound to a per-call CallState.
"""

import asyncio
import time
from datetime import datetime
from typing import Optional

from loguru import logger
from pipecat.frames.frames import EndWorkerFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallParams
from twilio.rest import Client as TwilioClient

from app import config, db
from app.logsetup import step


class CallState:
    """Mutable state shared by one call's tools."""

    def __init__(self, call_sid: str, lead_id, direction: str):
        self.call_sid = call_sid
        self.lead_id = lead_id
        self.direction = direction
        self.worker = None          # set by bot.py after worker creation
        self.outcome: str = ""
        self.reason_code: str = ""
        self.transferred = False
        self.dnc = False
        self.callback_scheduled_this_call = False
        self.closing = False       # goodbye in progress -> no fillers
        self.whatsapp_requested_this_call = False
        self.turn_times = None     # set by bot.py; used for audio drain


def _twilio() -> TwilioClient:
    return TwilioClient(config.TWILIO_ACCOUNT_SID, config.TWILIO_AUTH_TOKEN)


def build_tools(state: CallState) -> list:
    """Return the tool functions for one call."""

    async def record_qualification(
        params: FunctionCallParams,
        has_vacancy: bool,
        vacancy_count: int = 0,
        current_channels: str = "",
        interest_level: str = "",
    ):
        """Save qualification facts learned during the call. Call it as soon as you learn them.

        Args:
            has_vacancy: True if the owner currently has vacant rooms or beds.
            vacancy_count: Number of vacant rooms/beds if mentioned, else 0.
            current_channels: How the owner gets customers today (e.g. "WhatsApp, brokers").
            interest_level: One of "high", "medium", "low", "none".
        """
        step("TOOL-QUALIFY", f"vacancy={has_vacancy} count={vacancy_count} interest={interest_level} channels={current_channels}")
        if state.lead_id:
            await db.update_lead(state.lead_id, {"qualification": {
                "has_vacancy": has_vacancy,
                "vacancy_count": vacancy_count,
                "current_channels": current_channels,
                "interest_level": interest_level,
            }})
        await params.result_callback("Saved.")

    async def capture_property_details(
        params: FunctionCallParams,
        property_name: str = "",
        property_type: str = "",
        area: str = "",
        city: str = "",
        owner_name: str = "",
        vacant_units: str = "",
        approx_rent: str = "",
        extra_notes: str = "",
    ):
        """Save property onboarding details as the owner shares them. Call with whatever fields you have; leave unknown ones empty.

        Args:
            property_name: Name of the property.
            property_type: One of "PG", "hostel", "to-let", "hotel".
            area: Area / locality of the property.
            city: City.
            owner_name: Owner's name.
            vacant_units: Vacant rooms/beds description, e.g. "5 beds".
            approx_rent: Approximate rent as said, e.g. "6500 per bed".
            extra_notes: Any other useful detail (facilities, floors, etc.).
        """
        step("TOOL-PROPERTY", f"{property_name} {property_type} {area} {city} units={vacant_units} rent={approx_rent}")
        if state.lead_id:
            lead = await db.get_lead(state.lead_id) or {}
            details = dict(lead.get("property_details") or {})
            for k, v in [
                ("property_name", property_name), ("property_type", property_type),
                ("area", area), ("city", city), ("owner_name", owner_name),
                ("vacant_units", vacant_units), ("approx_rent", approx_rent),
                ("extra_notes", extra_notes),
            ]:
                if v:
                    details[k] = v
            top = {"property_details": details, "status": "onboarding_started"}
            if property_name:
                top["property_name"] = property_name
            if property_type:
                top["property_type"] = property_type.lower()
            if owner_name:
                top["name"] = owner_name
            if area:
                top["area"] = area
            await db.update_lead(state.lead_id, top)
        await params.result_callback("Saved. Continue collecting the remaining details one at a time.")

    async def request_whatsapp_details(
        params: FunctionCallParams,
        whatsapp_number: str = "",
    ):
        """Record that the owner wants LAMPOSE details / follow-up on WhatsApp.

        Args:
            whatsapp_number: The WhatsApp number the owner confirmed. Empty means the same number as this call.
        """
        step("TOOL-WHATSAPP", f"owner wants details on WhatsApp ({whatsapp_number or 'same number'})")
        state.whatsapp_requested_this_call = True
        if state.lead_id:
            await db.update_lead(state.lead_id, {
                "wants_whatsapp": True,
                "whatsapp_number": whatsapp_number,
            })
        await params.result_callback("Noted. Tell the owner the details will reach their WhatsApp shortly.")

    async def schedule_callback(
        params: FunctionCallParams,
        callback_time_iso: str,
        note: str = "",
    ):
        """Schedule a callback the owner asked for, at a specific time.

        Args:
            callback_time_iso: Callback time in ISO format with +05:30 offset, e.g. "2026-08-21T16:00:00+05:30".
            note: Short note about what to discuss on the callback.
        """
        step("TOOL-CALLBACK", f"requested for {callback_time_iso}: {note}")
        when: Optional[datetime] = None
        try:
            when = datetime.fromisoformat(callback_time_iso)
        except ValueError:
            pass
        if state.lead_id and when:
            state.callback_scheduled_this_call = True
            await db.update_lead(state.lead_id, {
                "status": "callback",
                "callback_at": when,
                "callback_note": note,
            })
            await params.result_callback("Callback scheduled. Confirm the time to the owner and wrap up politely.")
        else:
            await params.result_callback("Could not parse that time. Ask the owner for a clearer time.")

    async def transfer_to_sales(params: FunctionCallParams, reason: str):
        """Transfer this live call to a human LAMPOSE sales team member. Use only for clearly interested owners who need a person. Say the connecting line BEFORE calling this.

        Args:
            reason: One line on why you are transferring.
        """
        step("TOOL-TRANSFER", f"HOT lead -> transferring to {config.SALES_TRANSFER_NUMBER}: {reason}")
        state.transferred = True
        state.outcome = state.outcome or "hot"
        if state.lead_id:
            await db.update_lead(state.lead_id, {
                "status": "hot",
                "last_outcome": f"transferred: {reason}",
            })
        await db.update_call(state.call_sid, {"outcome": f"transferred: {reason}"})
        # Let the "connecting you now" line finish playing before we redirect.
        await asyncio.sleep(7)
        twiml = (
            f'<Response><Dial callerId="{config.TWILIO_NUMBER}" timeout="25">'
            f"{config.SALES_TRANSFER_NUMBER}</Dial></Response>"
        )
        try:
            await asyncio.to_thread(
                lambda: _twilio().calls(state.call_sid).update(twiml=twiml)
            )
            logger.info(f"Call {state.call_sid} transferred to {config.SALES_TRANSFER_NUMBER}")
            await params.result_callback("Transfer started. Say nothing more.")
        except Exception as e:
            logger.error(f"Transfer failed: {e}")
            await params.result_callback(
                "Transfer failed. Apologize, say the team will call them back soon, and continue."
            )

    async def mark_do_not_contact(params: FunctionCallParams, reason: str = ""):
        """Mark this owner as DO NOT CONTACT because they clearly asked for no further calls.

        Args:
            reason: Short note of what the owner said.
        """
        step("TOOL-DNC", f"marked DO NOT CONTACT: {reason}")
        state.dnc = True
        state.outcome = "lost"
        state.reason_code = "R16"
        if state.lead_id:
            await db.update_lead(state.lead_id, {
                "status": "dnc", "reason_code": "R16",
                "last_outcome": f"do-not-contact: {reason}",
            })
        await params.result_callback("Marked. Apologize briefly and end the call politely.")

    async def set_lead_outcome(
        params: FunctionCallParams,
        outcome: str,
        reason_code: str,
        notes: str = "",
    ):
        """Record the final result of this call. ALWAYS call this once before end_call.

        Args:
            outcome: One of "hot", "warm", "cold", "lost".
            reason_code: One code like R01, R02, R03, R04, R05, R06, R07, R08, R11, R12, R14, R15, R16, R17.
            notes: One-line summary of the conversation result.
        """
        step("TOOL-OUTCOME", f"{outcome.upper()} {reason_code}: {notes}")
        outcome = outcome.lower().strip()
        # R14 means "callback agreed" — refuse to close the call on a promise
        # that was never saved. Forces schedule_callback first.
        if (reason_code.upper().strip() == "R06" and state.lead_id
                and not state.whatsapp_requested_this_call):
            step("TOOL-OUTCOME-BLOCKED", "R06 without request_whatsapp_details — forcing it")
            await params.result_callback(
                "REJECTED: outcome R06 means the owner wants WhatsApp details, but "
                "request_whatsapp_details has not been called. Call it NOW (empty "
                "whatsapp_number = same number as this call), then set_lead_outcome again."
            )
            return
        if reason_code.upper().strip() == "R14" and state.lead_id:
            if not state.callback_scheduled_this_call:
                step("TOOL-OUTCOME-BLOCKED", "R14 without a saved callback — forcing schedule_callback")
                await params.result_callback(
                    "REJECTED: outcome R14 requires a saved callback, but "
                    "schedule_callback has not been called. Call schedule_callback "
                    "NOW with the agreed time in ISO format, then call "
                    "set_lead_outcome again."
                )
                return
        state.outcome = outcome
        state.reason_code = reason_code
        if state.lead_id and not state.dnc:
            lead = await db.get_lead(state.lead_id) or {}
            status = lead.get("status", "")
            # keep richer statuses set earlier in the call
            if status not in ("onboarding_started", "callback", "dnc", "hot"):
                status = outcome if outcome in ("hot", "warm", "cold", "lost") else "contacted"
            await db.update_lead(state.lead_id, {
                "status": status,
                "reason_code": reason_code,
                "last_outcome": notes or outcome,
            })
        await db.update_call(state.call_sid, {"outcome": f"{outcome} {reason_code} {notes}".strip()})
        await params.result_callback("Recorded. Say your short goodbye and then call end_call.")

    async def end_call(params: FunctionCallParams, reason: str = "finished"):
        """End the phone call. Call this only AFTER your goodbye line.

        Args:
            reason: Why the call is ending, e.g. "finished", "voicemail", "wrong number", "owner busy".
        """
        step("TOOL-END-CALL", f"agent ending call: {reason} — draining goodbye audio first")
        state.closing = True
        await db.update_call(state.call_sid, {"end_reason": reason})
        # Drain: never hang up while the final goodbye is queued or playing.
        # Wait until a bot utterance ENDS after this point (or times out).
        tt = state.turn_times
        t0 = time.time()
        if tt is not None:
            while time.time() - t0 < 10.0:
                await asyncio.sleep(0.25)
                if tt.bot_speaking:
                    continue
                # audio finished after end_call was invoked, or finished just
                # before it (goodbye streamed ahead of the tool call)
                if tt.last_bot_audio_end >= t0 - 2.0:
                    break
            await asyncio.sleep(0.6)  # let the transport's paced buffer flush fully
            step("TOOL-END-CALL-COMPLETE",
                 f"goodbye audio drained ({time.time() - t0:.1f}s) — closing now")
        try:
            await params.llm.push_frame(EndWorkerFrame(), FrameDirection.UPSTREAM)
        except Exception as e:
            logger.warning(f"EndWorkerFrame push failed ({e}); cancelling worker")
            if state.worker is not None:
                await state.worker.cancel()
        await params.result_callback("Call is ending. Do not say anything more.")

    return [
        record_qualification,
        capture_property_details,
        request_whatsapp_details,
        schedule_callback,
        transfer_to_sales,
        mark_do_not_contact,
        set_lead_outcome,
        end_call,
    ]
