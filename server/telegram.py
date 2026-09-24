"""Telegram delivery: /start link-up + solution push."""

import base64
import html
import threading
import time
from datetime import datetime

import httpx
from sqlalchemy import Column, DateTime, Integer, String

from server import chat_history
from server.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    STT_MODEL,
    TELEGRAM_BOT_TOKEN,
    VISION_MODEL,
)
from server.database import Base

API = "https://api.telegram.org/bot"


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(64), unique=True, nullable=False)
    title = Column(String(256), nullable=True)
    linked_at = Column(DateTime, nullable=True)


def enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)


def _call(method: str, **payload):
    if not enabled():
        return None
    try:
        r = httpx.post(f"{API}{TELEGRAM_BOT_TOKEN}/{method}", json=payload, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def send_message(chat_id: str, text: str) -> bool:
    ok = _call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
               disable_web_page_preview=True)
    return bool(ok and ok.get("ok"))


def send_photo(chat_id: str, png: bytes, caption: str | None = None) -> bool:
    if not enabled():
        return False
    try:
        files = {"photo": ("capture.png", png, "image/png")}
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "HTML"
        r = httpx.post(f"{API}{TELEGRAM_BOT_TOKEN}/sendPhoto",
                       data=data, files=files, timeout=60)
        return r.status_code == 200 and r.json().get("ok")
    except Exception:
        return False


def link_chat(db, chat_id, title=None) -> None:
    from datetime import datetime, timezone

    if db.query(TelegramChat).filter_by(chat_id=str(chat_id)).first():
        return
    db.add(TelegramChat(chat_id=str(chat_id), title=title,
                        linked_at=datetime.now(timezone.utc)))
    db.commit()


def broadcast(db, text: str) -> int:
    sent = 0
    for chat in db.query(TelegramChat).all():
        if send_message(chat.chat_id, text):
            sent += 1
    return sent


def broadcast_photo(db, png: bytes, caption: str | None = None) -> int:
    sent = 0
    for chat in db.query(TelegramChat).all():
        if send_photo(chat.chat_id, png, caption):
            sent += 1
    return sent


def download_file(file_id: str) -> bytes | None:
    if not enabled():
        return None
    try:
        r = httpx.get(f"{API}{TELEGRAM_BOT_TOKEN}/getFile",
                      params={"file_id": file_id}, timeout=30)
        path = r.json().get("result", {}).get("file_path")
        if not path:
            return None
        f = httpx.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{path}",
                      timeout=120)
        return f.content or None
    except Exception:
        return None


def transcribe(ogg_bytes: bytes) -> str:
    """Speech-to-text: local faster-whisper first, gateway fallback."""
    import tempfile

    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(ogg_bytes)
        ogg_path = f.name
    wav_path = ogg_path.replace(".ogg", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=60, check=True,
        )
    except Exception:
        wav_path = ogg_path  # backend may support ogg directly

    try:
        return _transcribe_local(wav_path)
    except Exception:
        if wav_path != ogg_path:
            return _transcribe_gateway(ogg_path, "audio/ogg")
        raise


_local_model = None
_local_lock = threading.Lock()


def _get_local_model():
    global _local_model
    with _local_lock:
        if _local_model is None:
            from faster_whisper import WhisperModel

            _local_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        return _local_model


def _transcribe_local(wav_path: str) -> str:
    model = _get_local_model()
    segments, _info = model.transcribe(wav_path)
    return " ".join(s.text.strip() for s in segments).strip()


def _transcribe_gateway(audio: bytes, mime: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("No speech-to-text backend configured")
    r = httpx.post(
        f"{OPENAI_BASE_URL.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"file": ("voice.ogg", audio, mime)},
        data={"model": STT_MODEL},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"transcription failed: HTTP {r.status_code}")
    return (r.json().get("text") or "").strip()


FOLLOWUP_PROMPT = """You are ScreenSolve, a programming tutor chatting on Telegram.

Below is the recent conversation with this user, including solutions you delivered
for screen captures. The user asks a follow-up question (typed or spoken).

Answer concisely in plain text (Telegram HTML allowed: <b>, <code>, <pre>).
Ground the answer in the recent solution when relevant — quote its steps/code.
If there is no relevant capture context, answer generally and say so.

Recent conversation:
{history}

User's new question: {question}

Your answer:"""


def answer_followup(question: str) -> str:
    history = chat_history.recent(question_chat_id_holder.get("chat_id", ""))
    hist_text = "\n".join(
        f"[{m['role']}] {m['content'][:800]}" for m in history
    ) or "(none — no captures delivered to this chat yet)"
    full = FOLLOWUP_PROMPT.format(history=hist_text, question=question)

    if GEMINI_API_KEY:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=[full],
            config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=2048),
        )
        return (resp.text or "").strip()

    if OPENAI_API_KEY:
        r = httpx.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": VISION_MODEL,
                "temperature": 0.3,
                "max_tokens": 2048,
                "messages": [{"role": "user", "content": full}],
            },
            timeout=120,
        )
        r.raise_for_status()
        return (r.json()["choices"][0]["message"]["content"] or "").strip()

    raise RuntimeError("No LLM configured for follow-up answers")


# set per-chat before calling answer_followup
question_chat_id_holder: dict = {}


def _handle_session_command(cmd: str, chat_id: str) -> None:
    from server.database import SessionLocal
    from server.models import Capture, CaptureSession
    from server.routers import agent as agent_api
    from server.gemini import analyze_images_multi

    db = SessionLocal()
    try:
        if cmd == "c":
            agent_api.issue_capture_command()
            open_n = db.query(CaptureSession).filter_by(status="open").first()
            parts = (
                db.query(Capture).filter_by(session_id=open_n.id, status="captured").count()
                if open_n else 0
            )
            send_message(
                chat_id,
                "📸 Capturing your screen…\n"
                + (f"Session has {parts} part(s) so far. " if parts else "")
                + "Scroll and send c again for more parts, or s to solve.",
            )

        elif cmd == "s":
            sess = db.query(CaptureSession).filter_by(status="open").first()
            if not sess:
                send_message(chat_id, "No capture session open. Send c first to capture your screen.")
                return
            parts = db.query(Capture).filter_by(session_id=sess.id, status="captured").count()
            if not parts:
                send_message(chat_id, "Session is empty — send c to capture the screen first.")
                return
            send_message(chat_id, f"🧠 Solving with {parts} part(s)…")
            sess.status = "solving"
            db.commit()
            images = agent_api.session_images(db, sess.id)
            try:
                result = analyze_images_multi(images)
            except Exception as e:  # noqa: BLE001
                sess.status = "open"  # allow retry
                db.commit()
                send_message(chat_id, f"⚠️ Analysis failed: {str(e)[:180]}")
                return

            # store as one capture row (primary image = first part)
            first = (
                db.query(Capture)
                .filter_by(session_id=sess.id, status="captured")
                .order_by(Capture.id.asc())
                .first()
            )
            now = datetime.utcnow()
            cap = Capture(
                image_path=first.image_path,
                session_id=sess.id,
                created_at=now,
                analyzed_at=now,
            )
            if not result.get("has_question"):
                cap.status = "no_question"
                cap.notes = "No coding question detected across the captured parts."
                db.add(cap)
                sess.status = "solved"
                sess.solved_at = now
                db.commit()
                send_message(chat_id, "🤔 No coding question found in the captured parts.")
                return
            cap.status = "solved"
            cap.problem_statement = result.get("problem_statement")
            cap.user_attempt = result.get("user_attempt")
            cap.solution_steps = result.get("solution_steps")
            cap.optimized_code = result.get("optimized_code")
            cap.time_complexity = result.get("time_complexity")
            cap.space_complexity = result.get("space_complexity")
            cap.notes = result.get("notes")
            db.add(cap)
            sess.status = "solved"
            sess.solved_at = now
            db.commit()
            db.refresh(cap)

            # deliver: each part screenshot, then the solution
            for i, img in enumerate(images, 1):
                send_photo(chat_id, img, caption=f"part {i}/{len(images)}")
            send_message(chat_id, format_solution(cap))
            chat_history.add(chat_id, "assistant",
                             f"Solution for capture #{cap.id} ({len(images)} parts): "
                             + (cap.problem_statement or "")[:500])

        elif cmd == "status":
            sess = db.query(CaptureSession).filter_by(status="open").first()
            if not sess:
                send_message(chat_id, "No open session. Send c to start capturing.")
            else:
                parts = db.query(Capture).filter_by(session_id=sess.id, status="captured").count()
                send_message(chat_id, f"Open session: {parts} part(s) captured. c = add part, s = solve.")
    finally:
        db.close()


def format_solution(capture) -> str:
    e = html.escape
    parts = ["<b>📸 ScreenSolve — new solution</b>"]
    if capture.problem_statement:
        parts.append("\n<b>Problem:</b>\n" + e(capture.problem_statement))
    if capture.solution_steps:
        items = []
        for i, s in enumerate(capture.solution_steps):
            if isinstance(s, dict):
                text = s.get("step") or ""
                code = s.get("code")
                if code:
                    items.append(f"<b>step {i+1}.</b> {e(text)}\n<pre>{e(code)}</pre>")
                else:
                    items.append(f"<b>step {i+1}.</b> {e(text)}")
            else:
                items.append(f"<b>step {i+1}.</b> {e(str(s))}")
        parts.append("\n<b>Approach (building up):</b>\n" + "\n\n".join(items))
    if capture.optimized_code:
        parts.append("\n<b>Optimized code:</b>\n<pre>" + e(capture.optimized_code) + "</pre>")
    badges = [b for b in (capture.time_complexity, capture.space_complexity) if b]
    if badges:
        parts.append("\n<b>Complexity:</b> " + " · ".join(e(b) for b in badges))
    if capture.notes:
        parts.append("\n<b>Feedback:</b>\n" + e(capture.notes))
    return "\n".join(parts)


def _poll_loop() -> None:
    from server.database import SessionLocal

    offset = 0
    while True:
        if not enabled():
            time.sleep(30)
            continue
        try:
            r = httpx.get(
                f"{API}{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"timeout": 25, "offset": offset},
                timeout=35,
            )
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if not chat_id:
                    continue
                db = SessionLocal()
                try:
                    link_chat(db, chat_id, chat.get("title"))
                finally:
                    db.close()

                voice = msg.get("voice") or msg.get("audio")
                text = (msg.get("text") or "").strip()

                if text and text.strip().lower() in ("c", "s", "status"):
                    _handle_session_command(text.strip().lower(), str(chat_id))
                    continue

                if voice:
                    file_id = voice.get("file_id")
                    raw = download_file(file_id)
                    if not raw:
                        send_message(chat_id, "⚠️ Couldn't download the voice message — try again.")
                        continue
                    try:
                        question = transcribe(raw)
                    except Exception as e:  # noqa: BLE001
                        send_message(chat_id, f"⚠️ Couldn't transcribe the voice message ({str(e)[:120]}). You can type the question instead.")
                        continue
                    if not question:
                        send_message(chat_id, "🤔 The voice message came through silent — say it again?")
                        continue
                    send_message(chat_id, f"🎙️ Heard: “{question}”")
                elif text and text.startswith("/"):
                    continue  # commands handled elsewhere
                elif text:
                    question = text
                else:
                    continue

                # follow-up question path
                question_chat_id_holder["chat_id"] = str(chat_id)
                chat_history.add(chat_id, "user", question)
                try:
                    answer = answer_followup(question)
                except Exception as e:  # noqa: BLE001
                    answer = f"⚠️ Couldn't answer that right now: {str(e)[:150]}"
                chat_history.add(chat_id, "assistant", answer)
                send_message(chat_id, answer)
        except Exception:
            time.sleep(10)


def start_bot() -> None:
    if not enabled():
        return
    threading.Thread(target=_poll_loop, daemon=True).start()
