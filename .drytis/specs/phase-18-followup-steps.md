# Phase 18 · Step-by-step additive code for voice/text follow-ups

## Problem
`answer_followup()` in `server/telegram.py` answers typed/voice follow-ups with a single
concise block. When the user asks for a code change ("add empty-input handling",
"do it with a stack instead"), it dumps the whole modified solution at once.

## Goal
Follow-ups that request a code change must be answered in the same additive,
live-coding tutor style as capture solutions: granular steps, reasoning-first
(why → what), cumulative code per step, then the final clean code.

## Files
- `server/telegram.py` — `FOLLOWUP_PROMPT`, `answer_followup()`, new `_format_stepped_followup()`.

## Design
- New prompt: model detects intent.
  - Code-change request → STRICT textual format with machine-parseable markers:
    ```
    <<<MODE:STEPS>>>
    <<<STEP 1>>>
    <why, then what>
    <<<CODE>>>
    ```<entire cumulative code so far>```
    ... final:
    <<<FINAL_CODE>>>
    ```<clean full version>```
  - Pure explanation → `<<<MODE:TEXT>>>` + plain answer (current behavior).
- Parse in `answer_followup()`; render steps via HTML like `format_solution`
  (numbered steps + `<pre>` cumulative code + final code).
- Fallback: if the model ignores markers, return raw text as today (never crash).

## Acceptance criteria
- [ ] Typed follow-up asking for a code change returns step-by-step additive code
- [ ] Same for a voice follow-up (same code path after transcription)
- [ ] Explanatory follow-ups remain concise one-block answers
- [ ] Malformed model output doesn't crash — falls back to raw text
- [ ] Verified live end-to-end
