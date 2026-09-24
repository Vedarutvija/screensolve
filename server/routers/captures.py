import os
import threading
import uuid
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Response, UploadFile
from PIL import Image
from sqlalchemy.orm import Session

from server.config import GEMINI_API_KEY, OPENAI_API_KEY, UPLOAD_DIR
from server.database import get_db
from server.gemini import analyze_image
from server.models import Capture
from server import telegram

import io

router = APIRouter(prefix="/api/captures", tags=["captures"])

MAX_DIM = 1600
ALLOWED = {"image/png", "image/jpeg", "image/webp"}


def _save_image(data: bytes) -> tuple[str, str]:
    img = Image.open(io.BytesIO(data))
    if img.width > MAX_DIM or img.height > MAX_DIM:
        img.thumbnail((MAX_DIM, MAX_DIM))
    name = f"{uuid.uuid4().hex}.png"
    path = os.path.join(UPLOAD_DIR, name)
    img.save(path, "PNG")
    with open(path, "rb") as f:
        return path, f.read()


def _run_analysis(capture_id: int) -> None:
    from server.database import SessionLocal

    db = SessionLocal()
    try:
        cap = db.get(Capture, capture_id)
        if cap is None:
            return
        cap.status = "analyzing"
        db.commit()
        with open(cap.image_path, "rb") as f:
            raw = f.read()
        try:
            result = analyze_image(raw)
        except Exception as e:  # noqa: BLE001
            cap.status = "error"
            cap.error_message = str(e)[:1000]
            cap.analyzed_at = datetime.utcnow()
            db.commit()
            return
        cap.analyzed_at = datetime.utcnow()
        if not result.get("has_question"):
            cap.status = "no_question"
            cap.notes = "No coding question or solution attempt detected on screen."
        else:
            cap.status = "solved"
            cap.problem_statement = result.get("problem_statement")
            cap.user_attempt = result.get("user_attempt")
            cap.solution_steps = result.get("solution_steps")
            cap.optimized_code = result.get("optimized_code")
            cap.time_complexity = result.get("time_complexity")
            cap.space_complexity = result.get("space_complexity")
            cap.notes = result.get("notes")
        db.commit()
        if telegram.enabled():
            try:
                if cap.status == "solved":
                    # send the screenshot first so the user sees WHICH screen was solved,
                    # then the full solution
                    telegram.broadcast_photo(db, raw, caption=f"📸 capture — {cap.id}")
                    telegram.broadcast(db, telegram.format_solution(cap))
                    # remember in chat history for follow-ups
                    for tchat in db.query(telegram.TelegramChat).all():
                        telegram.question_chat_id_holder["chat_id"] = tchat.chat_id
                        telegram.chat_history.add(
                            tchat.chat_id, "assistant",
                            f"Solution for capture #{cap.id}: "
                            + (cap.problem_statement or "")[:500],
                        )
                elif cap.status == "no_question":
                    # possible 'rest of the problem' continuation: a solved capture
                    # shortly before this one means the user may have scrolled
                    from datetime import timedelta

                    recent = (
                        db.query(Capture)
                        .filter(
                            Capture.id != cap.id,
                            Capture.status.in_(["solved", "analyzing", "pending"]),
                            Capture.created_at >= cap.created_at - timedelta(minutes=5),
                        )
                        .count()
                    )
                    if recent:
                        telegram.broadcast(
                            db,
                            "📸 New capture but no question on this screen.\n"
                            "If this is the REST of the previous problem, capture again "
                            "so both parts are on one screen (scroll so the earlier part "
                            "is visible too) — I solve what's visible in one capture.",
                        )
            except Exception:
                pass  # delivery must never break analysis
    finally:
        db.close()


@router.post("")
async def create_capture(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if file.content_type not in ALLOWED:
        raise HTTPException(415, "Only PNG/JPEG/WebP screenshots are supported")
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "Image too large (max 20 MB)")
    if not (GEMINI_API_KEY or OPENAI_API_KEY):
        raise HTTPException(503, "Server is not configured with a vision API key")

    path, _ = _save_image(data)
    cap = Capture(image_path=path, status="pending")
    db.add(cap)
    db.commit()
    db.refresh(cap)
    background.add_task(_run_analysis, cap.id)
    return {"id": cap.id, "status": cap.status}


@router.get("")
def list_captures(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(Capture).order_by(Capture.created_at.desc()).limit(min(limit, 200)).all()
    return [r.to_dict() for r in rows]


@router.get("/{capture_id}")
def get_capture(capture_id: int, db: Session = Depends(get_db)):
    cap = db.get(Capture, capture_id)
    if not cap:
        raise HTTPException(404, "capture not found")
    return cap.to_dict()


@router.get("/{capture_id}/image")
def get_image(capture_id: int, db: Session = Depends(get_db)):
    cap = db.get(Capture, capture_id)
    if not cap or not os.path.exists(cap.image_path):
        raise HTTPException(404, "image not found")
    with open(cap.image_path, "rb") as f:
        return Response(content=f.read(), media_type="image/png")
