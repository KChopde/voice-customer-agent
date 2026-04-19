"""Twilio Voice integration - turns the agent into a real phone-line CSR.

How it works:
    1. A user calls your Twilio phone number.
    2. Twilio POSTs to /api/twilio/voice  -> we return TwiML that greets the
       caller and opens a <Gather input="speech"> block.
    3. Twilio records the caller's speech, transcribes it (free basic STT
       included with the call), then POSTs the transcript to
       /api/twilio/gather.
    4. We feed the transcript into the SAME process_message() brain that
       powers the web UI and Telegram bot, then return TwiML that <Say>s
       the reply and re-opens <Gather> for the next turn.
    5. When the user says "bye"/"goodbye" or hangs up, we end the call.

Why this design (vs. Twilio Media Streams):
    - Zero extra dependencies. No WebSockets, no Whisper, no audio piping.
    - Twilio's free basic Speech Recognition is good enough for most calls.
    - Latency is acceptable (~1-2s per turn) and easy to debug since every
      step is just a normal HTTP POST.
    - Upgrade path: swap this file for a Media Streams version later for
      sub-second latency without touching the rest of the codebase.

Deps: only requires what's already in requirements.txt (FastAPI + stdlib).
The official `twilio` SDK is NOT needed because we hand-write TwiML
(it's just XML) and validate the signature with stdlib hmac.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from urllib.parse import urlencode
from xml.sax.saxutils import escape

from fastapi import APIRouter, Form, HTTPException, Request, Response

from db import Conversation, SessionLocal
from llm import process_message, reset_session


log = logging.getLogger("twilio")

router = APIRouter(prefix="/api/twilio", tags=["twilio"])


# ---------------------------------------------------------------------------
# Configuration (all overridable via env vars)
# ---------------------------------------------------------------------------
GREETING = os.environ.get(
    "TWILIO_GREETING",
    "Hi! Thanks for calling customer support. I can help you book an order, "
    "raise a complaint, give feedback, or check an order. How can I help today?",
)
GOODBYE = os.environ.get(
    "TWILIO_GOODBYE", "Thanks for calling. Have a great day!"
)
NO_INPUT_REPROMPT = (
    "I didn't catch that. Could you say it again? "
    "Or say 'goodbye' to end the call."
)
ERROR_MESSAGE = (
    "Sorry, I had a problem on my end. Let's try that again."
)

# Twilio TTS voice. Polly.* voices sound much more natural than the legacy
# "alice" voice. Free with the call. See:
# https://www.twilio.com/docs/voice/twiml/say/text-speech
VOICE = os.environ.get("TWILIO_VOICE", "Polly.Joanna")

# Speech recognition language. Use "en-IN" for Indian-English accents,
# "en-US" otherwise. Twilio's basic STT is free.
LANGUAGE = os.environ.get("TWILIO_LANGUAGE", "en-US")

# Optional: paste your Twilio Auth Token here (or set the env var) and we'll
# validate every incoming request's signature. Strongly recommended for
# anything exposed to the public internet.
AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()

# How many seconds Twilio waits for the caller to start speaking before
# treating the turn as empty. Keep this short for snappy back-and-forth.
GATHER_TIMEOUT = int(os.environ.get("TWILIO_GATHER_TIMEOUT", "5"))

# Words that end the call.
END_CALL_KEYWORDS = ("goodbye", "bye", "hang up", "end call", "thanks bye", "that's all")


# ---------------------------------------------------------------------------
# Signature validation (Twilio docs:
# https://www.twilio.com/docs/usage/security#validating-requests)
# ---------------------------------------------------------------------------
async def _validate_signature(request: Request) -> None:
    """Raise HTTP 403 if the request didn't come from Twilio.

    No-op when TWILIO_AUTH_TOKEN is not set (useful for local dev).
    """
    if not AUTH_TOKEN:
        return

    sig_header = request.headers.get("X-Twilio-Signature", "")
    if not sig_header:
        raise HTTPException(status_code=403, detail="Missing Twilio signature.")

    # Twilio computes the signature over: full_url + sorted(form_params concatenated).
    form = await request.form()
    full_url = str(request.url)
    payload = full_url + "".join(f"{k}{form[k]}" for k in sorted(form.keys()))
    mac = hmac.new(
        AUTH_TOKEN.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1
    ).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature.")


# ---------------------------------------------------------------------------
# TwiML builders (just XML strings - the official SDK isn't required)
# ---------------------------------------------------------------------------
def _twiml(body: str) -> Response:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response>{body}</Response>'
    return Response(content=xml, media_type="application/xml")


def _say(text: str) -> str:
    return f'<Say voice="{VOICE}" language="{LANGUAGE}">{escape(text)}</Say>'


def _gather(prompt: str | None = None) -> str:
    """Open a speech-input window. Twilio will POST the transcript to /gather."""
    inner = _say(prompt) if prompt else ""
    return (
        f'<Gather input="speech" '
        f'action="/api/twilio/gather" method="POST" '
        f'timeout="{GATHER_TIMEOUT}" speechTimeout="auto" '
        f'language="{LANGUAGE}">'
        f"{inner}"
        f"</Gather>"
        # Fallback if nothing was said inside the <Gather> window:
        f"{_say(NO_INPUT_REPROMPT)}"
        f'<Redirect method="POST">/api/twilio/voice</Redirect>'
    )


def _hangup(text: str) -> str:
    return f"{_say(text)}<Hangup/>"


def _session_id(call_sid: str) -> str:
    """One conversation context per phone call."""
    return f"tw_{call_sid}"


def _is_goodbye(text: str) -> bool:
    t = (text or "").lower().strip()
    return any(kw in t for kw in END_CALL_KEYWORDS)


# ---------------------------------------------------------------------------
# Webhook endpoints
# ---------------------------------------------------------------------------
@router.post("/voice")
async def voice_webhook(request: Request):
    """Initial webhook Twilio hits when a call comes in.

    Configure your Twilio number's "A Call Comes In" webhook to:
        POST  https://<your-public-host>/api/twilio/voice
    """
    await _validate_signature(request)
    form = await request.form()
    call_sid = form.get("CallSid", "")
    caller = form.get("From", "")
    log.info("Incoming call %s from %s", call_sid, caller)

    if call_sid:
        # Fresh call -> fresh conversation context.
        reset_session(_session_id(call_sid))

    return _twiml(_gather(GREETING))


@router.post("/gather")
async def gather_webhook(request: Request):
    """Receives the caller's transcribed speech for one turn."""
    await _validate_signature(request)
    form = await request.form()
    call_sid = form.get("CallSid", "")
    speech = (form.get("SpeechResult") or "").strip()
    confidence = form.get("Confidence", "0")
    log.info(
        "Turn  call=%s  conf=%s  text=%r", call_sid, confidence, speech
    )

    if not call_sid:
        return _twiml(_hangup(ERROR_MESSAGE))

    sid = _session_id(call_sid)

    if not speech:
        return _twiml(_gather("Sorry, I didn't catch that. Could you repeat it?"))

    # ---- Run the turn through the agent brain -----------------------------
    db = SessionLocal()
    try:
        db.add(Conversation(session_id=sid, role="user", message=speech))
        db.commit()

        try:
            result = process_message(sid, speech, db)
        except Exception:
            log.exception("Agent failure")
            return _twiml(_gather(ERROR_MESSAGE))

        reply = result.get("reply") or "Could you say that again?"
        task_type = result.get("task_type")

        db.add(
            Conversation(
                session_id=sid,
                role="assistant",
                message=reply,
                task_type=task_type,
            )
        )
        db.commit()
    finally:
        db.close()

    # Caller (or AI's reply) signaled the end of the conversation.
    if _is_goodbye(speech):
        reset_session(sid)
        return _twiml(_hangup(f"{reply} {GOODBYE}"))

    return _twiml(_say(reply) + _gather())


@router.post("/status")
async def status_webhook(request: Request):
    """Optional call-status callback.

    Configure on the Twilio number under "Call Status Changes" -> POST to
    /api/twilio/status. We use it to clear the in-memory session when the
    caller hangs up.
    """
    await _validate_signature(request)
    form = await request.form()
    call_sid = form.get("CallSid", "")
    call_status = form.get("CallStatus", "")
    log.info("Call %s status=%s", call_sid, call_status)
    if call_status in {"completed", "failed", "no-answer", "busy", "canceled"}:
        if call_sid:
            reset_session(_session_id(call_sid))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Local test helper - lets you simulate a Twilio request without a phone.
# ---------------------------------------------------------------------------
@router.post("/_simulate")
async def simulate(call_sid: str = Form("test"), text: str = Form(...)):
    """Pretend to be Twilio: send {call_sid, text} and get the TwiML back.

    Useful for smoke-testing without paying for a real call. Example:
        curl -X POST http://localhost:8000/api/twilio/_simulate \
             -d "call_sid=demo1" -d "text=I want to order 2 kg aloo"
    """
    sid = _session_id(call_sid)
    db = SessionLocal()
    try:
        db.add(Conversation(session_id=sid, role="user", message=text))
        db.commit()
        result = process_message(sid, text, db)
        reply = result.get("reply") or ""
        db.add(
            Conversation(
                session_id=sid,
                role="assistant",
                message=reply,
                task_type=result.get("task_type"),
            )
        )
        db.commit()
    finally:
        db.close()
    return _twiml(_say(reply) + _gather())
