"""Tests: Telegram log forwarding (filter, rate limit, recursion guard)."""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_logs.db"
os.environ.pop("LOGS_TO_TELEGRAM_LEVEL", None)
os.environ.pop("LOGS_TO_TELEGRAM_MAX", None)

from server.log_forwarder import TelegramLogHandler, attach  # noqa: E402


def _handler(monkeypatch, sent: list):
    h = TelegramLogHandler()
    h._broadcast = lambda text: sent.append(text)
    return h


def test_info_not_forwarded_warning_is(monkeypatch):
    sent = []
    h = _handler(monkeypatch, sent)
    log = logging.getLogger("t.info")
    log.addHandler(h)
    log.setLevel(logging.DEBUG)
    log.info("just info")
    log.warning("a warning")
    assert sent == [s for s in sent if "a warning" in s]
    assert not any("just info" in s for s in sent)
    log.removeHandler(h)


def test_rate_limit_blocks_burst(monkeypatch):
    sent = []
    h = _handler(monkeypatch, sent)
    recs = [
        logging.LogRecord("t.r", logging.ERROR, "p", 1, f"err {i}", None, None)
        for i in range(20)
    ]
    for r in recs:
        h.emit(r)
    n, _w = h._rate_limit()
    assert len(sent) == n


def test_recursion_guard(monkeypatch):
    sent = []
    h = _handler(monkeypatch, sent)
    # handler already forwarding → emit is a no-op
    h._in_forward = True
    h.emit(logging.LogRecord("t.g", logging.ERROR, "p", 1, "boom", None, None))
    assert sent == []


def test_telegram_logger_not_forwarded(monkeypatch):
    sent = []
    h = _handler(monkeypatch, sent)
    h.emit(logging.LogRecord("screensolve.telegram", logging.ERROR, "p", 1, "tg err", None, None))
    assert sent == []


def test_disabled_by_env(monkeypatch):
    sent = []
    h = _handler(monkeypatch, sent)
    monkeypatch.setenv("LOGS_TO_TELEGRAM", "0")
    h.emit(logging.LogRecord("t.d", logging.ERROR, "p", 1, "off", None, None))
    assert sent == []


def test_attach_is_idempotent():
    h1 = attach()
    h2 = attach()
    assert h1 is h2
    logging.getLogger().removeHandler(h1)
