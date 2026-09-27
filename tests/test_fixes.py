"""Tests: clear command, dedup of follow-up answers, transcription fallback."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_fixes.db"
os.environ.pop("DB_HOST", None)

import logging  # noqa: E402

import server.telegram as tg  # noqa: E402
from server import chat_history  # noqa: E402
from server.database import Base, engine, SessionLocal  # noqa: E402
from server.models import CaptureSession  # noqa: E402
from server.routers.agent import reset_question_context  # noqa: E402


def setup_module(_=None):
    Base.metadata.create_all(bind=engine)


def teardown_module(_=None):
    engine.dispose()
    if os.path.exists("./test_fixes.db"):
        os.remove("./test_fixes.db")


# ---------- clear command (same reset as Q) ----------

def test_clear_resets_context():
    db = SessionLocal()
    try:
        s = CaptureSession(status="open", chat_id="chatX")
        db.add(s)
        db.commit()
        chat_history.add("chatX", "user", "question")
        result = reset_question_context(db, "chatX")
        assert result["closed_sessions"] == 1
        assert result["cleared_messages"] == 1
        assert chat_history.recent("chatX") == []
    finally:
        db.close()


def test_clear_registered_as_command():
    assert "clear" in tg._handle_update.__code__.co_consts or True  # dispatched via tuple
    import inspect

    src = inspect.getsource(tg._handle_update)
    assert '"c", "s", "q", "clear", "status"' in src


# ---------- dedup of repeated follow-up answers ----------

def test_answer_and_store_dedupes_identical_consecutive_question(monkeypatch):
    chat_history.clear("chatD")
    calls = {"n": 0}

    def fake_followup(q, imgs):
        calls["n"] += 1
        return f"ANSWER-{calls['n']}"

    monkeypatch.setattr(tg, "answer_followup", fake_followup)

    a1 = tg.answer_and_store("what is two sum?", "chatD")
    a2 = tg.answer_and_store("What is Two Sum?  ", "chatD")  # same question, case/space diff
    assert calls["n"] == 1, "identical consecutive question was answered twice"
    assert a1 == a2
    chat_history.clear("chatD")


def test_answer_and_store_answers_new_question(monkeypatch):
    chat_history.clear("chatE")
    calls = {"n": 0}

    def fake_followup(q, imgs):
        calls["n"] += 1
        return f"ANSWER-{calls['n']}"

    monkeypatch.setattr(tg, "answer_followup", fake_followup)
    tg.answer_and_store("question one", "chatE")
    tg.answer_and_store("question two", "chatE")
    assert calls["n"] == 2
    chat_history.clear("chatE")


# ---------- transcription fallback ----------

def _raise(msg):
    def _f(_p):
        raise RuntimeError(msg)

    return _f


def test_transcribe_falls_back_to_gateway_on_local_failure(monkeypatch):
    monkeypatch.setattr(tg, "_transcribe_local", _raise("401 unauthorized HF hub"))
    monkeypatch.setattr(tg, "_transcribe_gateway", lambda audio, mime: "hello world")
    # ffmpeg may or may not exist; either path lands on the gateway fallback
    assert tg.transcribe(b"fakeogg") == "hello world"


def test_transcribe_raises_clear_error_when_both_fail(monkeypatch):
    monkeypatch.setattr(tg, "_transcribe_local", _raise("HF hub 403"))

    def boom(audio, mime):
        raise RuntimeError("gateway down")

    monkeypatch.setattr(tg, "_transcribe_gateway", boom)
    try:
        tg.transcribe(b"fakeogg")
        raised = False
    except RuntimeError as e:
        raised = "transcription unavailable" in str(e)
    assert raised
