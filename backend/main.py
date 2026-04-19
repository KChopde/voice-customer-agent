from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from db import Conversation, Grocery, GroceryAlias, GrocerySubstitute, get_session, init_db
from llm import _ollama_available, process_message, reset_session
from tasks import TASK_TYPES, reload_task_types
from twilio_voice import AUTH_TOKEN as TWILIO_AUTH_TOKEN
from twilio_voice import router as twilio_router

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="Voice Customer Support Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(twilio_router)


@app.on_event("startup")
def _startup() -> None:
    init_db()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class TalkRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=64)
    text: str = Field(..., min_length=1)


class ResetRequest(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "ollama": _ollama_available(),
        "task_types": list(TASK_TYPES.keys()),
        "channels": {
            "web": True,
            "telegram_configured": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
            "twilio_signature_check": bool(TWILIO_AUTH_TOKEN),
        },
    }


@app.get("/api/task-types")
def list_task_types() -> dict:
    return {"task_types": TASK_TYPES}


@app.post("/api/task-types/reload")
def reload_task_types_endpoint() -> dict:
    """Hot-reload task_types.json without restarting the server."""
    types = reload_task_types()
    return {"ok": True, "count": len(types), "task_types": list(types.keys())}


@app.post("/api/talk")
def talk(req: TalkRequest, db: Session = Depends(get_session)) -> dict:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text.")

    db.add(Conversation(session_id=req.session_id, role="user", message=text))
    db.commit()

    result = process_message(req.session_id, text, db)

    db.add(
        Conversation(
            session_id=req.session_id,
            role="assistant",
            message=result["reply"],
            task_type=result.get("task_type"),
        )
    )
    db.commit()

    return result


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...), suffix: str = Form(".webm")) -> dict:
    """Optional: server-side STT via faster-whisper.

    The browser already does STT for free, so this is only a fallback.
    """
    try:
        from stt import transcribe_bytes
    except Exception as exc:  # pragma: no cover - only triggered without dep
        raise HTTPException(status_code=500, detail=str(exc))
    data = await audio.read()
    text = transcribe_bytes(data, suffix=suffix)
    return {"text": text}


@app.post("/api/reset")
def reset(req: ResetRequest) -> dict:
    reset_session(req.session_id)
    return {"ok": True}


@app.get("/api/groceries")
def search_groceries(
    q: str | None = Query(None, description="Search by name OR alias (case-insensitive)."),
    category: str | None = Query(None, description="Filter by category."),
    limit: int = Query(20, ge=1, le=200),
    db: Session = Depends(get_session),
) -> dict:
    """Browse / search the seeded grocery catalog.

    Examples:
        /api/groceries                           -> first 20
        /api/groceries?q=aloo                    -> match by alias
        /api/groceries?q=apple&category=Fruits   -> filter by both
    """
    query = db.query(Grocery)
    if q:
        like = f"%{q.lower()}%"
        # Match either grocery name OR any of its aliases.
        alias_ids = (
            db.query(GroceryAlias.grocery_id)
            .filter(GroceryAlias.alias.ilike(like))
            .subquery()
        )
        query = query.filter(or_(Grocery.name.ilike(like), Grocery.id.in_(alias_ids)))
    if category:
        query = query.filter(Grocery.category.ilike(category))

    rows = query.order_by(Grocery.id).limit(limit).all()
    return {
        "count": len(rows),
        "items": [
            {
                "id": g.id,
                "name": g.name,
                "category": g.category,
                "unit": g.unit,
                "price": g.price,
                "stock": g.stock,
                "in_stock": g.stock > 0,
            }
            for g in rows
        ],
    }


@app.get("/api/groceries/{grocery_id}")
def get_grocery(grocery_id: int, db: Session = Depends(get_session)) -> dict:
    """Full detail for one grocery: aliases + substitute names."""
    g = db.query(Grocery).get(grocery_id)
    if g is None:
        raise HTTPException(status_code=404, detail="Grocery not found.")

    sub_ids = [
        s.substitute_id
        for s in db.query(GrocerySubstitute).filter(GrocerySubstitute.grocery_id == g.id)
    ]
    subs = (
        db.query(Grocery).filter(Grocery.id.in_(sub_ids)).all() if sub_ids else []
    )

    return {
        "id": g.id,
        "name": g.name,
        "category": g.category,
        "unit": g.unit,
        "price": g.price,
        "stock": g.stock,
        "in_stock": g.stock > 0,
        "aliases": [a.alias for a in g.aliases],
        "substitutes": [{"id": s.id, "name": s.name, "price": s.price} for s in subs],
    }


@app.get("/api/conversations/{session_id}")
def get_conversations(session_id: str, db: Session = Depends(get_session)) -> dict:
    rows = (
        db.query(Conversation)
        .filter(Conversation.session_id == session_id)
        .order_by(Conversation.id.desc())
        .limit(50)
        .all()
    )
    return {
        "turns": [
            {
                "role": r.role,
                "message": r.message,
                "task_type": r.task_type,
                "at": r.created_at.isoformat(),
            }
            for r in reversed(rows)
        ]
    }


# ---------------------------------------------------------------------------
# Frontend (served from ../frontend)
# ---------------------------------------------------------------------------
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
