from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from db import Complaint, Feedback, Inquiry, Order, User


CONFIG_PATH = Path(__file__).resolve().parent / "task_types.json"


# ---------------------------------------------------------------------------
# Dynamic task-type loading
# ---------------------------------------------------------------------------
def load_task_types() -> dict[str, dict[str, Any]]:
    """Load and index task types from task_types.json by name."""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {t["name"]: t for t in data.get("task_types", [])}


# Loaded once at import; reload via reload_task_types() if you edit the JSON.
TASK_TYPES: dict[str, dict[str, Any]] = load_task_types()


def reload_task_types() -> dict[str, dict[str, Any]]:
    global TASK_TYPES
    TASK_TYPES = load_task_types()
    return TASK_TYPES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _get_or_create_guest(db: Session) -> User:
    user = db.query(User).filter(User.name == "Guest").first()
    if user is None:
        user = User(name="Guest")
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------
def handle_book_order(fields: dict, db: Session) -> dict:
    user = _get_or_create_guest(db)
    try:
        qty = int(fields.get("quantity", 1))
    except (TypeError, ValueError):
        qty = 1
    order = Order(
        user_id=user.id,
        product=str(fields["product"]),
        quantity=qty,
        address=str(fields.get("address", "")),
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return {
        "ok": True,
        "summary": f"Order #{order.id}: {qty} x {order.product} to {order.address}",
        "data": {"order_id": order.id},
    }


def handle_raise_complaint(fields: dict, db: Session) -> dict:
    user = _get_or_create_guest(db)
    try:
        order_id = int(fields["order_id"])
    except (TypeError, ValueError, KeyError):
        order_id = None
    complaint = Complaint(
        user_id=user.id,
        order_id=order_id,
        issue=str(fields.get("issue", "")),
    )
    db.add(complaint)
    db.commit()
    db.refresh(complaint)
    return {
        "ok": True,
        "summary": f"Complaint #{complaint.id} registered for order {order_id}.",
        "data": {"complaint_id": complaint.id},
    }


def handle_give_feedback(fields: dict, db: Session) -> dict:
    user = _get_or_create_guest(db)
    try:
        rating = int(fields.get("rating", 0))
    except (TypeError, ValueError):
        rating = 0
    fb = Feedback(
        user_id=user.id,
        rating=rating,
        message=str(fields.get("message", "")),
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return {
        "ok": True,
        "summary": f"Feedback #{fb.id} ({rating}/5) saved.",
        "data": {"feedback_id": fb.id},
    }


def handle_check_order_status(fields: dict, db: Session) -> dict:
    try:
        order_id = int(fields["order_id"])
    except (TypeError, ValueError, KeyError):
        return {"ok": False, "summary": "I couldn't read the order id."}
    order = db.query(Order).get(order_id)
    if order is None:
        return {"ok": False, "summary": f"No order found with id {order_id}."}
    return {
        "ok": True,
        "summary": f"Order #{order.id} ({order.product}) is currently '{order.status}'.",
        "data": {"order_id": order.id, "status": order.status},
    }


def handle_cancel_order(fields: dict, db: Session) -> dict:
    try:
        order_id = int(fields["order_id"])
    except (TypeError, ValueError, KeyError):
        return {"ok": False, "summary": "I couldn't read the order id."}
    order = db.query(Order).get(order_id)
    if order is None:
        return {"ok": False, "summary": f"No order found with id {order_id}."}
    order.status = "cancelled"
    db.commit()
    return {
        "ok": True,
        "summary": f"Order #{order.id} has been cancelled.",
        "data": {"order_id": order.id},
    }


def handle_track_delivery(fields: dict, db: Session) -> dict:
    try:
        order_id = int(fields["order_id"])
    except (TypeError, ValueError, KeyError):
        return {"ok": False, "summary": "I couldn't read the order id."}
    order = db.query(Order).get(order_id)
    if order is None:
        return {"ok": False, "summary": f"No order found with id {order_id}."}
    fake_eta = "about 25 minutes"
    return {
        "ok": True,
        "summary": f"Order #{order.id} is out for delivery and will arrive in {fake_eta}.",
        "data": {"order_id": order.id, "eta": fake_eta},
    }


def _handle_inquiry(task_type: str, fields: dict, db: Session) -> dict:
    """Catch-all handler for AI-proposed task types or general questions.

    The user's request is logged as an Inquiry row so a human (or a
    later workflow) can follow up. The conversational reply itself is
    generated by the LLM in llm.py - this handler just records the request.
    """
    user = _get_or_create_guest(db)
    summary = (
        fields.get("question")
        or fields.get("message")
        or fields.get("description")
        or json.dumps(fields, ensure_ascii=False)
    )
    inq = Inquiry(
        user_id=user.id,
        task_type=task_type,
        summary=str(summary)[:1000],
        details=json.dumps(fields, ensure_ascii=False),
    )
    db.add(inq)
    db.commit()
    db.refresh(inq)
    return {
        "ok": True,
        "summary": f"Noted (inquiry #{inq.id}).",
        "data": {"inquiry_id": inq.id, "task_type": task_type},
        "is_inquiry": True,
    }


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
HANDLERS: dict[str, Callable[[dict, Session], dict]] = {
    "book_order": handle_book_order,
    "raise_complaint": handle_raise_complaint,
    "give_feedback": handle_give_feedback,
    "check_order_status": handle_check_order_status,
    "cancel_order": handle_cancel_order,
    "track_delivery": handle_track_delivery,
}


def execute_task(task_type: str, fields: dict, db: Session) -> dict:
    """Dispatch a task to its handler, or to the generic inquiry handler."""
    handler = HANDLERS.get(task_type)
    if handler is None:
        return _handle_inquiry(task_type, fields, db)
    return handler(fields, db)
