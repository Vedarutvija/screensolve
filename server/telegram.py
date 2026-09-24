"""Telegram delivery: /start link-up + solution push."""

import html
import threading
import time

import httpx
from sqlalchemy import Column, DateTime, Integer, String

from server.config import TELEGRAM_BOT_TOKEN
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
                    send_message(chat_id, "✅ Linked! ScreenSolve solutions will be sent here.")
                finally:
                    db.close()
        except Exception:
            time.sleep(10)


def start_bot() -> None:
    if not enabled():
        return
    threading.Thread(target=_poll_loop, daemon=True).start()
