from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.database import Base, engine, run_startup_migrations
from server.routers import agent as agent_router
from server.routers import captures
from server import telegram

Base.metadata.create_all(bind=engine)
try:
    run_startup_migrations()
except Exception:
    import logging

    logging.getLogger("screensolve").exception("startup migration failed")

app = FastAPI(title="ScreenSolve", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(captures.router)
app.include_router(agent_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}


DASHBOARD_CHAT_ID = "dashboard"


class ChatIn(BaseModel):
    text: str


@app.post("/api/chat")
def dashboard_chat(body: ChatIn):
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    try:
        answer = telegram.answer_and_store(text, DASHBOARD_CHAT_ID)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"answer failed: {str(e)[:180]}")
    return {"answer": answer}


@app.post("/api/chat/voice")
async def dashboard_chat_voice(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        raise HTTPException(400, "audio file is required")
    try:
        question = telegram.transcribe(raw)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"transcription failed: {str(e)[:150]}")
    if not question:
        raise HTTPException(422, "audio came through silent — try again")
    try:
        answer = telegram.answer_and_store(question, DASHBOARD_CHAT_ID)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"answer failed: {str(e)[:180]}")
    return {"transcript": question, "answer": answer}


@app.post("/api/chat/mixed")
async def dashboard_chat_mixed(
    text: str | None = Form(None),
    audio: UploadFile | None = File(None),
    image: UploadFile | None = File(None),
    chat_id: str = Form(DASHBOARD_CHAT_ID),
):
    """Initial question as text, audio, image, or ANY combination — one request
    is ONE question. Audio is transcribed; text + transcript are combined; the
    image (if any) is analyzed together with the text as a single problem."""
    text = (text or "").strip()
    image_bytes: bytes | None = None
    if image is not None and image.filename:
        if image.content_type not in ("image/png", "image/jpeg", "image/webp"):
            raise HTTPException(415, "Only PNG/JPEG/WebP images are supported")
        image_bytes = await image.read()
        if len(image_bytes) > 20 * 1024 * 1024:
            raise HTTPException(413, "Image too large (max 20 MB)")

    transcript = ""
    if audio is not None and audio.filename:
        raw_audio = await audio.read()
        if raw_audio:
            try:
                transcript = telegram.transcribe(raw_audio)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(502, f"transcription failed: {str(e)[:150]}")

    question = " ".join(p for p in (text, transcript) if p).strip()
    if not question and not image_bytes:
        raise HTTPException(422, "send text, audio, an image, or any combination")

    # store the question in chat history for follow-ups
    telegram.question_chat_id_holder["chat_id"] = chat_id
    telegram.chat_history.add(chat_id, "user", question or "(image only)")

    try:
        if image_bytes:
            answer = telegram.answer_question_with_image(
                question or "Solve the question visible in the attached screenshot.",
                image_bytes, chat_id)
        else:
            answer = telegram.answer_followup(question, [])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"answer failed: {str(e)[:180]}")
    telegram.chat_history.add(chat_id, "assistant", answer)
    return {"transcript": transcript or None, "answer": answer}


telegram.start_bot()


@app.post("/api/session/new")
def new_question_session(chat_id: str = DASHBOARD_CHAT_ID):
    """\"Q\" command for the dashboard: end the current question's context —
    close the open capture session and clear this chat's history so the next
    capture/question starts fresh."""
    from server.database import SessionLocal
    from server.routers.agent import reset_question_context

    db = SessionLocal()
    try:
        result = reset_question_context(db, chat_id)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"reset failed: {str(e)[:180]}")
    finally:
        db.close()
    return result


DASH = Path(__file__).resolve().parent.parent / "dashboard"

app.mount("/assets", StaticFiles(directory=DASH / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(DASH / "index.html")
