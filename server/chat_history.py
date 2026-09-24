"""Per-chat conversation history for follow-up questions."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from server.database import Base


class ChatMessage(Base):
    __tablename__ = "chat_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(64), index=True, nullable=False)
    role = Column(String(16), nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


def add(chat_id: str, role: str, content: str) -> None:
    from server.database import SessionLocal

    db = SessionLocal()
    try:
        db.add(ChatMessage(chat_id=str(chat_id), role=role, content=content[:4000]))
        db.commit()
    finally:
        db.close()


def recent(chat_id: str, limit: int = 10) -> list[dict]:
    from server.database import SessionLocal

    db = SessionLocal()
    try:
        rows = (
            db.query(ChatMessage)
            .filter_by(chat_id=str(chat_id))
            .order_by(ChatMessage.id.desc())
            .limit(limit)
            .all()
        )
        return [{"role": r.role, "content": r.content} for r in reversed(rows)]
    finally:
        db.close()
