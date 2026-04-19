from __future__ import annotations

import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

# Make the agent modules importable when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import Conversation, SessionLocal, init_db  # noqa: E402
from llm import process_message, reset_session  # noqa: E402


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"

POLL_TIMEOUT = 30  # seconds
MAX_REPLY_CHARS = 4000  # Telegram hard limit is 4096

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)
log = logging.getLogger("telegram-bot")


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def tg(method: str, **params) -> dict:
    """Call a Telegram Bot API method and return the JSON 'result'."""
    r = requests.post(f"{API}/{method}", json=params, timeout=POLL_TIMEOUT + 10)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram error on {method}: {data}")
    return data["result"]


def send_message(chat_id: int, text: str) -> None:
    text = (text or "").strip() or "(no reply)"
    if len(text) > MAX_REPLY_CHARS:
        text = text[: MAX_REPLY_CHARS - 1] + "…"
    tg("sendMessage", chat_id=chat_id, text=text)


def send_typing(chat_id: int) -> None:
    try:
        tg("sendChatAction", chat_id=chat_id, action="typing")
    except Exception:
        pass


def download_file(file_id: str) -> bytes | None:
    """Resolve a Telegram file_id to its bytes."""
    try:
        info = tg("getFile", file_id=file_id)
        path = info.get("file_path")
        if not path:
            return None
        r = requests.get(f"{FILE_API}/{path}", timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as exc:  # pragma: no cover
        log.warning("download_file failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Voice transcription (optional - requires faster-whisper)
# ---------------------------------------------------------------------------
def transcribe_voice(audio_bytes: bytes) -> str | None:
    """Return transcript or None if STT isn't available."""
    try:
        from stt import transcribe_bytes  # type: ignore
    except Exception as exc:
        log.info("Voice received but STT not available: %s", exc)
        return None
    # Telegram voice notes are OGG/Opus.
    return transcribe_bytes(audio_bytes, suffix=".oga")


# ---------------------------------------------------------------------------
# Update handling
# ---------------------------------------------------------------------------
def session_id_for(chat_id: int) -> str:
    """One conversation context per Telegram chat."""
    return f"tg_{chat_id}"


def handle_command(chat_id: int, text: str) -> bool:
    """Handle /start, /reset, /help. Returns True if it handled the message."""
    cmd = text.split()[0].lower().split("@")[0]
    if cmd == "/start":
        send_message(
            chat_id,
            "Hi! I'm your customer-support assistant.\n\n"
            "You can:\n"
            "• Book an order\n"
            "• Raise a complaint\n"
            "• Give feedback\n"
            "• Check / cancel / track an order\n\n"
            "Send a text message or a voice note. Use /reset to start over.",
        )
        return True
    if cmd == "/reset":
        reset_session(session_id_for(chat_id))
        send_message(chat_id, "Conversation cleared. How can I help?")
        return True
    if cmd == "/help":
        send_message(
            chat_id,
            "Just talk naturally. Examples:\n"
            "• \"Order 2 pizzas to 21 Oak Street\"\n"
            "• \"Complain about order 3, food was cold\"\n"
            "• \"5 stars feedback, great service\"\n"
            "• \"Cancel order 5\"",
        )
        return True
    return False


def handle_message(msg: dict[str, Any]) -> None:
    chat_id = msg["chat"]["id"]
    user_text: str | None = None

    # ---- Voice note --------------------------------------------------------
    if "voice" in msg or "audio" in msg:
        send_typing(chat_id)
        file_id = (msg.get("voice") or msg.get("audio"))["file_id"]
        audio = download_file(file_id)
        if not audio:
            send_message(chat_id, "Sorry, I couldn't fetch that voice note.")
            return
        transcript = transcribe_voice(audio)
        if not transcript:
            send_message(
                chat_id,
                "I received your voice note, but voice transcription isn't "
                "enabled on this server. Please send your message as text, "
                "or ask the admin to `pip install faster-whisper`.",
            )
            return
        send_message(chat_id, f"🎙 \"{transcript}\"")
        user_text = transcript

    # ---- Text --------------------------------------------------------------
    elif "text" in msg:
        text = msg["text"].strip()
        if text.startswith("/") and handle_command(chat_id, text):
            return
        user_text = text

    else:
        send_message(chat_id, "Please send me a text message or a voice note.")
        return

    if not user_text:
        return

    # ---- Run through the agent --------------------------------------------
    send_typing(chat_id)
    sid = session_id_for(chat_id)

    db = SessionLocal()
    try:
        db.add(Conversation(session_id=sid, role="user", message=user_text))
        db.commit()

        try:
            result = process_message(sid, user_text, db)
        except Exception as exc:
            log.exception("Agent failed")
            send_message(chat_id, "Sorry, something went wrong on my side. Please try again.")
            return

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

    send_message(chat_id, reply)


# ---------------------------------------------------------------------------
# Long-polling loop
# ---------------------------------------------------------------------------
def run() -> None:
    if not TOKEN:
        log.error(
            "TELEGRAM_BOT_TOKEN is not set. Get a token from @BotFather and set the env var."
        )
        sys.exit(1)

    init_db()

    me = tg("getMe")
    log.info("Connected as @%s (%s)", me.get("username"), me.get("first_name"))
    log.info("Press Ctrl+C to stop. Open Telegram and message your bot now.")

    offset = 0
    backoff = 1.0
    while True:
        try:
            updates = tg(
                "getUpdates",
                offset=offset,
                timeout=POLL_TIMEOUT,
                allowed_updates=["message"],
            )
            backoff = 1.0
            for upd in updates:
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg:
                    continue
                try:
                    handle_message(msg)
                except Exception:
                    log.exception("Failed to handle message")
        except KeyboardInterrupt:
            log.info("Bye.")
            return
        except requests.RequestException as exc:
            log.warning("Network hiccup: %s (retrying in %.1fs)", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
        except Exception:
            log.exception("Unexpected error in poll loop")
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    run()
