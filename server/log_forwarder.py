"""Forward server logs to linked Telegram chats.

A logging.Handler that pushes WARNING+ records to every linked Telegram chat,
so the owner can monitor the server remotely. Safety rails:

- level filter: INFO/DEBUG never forwarded (env LOGS_TO_TELEGRAM_LEVEL, default WARNING)
- master switch: env LOGS_TO_TELEGRAM ("0" disables)
- rate limit: max RATE_LIMIT_N messages per RATE_LIMIT_WINDOW seconds; bursts are
  collapsed, so a crash loop can't spam the chat
- recursion guard: the handler never forwards its own internal errors, and
  telegram._call failures are swallowed
"""

import logging
import os
import threading
import time


class TelegramLogHandler(logging.Handler):
    def __init__(self, level: int = logging.WARNING):
        super().__init__(level=level)
        self._lock = threading.Lock()
        self._sent: list[float] = []
        self._in_forward = False  # recursion guard

    # ---- config ----

    @staticmethod
    def enabled() -> bool:
        return os.getenv("LOGS_TO_TELEGRAM", "1") not in ("0", "false", "False")

    @staticmethod
    def _rate_limit() -> tuple[int, float]:
        n = int(os.getenv("LOGS_TO_TELEGRAM_MAX", "5"))
        window = float(os.getenv("LOGS_TO_TELEGRAM_WINDOW", "30"))
        return n, window

    # ---- rate limiting ----

    def _allow(self, now: float) -> bool:
        n, window = self._rate_limit()
        with self._lock:
            self._sent = [t for t in self._sent if now - t < window]
            if len(self._sent) >= n:
                return False
            self._sent.append(now)
            return True

    # ---- logging API ----

    def emit(self, record: logging.LogRecord) -> None:
        # never forward anything originating from forwarding itself
        if self._in_forward or record.name.startswith("screensolve.telegram"):
            return
        if not self.enabled():
            return
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001 — formatting must never raise
            return
        if not self._allow(time.time()):
            return
        self._in_forward = True
        try:
            self._broadcast(text)
        except Exception:  # noqa: BLE001 — logging must never raise
            pass
        finally:
            self._in_forward = False

    def _broadcast(self, text: str) -> None:
        from server.database import SessionLocal
        from server import telegram

        if not telegram.enabled():
            return
        db = SessionLocal()
        try:
            telegram.broadcast(db, telegram.markdown_to_telegram_html(text)[:3500])
        finally:
            db.close()


def attach() -> logging.Handler:
    """Attach the Telegram handler to the root logger (and uvicorn error log).
    Idempotent — returns the existing handler if already attached."""
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, TelegramLogHandler):
            return h
    handler = TelegramLogHandler(level=_level())
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    for name in ("uvicorn.error", "uvicorn"):
        logging.getLogger(name).addHandler(handler)
    return handler


def _level() -> int:
    name = os.getenv("LOGS_TO_TELEGRAM_LEVEL", "WARNING").upper()
    return getattr(logging, name, logging.WARNING)
