# Phase 22 · Fix silent 's' failure (no reply after solve command)

## Symptom
Agent runs silently (pythonw, Task Scheduler started via PowerShell), captures arrive
in Telegram (echo photos work), but sending `s` produces NO reply — no solution,
no error, nothing.

## Investigation findings (researched)
- DB state is healthy: session 9 is `open` with a `captured` part whose image exists.
  `pool_pre_ping`/`pool_recycle` already set in `server/database.py` — stale DB
  connection is NOT the cause.
- **Root cause:** in `server/telegram.py` `_poll_loop`:
  1. `offset = upd["update_id"] + 1` is consumed BEFORE the handler runs, and the
     whole per-update handling sits under a bare `except Exception: time.sleep(10)`
     with **no logging**. Any exception in `_handle_session_command('s')` before its
     first `send_message` → update consumed, exception swallowed → total silence.
  2. `_handle_session_command` itself has internal try/excepts around delivery, but
     the dispatch call at the `_poll_loop` site has none.
  3. `_call()` returns None on any Telegram non-200 (e.g. HTTP 400 from bad HTML in
     `parse_mode="HTML"`) → `send_message` returns False silently, no retry/log.
  4. Service log shows zero exceptions — consistent with the swallow.
  5. Stale session 6 stuck in `status="solving"` from a restart (orphaned, harmless
     for new sessions but should be cleaned).

## Files to change
- `server/telegram.py`

## Changes
1. `_poll_loop`: add `import logging; log = logging.getLogger("screensolve.telegram")`.
   Replace the trailing bare swallow with:
   ```python
   except Exception:
       log.exception("poll loop error")
       time.sleep(10)
   ```
2. Wrap the per-update body (after `chat_id` extraction) in try/except:
   on exception, `log.exception(...)`, reset `offset` is NOT possible (already
   consumed), but send `"⚠️ Something went wrong handling that — please try again."`
   to the chat so the user is never left in silence.
3. `_handle_session_command('s')`: move `images = agent_api.session_images(db, sess.id)`
   inside the existing try block that catches analysis errors, so a file-read failure
   returns `⚠️ ...` to the chat instead of propagating.
4. `send_message`: on failure (`ok` falsy or `ok` dict with `ok: False`), log the
   response body at warning level so Telegram-side rejections (bad HTML etc.)
   become visible.
5. Startup cleanup: in `start_bot()` (before spawning the thread), open a session and
   update `CaptureSession` rows with `status="solving"` back to `"open"`.

## Acceptance criteria (user-visible)
- [ ] Sending `s` ALWAYS produces a reply: either the solution, or a visible ⚠️ error message — never silence
- [ ] The actual failure reason appears in `/var/log/services/service-bg-service-4335.log`
- [ ] After a server restart mid-session, `s` still works (orphaned solving sessions cleaned)
- [ ] Live-verified: c → s → solution delivered end-to-end

## Tests / edge cases
- Simulate handler exception (monkeypatch send/db) → assert warning message sent & logged
- Simulate Telegram 400 on sendMessage → assert logged, no crash
- Stale `solving` session at startup → reset to `open`
