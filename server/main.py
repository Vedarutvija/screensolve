from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from server.database import Base, engine
from server.routers import agent as agent_router
from server.routers import captures
from server import telegram

Base.metadata.create_all(bind=engine)

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


telegram.start_bot()


DASH = Path(__file__).resolve().parent.parent / "dashboard"

app.mount("/assets", StaticFiles(directory=DASH / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(DASH / "index.html")
