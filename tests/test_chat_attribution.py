"""Tests: capture commands attribute sessions to the requesting chat."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_chatattr.db"
os.environ.pop("DB_HOST", None)

from server.database import Base, SessionLocal, engine  # noqa: E402
from server.models import CaptureSession  # noqa: E402
from server.routers import agent as agent_api  # noqa: E402


def setup_module(_=None):
    Base.metadata.create_all(bind=engine)


def teardown_module(_=None):
    engine.dispose()
    if os.path.exists("./test_chatattr.db"):
        os.remove("./test_chatattr.db")


def test_issue_command_stores_chat_id():
    agent_api.issue_capture_command(chat_id="7635406326")
    assert agent_api.pending_chat_id() == "7635406326"


def test_default_pending_chat_is_dashboard():
    with agent_api._lock:
        agent_api._pending.pop("default", None)
    assert agent_api.pending_chat_id() == "dashboard"


def test_capture_lands_in_issuing_chats_session():
    from PIL import Image
    import io

    from fastapi.testclient import TestClient

    from server.main import app

    agent_api.issue_capture_command(chat_id="chatT")
    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, "PNG")
    buf.seek(0)
    client = TestClient(app)
    r = client.post("/api/agent/capture", files={"file": ("s.png", buf, "image/png")})
    assert r.status_code == 200
    body = r.json()
    db = SessionLocal()
    try:
        sess = db.get(CaptureSession, body["session_id"])
        assert sess.chat_id == "chatT", f"capture filed under {sess.chat_id}, not chatT"
    finally:
        db.close()
