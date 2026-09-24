# Phase 12 spec — c/s capture sessions (Telegram-commanded)

## Problem
Agent's periodic mode captures in a loop and floods analysis. User wants explicit control from Telegram: `c` = capture current screen, `s` = solve all captured parts together as ONE question.

## Design
- **Agent runs on the laptop; server can't reach it** → agent LONG-POLLS the server for commands:
  - `GET /api/agent/poll?agent_id=...` → returns pending command (`capture` / none); agent checks every ~1.5s
  - `POST /api/agent/capture` (existing upload, new field `session_part=true`) → stored in the OPEN capture session
- **Sessions**: new `capture_sessions` table (id, status open/solving/solved, created_at). Captures get `session_id`. One open session at a time; `c` while open appends, if none open a new one is created.
- **Telegram commands** (handled in telegram.py poll loop):
  - `c` → enqueue `capture` command → reply "📸 Capturing your screen… (send c again after scrolling for more parts, s to solve)"
  - `s` → close session → analyze ALL its images in ONE Gemini call (multi-image contents) → reply with solution (+ screenshot of FIRST part via broadcast_photo + each part numbered) → mark solved
  - `status` → parts so far
- **Agent changes**: periodic mode becomes OFF by default; new `mode: telegram` (default) = just poll for commands + upload on demand. Keep `periodic` and `hotkey` modes selectable.
- **Analysis**: multi-image prompt — concatenate problem across parts, single solution_steps output (existing additive prompt), stored as ONE capture row (primary image = first part, additional parts listed).
- Old flow's no_question nudge no longer needed for command mode; keep for other modes.

## Files
- server/models.py: CaptureSession model, Capture.session_id
- server/routers/agent.py: /api/agent/poll, /api/agent/capture, session helpers
- server/routers/captures.py: session_id support; keep legacy single-shot POST working
- server/telegram.py: c/s/status commands; multi-image solve; command queue helpers
- server/gemini.py: analyze_images_multi(parts) — list of images, one JSON out
- agent/main.py: mode: telegram (poll → capture → upload as session part), keep periodic/hotkey; config.yaml default change

## Acceptance criteria
- [ ] Agent in telegram mode captures ONLY when 'c' is sent; no loop captures
- [ ] Sending c twice (with scroll between) yields 2 parts in one open session
- [ ] Sending s produces ONE combined solution covering all parts, delivered with the screenshots to Telegram
- [ ] status shows how many parts are captured
- [ ] periodic/hotkey modes still available via config
