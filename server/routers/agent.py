"""Agent-facing endpoints: command polling + session captures."""

import threading
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

import io
import os
import uuid

from server.config import UPLOAD_DIR
from server.database import get_db
from server.models import Capture, CaptureSession

router = APIRouter(prefix="/api/agent", tags=["agent"])

# agent_id -> {"command": "capture", "issued_at": ts}
_pending: dict = {}
_lock = threading.Lock()

MAX_DIM = 1600
ALLOWED = {"image/png", "image/jpeg", "image/webp"}


def issue_capture_command(agent_id: str = "default", chat_id: str = "dashboard") -> bool:
    with _lock:
        _pending[agent_id] = {
            "command": "capture",
            "issued_at": datetime.utcnow().timestamp(),
            "chat_id": str(chat_id),  # the chat that asked for this capture
        }
    return True


def pending_chat_id(agent_id: str = "default") -> str:
    """Chat that issued the currently pending capture command (dashboard default)."""
    with _lock:
        entry = _pending.get(agent_id)
        return (entry or {}).get("chat_id", "dashboard")


COMMAND_TTL = 20  # seconds before an unclaimed command expires


def command_pending(agent_id: str = "default") -> bool:
    """True while a command is waiting to be picked up; expires if agent never polls."""
    with _lock:
        entry = _pending.get(agent_id)
        if not entry:
            return False
        if datetime.utcnow().timestamp() - entry["issued_at"] > COMMAND_TTL:
            del _pending[agent_id]
            return False
        return True


def open_session(db: Session, chat_id: str = "dashboard") -> CaptureSession:
    """Return (or create) the open session owned by this chat. Sessions are
    per-chat so one chat's question never shares parts with another's — and a
    session closed by the \"Q\" command is never reused."""
    sess = (
        db.query(CaptureSession)
        .filter_by(status="open", chat_id=str(chat_id))
        .first()
    )
    if not sess:
        sess = CaptureSession(status="open", chat_id=str(chat_id))
        db.add(sess)
        db.commit()
        db.refresh(sess)
    return sess


@router.get("/poll")
def poll(agent_id: str = "default"):
    with _lock:
        entry = _pending.get(agent_id)
        if entry and datetime.utcnow().timestamp() - entry["issued_at"] > COMMAND_TTL:
            # stale command (agent was offline when issued) — discard, never deliver
            del _pending[agent_id]
            entry = None
        if entry:
            del _pending[agent_id]
    if entry:
        return {"command": entry["command"]}
    return {"command": None}


@router.post("/capture")
async def agent_capture(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Agent uploads a screenshot as a part of the open session (no analysis yet)."""
    if file.content_type not in ALLOWED:
        raise HTTPException(415, "Only PNG/JPEG/WebP screenshots are supported")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Image too large")

    img = Image.open(io.BytesIO(data))
    if img.width > MAX_DIM or img.height > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM))
    name = f"{uuid.uuid4().hex}.png"
    path = os.path.join(UPLOAD_DIR, name)
    img.save(path, "PNG")

    sess = open_session(db, pending_chat_id())
    cap = Capture(image_path=path, status="captured", session_id=sess.id)
    db.add(cap)
    db.commit()
    db.refresh(cap)
    parts = _part_count(db, sess.id)

    # echo the captured screenshot to all linked Telegram chats
    background.add_task(_echo_part, data, parts)

    return {"id": cap.id, "session_id": sess.id, "parts": parts}


def _echo_part(png: bytes, part_no: int) -> None:
    from server import telegram
    from server.database import SessionLocal

    if not telegram.enabled():
        return
    db = SessionLocal()
    try:
        telegram.broadcast_photo(db, png, caption=f"📸 part {part_no} captured")
    except Exception:
        pass
    finally:
        db.close()


def _part_count(db: Session, session_id: int) -> int:
    return db.query(Capture).filter_by(session_id=session_id, status="captured").count()


def get_open_session(db: Session, chat_id: str = "dashboard") -> CaptureSession | None:
    return (
        db.query(CaptureSession)
        .filter_by(status="open", chat_id=str(chat_id))
        .first()
    )


def reset_question_context(db: Session, chat_id: str = "dashboard") -> dict:
    """\"Q\" command: end the current question's context. Closes any open /
    stuck-solving session owned by this chat and clears that chat's history so
    the next capture/question starts genuinely fresh. Closed sessions are
    never reopened and their images are never attached to future answers."""
    from server import chat_history

    closed = 0
    sessions = (
        db.query(CaptureSession)
        .filter(CaptureSession.chat_id == str(chat_id))
        .filter(CaptureSession.status.in_(("open", "solving")))
        .all()
    )
    for s in sessions:
        s.status = "closed"
        closed += 1
    db.commit()
    cleared = chat_history.clear(chat_id)
    return {"chat_id": str(chat_id), "closed_sessions": closed, "cleared_messages": cleared}


def session_images(db: Session, session_id: int) -> list[bytes]:
    rows = (
        db.query(Capture)
        .filter_by(session_id=session_id, status="captured")
        .order_by(Capture.id.asc())
        .all()
    )
    out = []
    for r in rows:
        if os.path.exists(r.image_path):
            with open(r.image_path, "rb") as f:
                out.append(f.read())
    return out
