"""Phase 2 tests: mixed input (text / audio / image / combined)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_mixed.db"
os.environ.pop("DB_HOST", None)

from fastapi.testclient import TestClient  # noqa: E402

from server.database import Base, engine, SessionLocal  # noqa: E402
from server import chat_history  # noqa: E402


def setup_module(_=None):
    Base.metadata.create_all(bind=engine)


def teardown_module(_=None):
    engine.dispose()
    if os.path.exists("./test_mixed.db"):
        os.remove("./test_mixed.db")


def _client():
    from server.main import app

    return TestClient(app)


def _png() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    return buf.getvalue()


def _no_llm(monkeypatch):
    """Point answer paths at stubs so tests don't call a real LLM."""
    import server.main as m

    monkeypatch.setattr(
        m.telegram, "answer_followup",
        lambda q, imgs: f"TEXT-ONLY ANSWER to: {q[:50]} (imgs={len(imgs)})",
    )

    def _fake_with_image(question, image_bytes, chat_id):
        return f"IMAGE ANSWER to: {question[:50]}"

    monkeypatch.setattr(m.telegram, "answer_question_with_image", _fake_with_image)


def test_mixed_text_only(monkeypatch):
    _no_llm(monkeypatch)
    r = _client().post("/api/chat/mixed", data={"text": "solve two-sum with a hash map"})
    assert r.status_code == 200
    body = r.json()
    assert "TEXT-ONLY ANSWER" in body["answer"]
    assert body["transcript"] is None


def test_mixed_image_only(monkeypatch):
    _no_llm(monkeypatch)
    r = _client().post(
        "/api/chat/mixed",
        files={"image": ("q.png", _png(), "image/png")},
    )
    assert r.status_code == 200
    assert "IMAGE ANSWER" in r.json()["answer"]


def test_mixed_text_plus_image(monkeypatch):
    _no_llm(monkeypatch)
    r = _client().post(
        "/api/chat/mixed",
        data={"text": "optimize this using recursion"},
        files={"image": ("q.png", _png(), "image/png")},
    )
    assert r.status_code == 200
    assert "IMAGE ANSWER" in r.json()["answer"]


def test_mixed_empty_payload_rejected():
    r = _client().post("/api/chat/mixed")
    assert r.status_code == 422


def test_mixed_bad_image_type():
    r = _client().post(
        "/api/chat/mixed",
        files={"image": ("x.gif", b"GIF89a", "image/gif")},
    )
    assert r.status_code == 415


def test_mixed_stores_question_in_history(monkeypatch):
    _no_llm(monkeypatch)
    chat_history.clear("dashboard")
    _client().post("/api/chat/mixed", data={"text": "what is a binary gap?"})
    hist = chat_history.recent("dashboard")
    assert any("binary gap" in m["content"] for m in hist)
    chat_history.clear("dashboard")
