# Phase 2 — Capture ingestion + Gemini analysis
## Goal
POST /api/captures (PNG upload → disk), background Gemini vision analysis (gemini-2.0-flash) with Python-coding-specialized prompt; structured result stored; status transitions pending→analyzing→solved|no_question|error.
## Acceptance Criteria
- [ ] Screenshot of a Python problem becomes a card with detected problem, step-by-step optimized solution, complexity, highlighted code
- [ ] Screenshot with no question marked "No question detected", not an error
- [ ] Gemini failure shows clear error state on card
## Edge cases
Large images downscaled; missing API key → immediate clear error.
