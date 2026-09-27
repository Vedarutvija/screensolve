# Phase 1 · "Q" command — new question context

## Goal
Typing "Q" (Telegram or dashboard) ends the current question's context: closes the
open capture session and clears that chat's history, so the next capture/question
starts genuinely fresh and never mixes with previous question parts.

## Root causes fixed
- Single global open session (`get_open_session()` first-open-row) → sessions now
  per-chat (`CaptureSession.chat_id`).
- `recent_session_images()` attached the most recent session's images regardless of
  topic → now scoped by chat and skips `closed` sessions.
- `chat_history` never cleared per question → `chat_history.clear(chat_id)`.

## Files changed
- server/models.py — CaptureSession.chat_id column; status now open|solving|solved|closed
- server/database.py — run_startup_migrations() adds chat_id to existing tables
- server/chat_history.py — clear(chat_id)
- server/routers/agent.py — open_session/get_open_session per-chat; reset_question_context()
- server/telegram.py — "q" command; per-chat session lookups; scoped recent images
- server/main.py — POST /api/session/new
- dashboard/index.html + assets/app.js — 🆕 New Question button

## Acceptance criteria
- [x] "Q" in bot chat closes open session + clears history, confirms to user
- [x] After "Q", new capture + "s" answers only the new screenshots
- [x] After "Q", follow-ups no longer receive previous session's images
- [x] Dashboard 🆕 button calls POST /api/session/new and confirms
- [x] Normal flow without "Q" unchanged (follow-ups keep current-question context)

## Tests
- tests/test_q_command.py — unit tests for clear(), reset_question_context(),
  per-chat session isolation, recent_session_images scoping/closed-skipping.
