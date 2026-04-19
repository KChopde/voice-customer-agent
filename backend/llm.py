from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from tasks import TASK_TYPES, execute_task


OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "30"))


# ---------------------------------------------------------------------------
# Session memory (in-process; demo only)
# ---------------------------------------------------------------------------
# session_id -> {"task_type": str | None, "fields": dict, "history": list[dict]}
SESSIONS: dict[str, dict[str, Any]] = {}


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {"task_type": None, "fields": {}, "history": []}
    return SESSIONS[session_id]


def reset_session(session_id: str) -> None:
    SESSIONS.pop(session_id, None)


# ---------------------------------------------------------------------------
# Ollama client
# ---------------------------------------------------------------------------
def _ollama_available() -> bool:
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=1.5)
        return r.status_code == 200
    except requests.RequestException:
        return False


def _call_ollama(prompt: str) -> str:
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.2},
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def _build_prompt(user_text: str, session: dict[str, Any]) -> str:
    catalog_lines = []
    for name, meta in TASK_TYPES.items():
        catalog_lines.append(
            f"- {name}: {meta['description']} | required_fields: {meta['required_fields']}"
        )
    catalog = "\n".join(catalog_lines)

    history_block = (
        "\n".join(f"{turn['role']}: {turn['message']}" for turn in session["history"][-6:])
        or "(no prior messages)"
    )
    current_task = session["task_type"] or "none"
    fields = json.dumps(session["fields"])

    return f"""You are a friendly customer-support voice agent. Your job is
to figure out what TASK the user wants done, gather the fields each task needs,
and reply briefly and naturally.

Available task types:
{catalog}

You may ALSO invent a brand-new task_type name (snake_case) if NOTHING in
the catalog fits. The system will capture it as a customer inquiry. Prefer
using a catalog task_type when one applies.

Conversation so far:
{history_block}

Current active task_type: {current_task}
Fields collected so far: {fields}

The user just said: "{user_text}"

Respond with ONLY a single JSON object, no prose, in this exact shape:
{{
  "task_type": "<a catalog name, or a NEW snake_case name if needed, or 'small_talk'>",
  "fields": {{ "<field_name>": "<value>", ... }},
  "ready": <true if every required field for the task is now filled, else false>,
  "reply": "<one short, natural, voice-friendly sentence (max 25 words)>"
}}

Rules:
- Keep the active task_type unless the user clearly switches topic.
- Only include field values clearly stated by the user; never invent them.
- If a required field is missing, set ready=false and ask for it in `reply`.
- For chit-chat / greetings, use task_type='small_talk' and reply naturally.
- For questions you can answer directly (FAQs, hours, policies, etc.) use
  task_type='general_inquiry' with field 'question' set to the user's question.
"""


def _parse_llm_json(raw: str) -> dict[str, Any]:
    raw = (raw or "").strip()
    if not raw:
        return {}
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# Rule-based fallback (used only when Ollama isn't running)
# ---------------------------------------------------------------------------
def _rule_based(user_text: str, session: dict[str, Any]) -> dict[str, Any]:
    text = user_text.lower()

    task = session["task_type"]
    if task is None:
        # Try keyword match against the catalog (keywords come from JSON).
        for name, meta in TASK_TYPES.items():
            for kw in meta.get("keywords", []) or []:
                if kw in text:
                    task = name
                    break
            if task:
                break
    if task is None:
        # Default to a general inquiry so we still capture and respond.
        task = "general_inquiry"

    new_fields: dict[str, Any] = {}
    nums = [int(n) for n in re.findall(r"\b(\d+)\b", text)]

    if task in {"check_order_status", "cancel_order", "track_delivery", "raise_complaint"}:
        if nums and "order_id" not in session["fields"]:
            new_fields["order_id"] = nums[0]
    if task == "book_order" and nums and "quantity" not in session["fields"]:
        new_fields["quantity"] = nums[0]
    if task == "give_feedback" and nums and "rating" not in session["fields"]:
        new_fields["rating"] = max(1, min(5, nums[0]))

    last_asked = session.get("_last_asked")
    if last_asked and last_asked not in session["fields"] and last_asked not in new_fields:
        new_fields[last_asked] = user_text.strip()

    merged = {**session["fields"], **new_fields}
    meta = TASK_TYPES.get(task, {})
    required = meta.get("required_fields", [])
    missing = [f for f in required if f not in merged or merged[f] in ("", None)]

    # general_inquiry: capture the whole user text as the question.
    if task == "general_inquiry" and "question" not in merged:
        new_fields["question"] = user_text.strip()
        missing = []

    if not missing:
        ready = True
        reply = "Got it, processing that for you now."
    else:
        ready = False
        next_field = missing[0]
        prompts = meta.get("field_prompts", {})
        reply = prompts.get(next_field, f"Could you provide the {next_field}?")
        session["_last_asked"] = next_field

    return {"task_type": task, "fields": new_fields, "ready": ready, "reply": reply}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def process_message(session_id: str, user_text: str, db) -> dict[str, Any]:
    session = _get_session(session_id)
    session["history"].append({"role": "user", "message": user_text})

    used_engine = "rule-based"
    parsed: dict[str, Any] = {}

    if _ollama_available():
        try:
            raw = _call_ollama(_build_prompt(user_text, session))
            parsed = _parse_llm_json(raw)
            if parsed.get("task_type"):
                used_engine = f"ollama:{OLLAMA_MODEL}"
        except requests.RequestException:
            parsed = {}

    if not parsed:
        parsed = _rule_based(user_text, session)

    task_type = parsed.get("task_type") or session["task_type"] or "general_inquiry"

    new_fields = parsed.get("fields") or {}
    if isinstance(new_fields, dict):
        for k, v in new_fields.items():
            if v not in ("", None):
                session["fields"][k] = v

    # Allow the AI to switch tasks mid-conversation.
    if session["task_type"] != task_type and task_type not in {"small_talk"}:
        session["task_type"] = task_type

    reply = parsed.get("reply") or "Could you say that again?"
    action_result: dict[str, Any] | None = None

    active = session["task_type"]
    if active and active != "small_talk":
        meta = TASK_TYPES.get(active, {})
        required = meta.get("required_fields", [])
        all_filled = all(
            f in session["fields"] and session["fields"][f] not in ("", None)
            for f in required
        )
        ai_says_ready = bool(parsed.get("ready"))

        # For unknown task types (no catalog entry, no required fields),
        # treat them as ready immediately so we capture them as inquiries.
        if active not in TASK_TYPES:
            all_filled = True

        if all_filled and (ai_says_ready or used_engine == "rule-based" or active not in TASK_TYPES):
            action_result = execute_task(active, session["fields"], db)
            if action_result.get("ok"):
                # Use the AI's natural reply for inquiries; use the handler's
                # confirmation summary for actionable tasks.
                if not action_result.get("is_inquiry"):
                    reply = f"{action_result.get('summary', 'Done.')} Anything else I can help with?"
            else:
                reply = action_result.get("summary", "Sorry, I couldn't do that.")
            session["task_type"] = None
            session["fields"] = {}
            session.pop("_last_asked", None)

    session["history"].append({"role": "assistant", "message": reply})

    return {
        "reply": reply,
        "task_type": active,
        "fields": dict(session["fields"]),
        "engine": used_engine,
        "action": action_result,
    }
