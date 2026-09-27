"""Phase 1 tests: "Q" command — new question context isolation."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force an isolated SQLite DB before importing server modules.
os.environ["DATABASE_URL"] = "sqlite:///./test_q.db"
os.environ.pop("DB_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402

from server.database import Base, SessionLocal, engine  # noqa: E402
from server import chat_history  # noqa: E402
from server.models import Capture, CaptureSession  # noqa: E402
from server.routers.agent import (  # noqa: E402
    open_session,
    reset_question_context,
)


def setup_module(_=None):
    Base.metadata.create_all(bind=engine)


def teardown_module(_=None):
    engine.dispose()
    if os.path.exists("./test_q.db"):
        os.remove("./test_q.db")


def _mk_session(db, chat_id, status="open"):
    s = CaptureSession(status=status, chat_id=chat_id)
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _mk_capture(db, session_id, status="captured"):
    import tempfile

    img = os.path.join(tempfile.gettempdir(), f"tq_{session_id}_{next(_mk_capture._n)}.png")
    with open(img, "wb") as f:
        f.write(b"\x89PNG fake")
    c = Capture(image_path=img, status=status, session_id=session_id)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


import itertools  # noqa: E402

_mk_capture._n = itertools.count()


# ---------- chat_history.clear ----------

def test_clear_deletes_only_that_chat():
    chat_history.add("chatA", "user", "hello A")
    chat_history.add("chatA", "assistant", "answer A")
    chat_history.add("chatB", "user", "hello B")
    n = chat_history.clear("chatA")
    assert n == 2
    assert chat_history.recent("chatA") == []
    assert len(chat_history.recent("chatB")) == 1
    chat_history.clear("chatB")


def test_clear_empty_chat_is_zero():
    assert chat_history.clear("never-existed") == 0


# ---------- reset_question_context ----------

def test_reset_closes_open_and_solving_sessions_only():
    db = SessionLocal()
    try:
        s1 = _mk_session(db, "chatA", "open")
        s2 = _mk_session(db, "chatA", "solving")
        s3 = _mk_session(db, "chatA", "solved")
        _mk_session(db, "chatB", "open")
        chat_history.add("chatA", "user", "q1")
        result = reset_question_context(db, "chatA")
        assert result["closed_sessions"] == 2
        assert result["cleared_messages"] == 1
        db.expire_all()
        assert db.get(CaptureSession, s1.id).status == "closed"
        assert db.get(CaptureSession, s2.id).status == "closed"
        assert db.get(CaptureSession, s3.id).status == "solved"  # untouched
        # chatB unaffected
        assert db.query(CaptureSession).filter_by(chat_id="chatB", status="open").count() == 1
        assert chat_history.recent("chatA") == []
    finally:
        db.close()
        chat_history.clear("chatA")
        chat_history.clear("chatB")


def test_reset_with_no_open_session_is_safe():
    db = SessionLocal()
    try:
        result = reset_question_context(db, "chat-empty")
        assert result["closed_sessions"] == 0
        assert result["cleared_messages"] == 0
    finally:
        db.close()


# ---------- per-chat session isolation ----------

def test_open_session_is_per_chat_and_never_reuses_closed():
    db = SessionLocal()
    try:
        closed = _mk_session(db, "chatA", "open")
        closed.status = "closed"
        db.commit()
        s = open_session(db, "chatA")
        assert s.id != closed.id
        assert s.chat_id == "chatA"
        # second call reuses the same open session
        assert open_session(db, "chatA").id == s.id
        # another chat gets its own
        assert open_session(db, "chatB").id != s.id
    finally:
        db.close()


# ---------- recent_session_images scoping ----------

def test_recent_session_images_skips_closed_and_scopes_chat():
    from server.telegram import recent_session_images

    db = SessionLocal()
    try:
        s_old = _mk_session(db, "chatA", "solved")  # previous question
        _mk_capture(db, s_old.id)
        s_closed = _mk_session(db, "chatA", "closed")
        _mk_capture(db, s_closed.id)
        # open session for a different chat
        s_b = _mk_session(db, "chatB", "open")
        _mk_capture(db, s_b.id)
        # chatA's images come only from non-closed chatA sessions (old solved)
        imgs = recent_session_images(db, "chatA")
        assert len(imgs) == 1
        # an isolated chat whose only sessions are closed gets nothing
        s_c = _mk_session(db, "chatC", "closed")
        _mk_capture(db, s_c.id)
        assert recent_session_images(db, "chatC") == []
        # chatD (no sessions) also empty
        assert recent_session_images(db, "chatD") == []
    finally:
        db.close()


# ---------- API endpoint ----------

def test_api_session_new_endpoint():
    from server.main import app

    chat_history.add("dashboard", "user", "old question")
    _mk_session(SessionLocal(), "dashboard", "open")
    client = TestClient(app)
    r = client.post("/api/session/new")
    assert r.status_code == 200
    body = r.json()
    assert body["chat_id"] == "dashboard"
    assert body["closed_sessions"] >= 1
    assert body["cleared_messages"] >= 1
    assert chat_history.recent("dashboard") == []
    chat_history.clear("dashboard")
