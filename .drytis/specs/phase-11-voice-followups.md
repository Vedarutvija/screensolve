# Phase 11 spec — Voice follow-ups via Telegram

## Goal
User records a Telegram voice message in the bot chat → server transcribes it (speech-to-text) → treats it as a follow-up question about the recent solution(s) sent to that chat → replies in text in the same chat.

## Design
- **Ingestion**: extend the existing bot long-poll in `server/telegram.py` to also handle `voice`/`audio` messages. Download the OGG/Opus file via `getFile` + file URL.
- **Transcription**: Gemini 2.0 Flash accepts audio inline; fallback = OpenAI-compatible `audio/transcriptions` (whisper) on the workspace gateway if supported. Env: reuse GEMINI_API_KEY or OPENAI_* keys; new env `STT_MODEL` optional.
- **Context**: keep last N (10) messages per chat in a new `chat_history` table (chat_id, role, text, created_at). Every bot reply and every capture solution delivered to a chat is appended. Follow-up prompt = recent history + latest transcribed question → answer in text.
- **Reply**: `send_message` in same chat; append Q and A to history.
- Text messages sent to the bot (non-voice) should ALSO be treated as follow-up questions — same path after transcription (skipped).

## Files
- server/telegram.py (ingest voice, download, transcribe dispatch, history helpers)
- server/chat_history.py (model + helpers) or add to telegram.py
- server/config.py (STT env keys)
- .env keys: STT_PROVIDER, STT_MODEL (optional)

## Acceptance criteria
- [ ] Voice message in bot chat → text answer in same chat referencing the recent solution
- [ ] Plain text follow-up questions work the same way
- [ ] History persists across restarts (DB table)
- [ ] No transcription backend available → clear error message to the chat

## Edge cases
- OGG/Opus conversion if backend needs WAV (use ffmpeg if present, else pick backend that accepts OGG)
- Long voice (>2min) → still works via file download
- No recent solution in history → answer generally, noting no capture context
