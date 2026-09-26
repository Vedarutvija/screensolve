"""Telegram delivery: /start link-up + solution push."""

import base64
import html
import os
import re
import threading
import time
from datetime import datetime

import httpx
from sqlalchemy import Column, DateTime, Integer, String

from server import chat_history
from server.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    STT_MODEL,
    TELEGRAM_BOT_TOKEN,
    VISION_MODEL,
)
from server.database import Base

API = "https://api.telegram.org/bot"


class TelegramChat(Base):
    __tablename__ = "telegram_chats"

    id = Column(Integer, primary_key=True)
    chat_id = Column(String(64), unique=True, nullable=False)
    title = Column(String(256), nullable=True)
    linked_at = Column(DateTime, nullable=True)


def enabled() -> bool:
    return bool(TELEGRAM_BOT_TOKEN)


def _call(method: str, **payload):
    import logging

    if not enabled():
        return None
    try:
        r = httpx.post(f"{API}{TELEGRAM_BOT_TOKEN}/{method}", json=payload, timeout=30)
        if r.status_code == 200:
            return r.json()
        logging.getLogger("screensolve.telegram").warning(
            "Telegram %s failed: HTTP %s %s", method, r.status_code, r.text[:300]
        )
        return None
    except Exception:
        logging.getLogger("screensolve.telegram").exception(
            "Telegram %s request error", method
        )
        return None


def send_message(chat_id: str, text: str) -> bool:
    ok = _call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML",
               disable_web_page_preview=True)
    return bool(ok and ok.get("ok"))


def send_photo(chat_id: str, png: bytes, caption: str | None = None) -> bool:
    if not enabled():
        return False
    try:
        files = {"photo": ("capture.png", png, "image/png")}
        data = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "HTML"
        r = httpx.post(f"{API}{TELEGRAM_BOT_TOKEN}/sendPhoto",
                       data=data, files=files, timeout=60)
        return r.status_code == 200 and r.json().get("ok")
    except Exception:
        return False


def link_chat(db, chat_id, title=None) -> None:
    from datetime import datetime, timezone

    if db.query(TelegramChat).filter_by(chat_id=str(chat_id)).first():
        return
    db.add(TelegramChat(chat_id=str(chat_id), title=title,
                        linked_at=datetime.now(timezone.utc)))
    db.commit()


def broadcast(db, text: str) -> int:
    sent = 0
    for chat in db.query(TelegramChat).all():
        if send_message(chat.chat_id, text):
            sent += 1
    return sent


def broadcast_photo(db, png: bytes, caption: str | None = None) -> int:
    sent = 0
    for chat in db.query(TelegramChat).all():
        if send_photo(chat.chat_id, png, caption):
            sent += 1
    return sent


def download_file(file_id: str) -> bytes | None:
    if not enabled():
        return None
    try:
        r = httpx.get(f"{API}{TELEGRAM_BOT_TOKEN}/getFile",
                      params={"file_id": file_id}, timeout=30)
        path = r.json().get("result", {}).get("file_path")
        if not path:
            return None
        f = httpx.get(f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{path}",
                      timeout=120)
        return f.content or None
    except Exception:
        return None


def transcribe(ogg_bytes: bytes) -> str:
    """Speech-to-text: local faster-whisper first, gateway fallback."""
    import tempfile

    import subprocess

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(ogg_bytes)
        ogg_path = f.name
    wav_path = ogg_path.replace(".ogg", ".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", ogg_path, "-ar", "16000", "-ac", "1", wav_path],
            capture_output=True, timeout=60, check=True,
        )
    except Exception:
        wav_path = ogg_path  # backend may support ogg directly

    try:
        return _transcribe_local(wav_path)
    except Exception:
        if wav_path != ogg_path:
            return _transcribe_gateway(ogg_path, "audio/ogg")
        raise


_local_model = None
_local_lock = threading.Lock()


def _get_local_model():
    global _local_model
    with _local_lock:
        if _local_model is None:
            from faster_whisper import WhisperModel

            _local_model = WhisperModel("tiny", device="cpu", compute_type="int8")
        return _local_model


def _transcribe_local(wav_path: str) -> str:
    model = _get_local_model()
    segments, _info = model.transcribe(wav_path)
    return " ".join(s.text.strip() for s in segments).strip()


def _transcribe_gateway(audio: bytes, mime: str) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("No speech-to-text backend configured")
    r = httpx.post(
        f"{OPENAI_BASE_URL.rstrip('/')}/audio/transcriptions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        files={"file": ("voice.ogg", audio, mime)},
        data={"model": STT_MODEL},
        timeout=180,
    )
    if r.status_code != 200:
        raise RuntimeError(f"transcription failed: HTTP {r.status_code}")
    return (r.json().get("text") or "").strip()


FOLLOWUP_PROMPT = """You are ScreenSolve, a programming tutor chatting on Telegram.

Below is the recent conversation with this user, including solutions you delivered
for screen captures. The user asks a follow-up question (typed or spoken).

FIRST decide the intent:
A) The user asks to SOLVE a problem, or for a CODE CHANGE or new implementation
   ("solve this", "add X", "change it to Y", "make it handle Z", "rewrite using W",
   "write code for..."). ALWAYS pick A when code should be produced or changed —
   even if the request is short or vague.
B) Purely conceptual/explanatory questions with no code to produce.

For case A, answer in the ADDITIVE live-coding tutor style — steps are REQUIRED:
- Split the work into granular steps; each step introduces exactly ONE concept.
- Every step's explanation starts with WHY we do this before WHAT we add.
- **Bold** the key terms in every explanation: function names, data structures,
  algorithms, and any O(...) complexity (e.g. **hash map**, **O(n)**, **recursion**).
- Each step's code block contains the ENTIRE code accumulated so far (previous
  steps + this step's addition, new part marked with a short comment).
- The final code block is the complete, clean, runnable version (no narration comments).

For case B, answer concisely but still format nicely: use **bold** for key terms
and ``` code fences for any code snippet.

Markdown formatting (**bold**, *italic*, `inline code`, ``` fences) is supported
everywhere in your reply and will be rendered — use it generously.

Output format (STRICT — these markers are machine-parsed):
- Always start with exactly one line: <<<MODE:STEPS>>> or <<<MODE:TEXT>>>
- For <<<MODE:STEPS>>>, after the marker repeat for each step:
<<<STEP>>>
<why first, then what this step adds>
<<<CODE>>>
```python
<entire cumulative code so far>
```
- After the last step:
<<<FINAL_CODE>>>
```python
<complete clean solution>
```
- For <<<MODE:TEXT>>>, write the plain answer right after the marker (Telegram HTML allowed: <b>, <code>, <pre>).

Recent conversation:
{history}

User's new question: {question}

Your answer:"""


IMAGES_CONTEXT_SUFFIX = """

Attached are screenshot(s) of content the user previously captured from their
screen (dataset previews, question parts, their own code, or a COMPLETE
solution shown on screen). Treat them as GROUND-TRUTH CONTEXT:
- Read code, problems, and datasets directly from the images — even if no
  solution was ever delivered in the conversation below, the image IS the
  current state of the user's code.
- When the user's question says "do X" / "change it" / "refactor" with no
  explicit target, "it" means the code visible in these images.
- Base your answer on the image content (variables, function names, approach),
  not on assumptions.
- If the request involves SOLVE/MODIFY/WRITE code, you MUST use the additive
  stepped style (mode STEPS)."""


_MD_CODE_RE = re.compile(r"```[a-zA-Z0-9_+-]*[ \t]*\n(.*?)```", re.DOTALL)


def markdown_to_telegram_html(text: str) -> str:
    """Convert a model's Markdown reply to Telegram-safe HTML.

    Order matters: extract fenced code first (its content must NOT be
    markdown-converted), then inline code, then bold/italic, then escape
    what's left. All text/code content is html.escape()d before tags wrap it,
    so raw < > & in answers can't break Telegram's HTML parser.
    """
    e = html.escape

    # 1. fenced code blocks -> <pre> (content escaped ONCE, left untouched otherwise)
    placeholders: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code = m.group(1).rstrip()
        # raw markdown: & < > are all literal characters — escape fully
        code = e(code)
        placeholders.append(f"<pre>{code}</pre>")
        return f"\x00CODE{len(placeholders) - 1}\x00"

    out = _MD_CODE_RE.sub(_stash_code, text)

    # 2. inline code `x` -> <code> (content escaped, backticks removed)
    def _stash_inline(m: re.Match) -> str:
        placeholders.append("<code>" + e(m.group(1)) + "</code>")
        return f"\x00INLINE{len(placeholders) - 1}\x00"

    out = re.sub(r"`([^`\n]+)`", _stash_inline, out)

    # 3. bold / italic on remaining text
    out = re.sub(r"\*\*\*(.+?)\*\*\*", r"<b><i>\1</i></b>", out, flags=re.DOTALL)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out, flags=re.DOTALL)
    out = re.sub(r"(?<![\w*])\*([^*\n]+?)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(r"(?<![\w*_])__(.+?)__(?![\w_])", r"<b>\1</b>", out, flags=re.DOTALL)
    out = re.sub(r"(?<![\w_])_([^_\n]+?)_(?![\w_])", r"<i>\1</i>", out)

    # 4. markdown headings / bullets -> friendly plain-ish formatting
    out = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", out)
    out = re.sub(r"(?m)^\s*[-*]\s+", "• ", out)
    out = re.sub(r"(?m)^\s*\d+\.\s+", lambda m: m.group(0), out)

    # 5. escape stray angle brackets in prose. Everything in `out` now is a mix
    # of (a) our own whitelisted tags, (b) stashed placeholders, (c) raw user
    # text that may contain < >. Protect (a) and (b), escape the rest.
    PROTECT = re.compile(
        r"</?(?:b|i|u|s|code|pre)>|\x00(?:CODE|INLINE)\d+\x00"
    )
    pieces = PROTECT.split(out)
    keepers = PROTECT.findall(out)
    rebuilt: list[str] = []
    for idx, chunk in enumerate(pieces):
        rebuilt.append(e(chunk))
        if idx < len(keepers):
            rebuilt.append(keepers[idx])
    out = "".join(rebuilt)

    # 6. restore stashed code blocks (already fully escaped + wrapped)
    out = re.sub(
        r"\x00CODE(\d+)\x00", lambda m: placeholders[int(m.group(1))], out
    )
    out = re.sub(
        r"\x00INLINE(\d+)\x00", lambda m: placeholders[int(m.group(1))], out
    )
    return out.strip()


def _format_stepped_followup(steps: list[dict], final_code: str | None) -> str:
    e = html.escape
    parts = ["<b>🛠 Updated solution — building it up:</b>"]
    for i, s in enumerate(steps, 1):
        text = markdown_to_telegram_html(s.get("step") or "")
        code = s.get("code")
        if code:
            parts.append(f"<b>🔹 Step {i}</b>\n{text}\n<pre>{e(code)}</pre>")
        else:
            parts.append(f"<b>🔹 Step {i}</b>\n{text}")
    if final_code:
        parts.append("\n<b>✅ Final code:</b>\n<pre>" + e(final_code) + "</pre>")
    return "\n\n".join(parts)


def _parse_stepped_answer(text: str) -> tuple[str, str]:
    """Returns (mode, rendered). mode is 'steps' or 'text'. Falls back to
    ('text', raw) when markers are missing or malformed.

    Tolerates marker noise: model may wrap markers in bold/italics, add
    spaces, or drop some angle brackets (e.g. '**MODE:STEPS**>', 'MODE: STEPS')."""
    # normalize noisy marker variants to canonical form. IMPORTANT: require
    # brackets (<<..>>) or bold (**) AROUND the keyword — bare words like
    # "this step adds" in prose must NOT become phantom markers.
    _NOISE = {
        "mode": r"(?:<{2,3}|\*\*)\s*MODE\s*:?\s*(_?STEPS?|_?TEXT)\s*(?:\*\*|>{2,3})",
        "step": r"(?:<{2,3}|\*\*)\s*STEP\b(?!S\b)\s*(?:\*\*|>{2,3})",
        "code": r"(?:<{2,3}|\*\*)\s*CODE\s*(?:\*\*|>{2,3})",
        "final": r"(?:<{2,3}|\*\*)\s*FINAL[_\s-]*CODE\s*(?:\*\*|>{2,3})",
    }

    def _canon(m: re.Match) -> str:
        word = (m.group(1) or "").upper().replace(" ", "").lstrip("_")
        return f"<<<MODE:{word}>>>"

    text = re.sub(_NOISE["mode"], _canon, text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["step"], "<<<STEP>>>", text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["code"], "<<<CODE>>>", text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["final"], "<<<FINAL_CODE>>>", text, flags=re.IGNORECASE)

    marker_re = re.compile(r"<<<MODE:(STEPS|TEXT)>>>")
    m = marker_re.search(text)
    if not m:
        return "text", text.strip()
    mode = m.group(1).lower()
    body = text[m.end():].strip()
    if mode == "text":
        return "text", body

    steps: list[dict] = []
    final_code = None
    chunks = body.split("<<<STEP>>>")[1:]
    for chunk in chunks:
        if "<<<CODE>>>" not in chunk:
            return "text", text.replace("<<<", "").strip()  # malformed — fallback
        explanation, rest = chunk.split("<<<CODE>>>", 1)
        code = ""
        fm = re.search(r"```(?:python)?\s*\n(.*?)```", rest, re.DOTALL)
        if fm:
            code = fm.group(1).rstrip()
        if "<<<FINAL_CODE>>>" in rest:
            frest = rest.split("<<<FINAL_CODE>>>", 1)[1]
            ffm = re.search(r"```(?:python)?\s*\n(.*?)```", frest, re.DOTALL)
            if ffm:
                final_code = ffm.group(1).rstrip()
        steps.append({"step": explanation.strip(), "code": code})
    if not steps:
        return "text", text.replace("<<<", "").strip()
    if final_code is None:
        final_code = steps[-1].get("code")
    return "steps", _format_stepped_followup(steps, final_code)


def answer_followup(question: str, image_parts: list[bytes] | None = None) -> str:
    history = chat_history.recent(question_chat_id_holder.get("chat_id", ""), limit=14)
    hist_text = "\n".join(
        f"[{m['role']}] {m['content'][:800]}" for m in history
    ) or "(none — no captures delivered to this chat yet)"
    full = FOLLOWUP_PROMPT.format(history=hist_text, question=question)
    if image_parts:
        full += IMAGES_CONTEXT_SUFFIX

    if GEMINI_API_KEY:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        contents: list = [
            types.Part.from_bytes(data=p, mime_type="image/png") for p in (image_parts or [])
        ]
        contents.append(full)
        resp = client.models.generate_content(
            model=GEMINI_MODEL, contents=contents,
            config=types.GenerateContentConfig(temperature=0.3, max_output_tokens=4096),
        )
        raw = (resp.text or "").strip()
    elif OPENAI_API_KEY:
        content: list = [{"type": "text", "text": full}]
        for p in image_parts or []:
            b64 = base64.b64encode(p).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        r = httpx.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": VISION_MODEL,
                "temperature": 0.3,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=300,
        )
        r.raise_for_status()
        raw = (r.json()["choices"][0]["message"]["content"] or "").strip()
    else:
        raise RuntimeError("No LLM configured for follow-up answers")

    mode, rendered = _parse_stepped_answer(raw)
    if mode == "text":
        rendered = markdown_to_telegram_html(rendered)
    return rendered


def recent_session_images(db) -> list[bytes]:
    """Images from the most recently active capture session (any status with
    captured parts) — used to give voice/text follow-ups visual context."""
    from server.models import Capture, CaptureSession

    sess_ids = [
        s.id for s in db.query(CaptureSession)
        .order_by(CaptureSession.id.desc()).limit(10).all()
    ]
    for sid in sess_ids:
        imgs = []
        rows = (
            db.query(Capture)
            .filter_by(session_id=sid, status="captured")
            .order_by(Capture.id.asc()).all()
        )
        for r in rows:
            if os.path.exists(r.image_path):
                try:
                    with open(r.image_path, "rb") as f:
                        imgs.append(f.read())
                except OSError:
                    pass
        if imgs:
            return imgs
    return []


# set per-chat before calling answer_followup
question_chat_id_holder: dict = {}


def _handle_session_command(cmd: str, chat_id: str) -> None:
    from server.database import SessionLocal
    from server.models import Capture, CaptureSession
    from server.routers import agent as agent_api
    from server.gemini import analyze_images_multi

    db = SessionLocal()
    try:
        if cmd == "c":
            agent_api.issue_capture_command()
            # wait for the agent to upload a NEW part (id must increase past
            # whatever existed when the command was issued)
            import time as _time

            from server.models import Capture as _Cap

            last_id_before = db.query(_Cap.id).order_by(_Cap.id.desc()).first()
            last_id_before = last_id_before[0] if last_id_before else 0

            waited = 0.0
            new_part = None
            while waited < 18:
                _time.sleep(2)
                waited += 2
                db.expire_all()
                new_cap = db.query(_Cap).filter(_Cap.id > last_id_before).first()
                if new_cap:
                    new_part = new_cap
                    # small grace so the echo photo lands too
                    _time.sleep(1)
                    break
            if new_part:
                parts = (
                    db.query(Capture).filter_by(status="captured").count()
                )
                send_message(
                    chat_id,
                    f"📸 Captured — the screenshot is above (part {parts}).\n"
                    "Scroll and send c for more parts, or s to solve.",
                )
            else:
                send_message(
                    chat_id,
                    "⚠️ No capture arrived — is the agent running on your computer?\n"
                    "Start it there with: python3 agent/main.py",
                )

        elif cmd == "s":
            sess = db.query(CaptureSession).filter_by(status="open").first()
            if not sess:
                # maybe the session was already solved/cleared — check for any
                # recently captured parts and tell the truth
                last = db.query(Capture).filter_by(status="captured").order_by(Capture.id.desc()).first()
                if last and last.session_id:
                    send_message(
                        chat_id,
                        f"That session was already solved. Send c to start a new capture"
                        f" (your last solution is above — capture #{last.id}).",
                    )
                else:
                    send_message(chat_id, "No capture session open. Send c first to capture your screen.")
                return
            parts = db.query(Capture).filter_by(session_id=sess.id, status="captured").count()
            if not parts:
                send_message(chat_id, "Session is empty — send c to capture the screen first.")
                return
            send_message(chat_id, f"🧠 Solving with {parts} part(s)…")
            sess.status = "solving"
            db.commit()
            try:
                images = agent_api.session_images(db, sess.id)
                if not images:
                    raise RuntimeError("captured images could not be read from disk")
                # any voice/text instructions sent while capturing (before s)
                # must shape the solve — pull them from chat history
                pending = [
                    m["content"] for m in chat_history.recent(chat_id, limit=10)
                    if m["role"] == "user"
                    and "🎙️" not in m["content"]
                    and m["content"].strip().lower() not in ("c", "s", "status")
                ]
                extra = " ".join(pending[-3:]).strip() or None
                result = analyze_images_multi(images, extra_instruction=extra)
            except Exception as e:  # noqa: BLE001
                sess.status = "open"  # allow retry
                db.commit()
                send_message(chat_id, f"⚠️ Analysis failed: {str(e)[:180]}")
                return

            # store as one capture row (primary image = first part)
            first = (
                db.query(Capture)
                .filter_by(session_id=sess.id, status="captured")
                .order_by(Capture.id.asc())
                .first()
            )
            now = datetime.utcnow()
            cap = Capture(
                image_path=first.image_path,
                session_id=sess.id,
                created_at=now,
                analyzed_at=now,
            )
            if not result.get("has_question"):
                cap.status = "no_question"
                cap.notes = "No coding question detected across the captured parts."
                db.add(cap)
                sess.status = "solved"
                sess.solved_at = now
                db.commit()
                send_message(chat_id, "🤔 No coding question found in the captured parts.")
                return
            cap.status = "solved"
            cap.problem_statement = result.get("problem_statement")
            cap.user_attempt = result.get("user_attempt")
            cap.solution_steps = result.get("solution_steps")
            cap.optimized_code = result.get("optimized_code")
            cap.time_complexity = result.get("time_complexity")
            cap.space_complexity = result.get("space_complexity")
            cap.notes = result.get("notes")
            db.add(cap)
            sess.status = "solved"
            sess.solved_at = now
            db.commit()
            db.refresh(cap)

            # deliver: each part screenshot, then the solution
            for i, img in enumerate(images, 1):
                send_photo(chat_id, img, caption=f"part {i}/{len(images)}")
            send_message(chat_id, format_solution(cap))
            _hist_summary = (
                f"Solution for capture #{cap.id} ({len(images)} parts).\n"
                f"Problem: {(cap.problem_statement or '')[:500]}\n"
                f"Final code:\n{(cap.optimized_code or '')[:1500]}"
            )
            chat_history.add(chat_id, "assistant", _hist_summary)

        elif cmd == "status":
            sess = db.query(CaptureSession).filter_by(status="open").first()
            if not sess:
                send_message(chat_id, "No open session. Send c to start capturing.")
            else:
                parts = db.query(Capture).filter_by(session_id=sess.id, status="captured").count()
                send_message(chat_id, f"Open session: {parts} part(s) captured. c = add part, s = solve.")
    finally:
        db.close()


def format_solution(capture) -> str:
    parts = ["<b>📸 ScreenSolve — new solution</b>"]
    if capture.problem_statement:
        parts.append("\n<b>📋 Problem:</b>\n" + markdown_to_telegram_html(capture.problem_statement))
    if capture.solution_steps:
        items = []
        for i, s in enumerate(capture.solution_steps):
            if isinstance(s, dict):
                text = markdown_to_telegram_html(s.get("step") or "")
                code = s.get("code")
                if code:
                    items.append(f"<b>🔹 Step {i+1}</b>\n{text}\n<pre>{html.escape(code)}</pre>")
                else:
                    items.append(f"<b>🔹 Step {i+1}</b>\n{text}")
            else:
                items.append(f"<b>🔹 Step {i+1}</b>\n" + markdown_to_telegram_html(str(s)))
        parts.append("\n<b>🧩 Approach (building up):</b>\n" + "\n\n".join(items))
    if capture.optimized_code:
        parts.append("\n<b>✅ Optimized code:</b>\n<pre>" + html.escape(capture.optimized_code) + "</pre>")
    badges = [b for b in (capture.time_complexity, capture.space_complexity) if b]
    if badges:
        parts.append("\n<b>⚡ Complexity:</b> " + " · ".join(markdown_to_telegram_html(b) for b in badges))
    if capture.notes:
        parts.append("\n<b>💡 Feedback:</b>\n" + markdown_to_telegram_html(capture.notes))
    return "\n".join(parts)


def _poll_loop() -> None:
    from server.database import SessionLocal

    import logging

    tg_log = logging.getLogger("screensolve.telegram")

    offset = 0
    while True:
        if not enabled():
            time.sleep(30)
            continue
        try:
            r = httpx.get(
                f"{API}{TELEGRAM_BOT_TOKEN}/getUpdates",
                params={"timeout": 25, "offset": offset},
                timeout=35,
            )
            data = r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or {}
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                if not chat_id:
                    continue
                try:
                    _handle_update(msg, str(chat_id))
                except Exception:
                    tg_log.exception("failed handling Telegram update from chat %s", chat_id)
                    send_message(
                        str(chat_id),
                        "⚠️ Something went wrong handling that — please try again.",
                    )
        except Exception:
            tg_log.exception("telegram getUpdates poll error")
            time.sleep(10)


def _handle_update(msg: dict, chat_id: str) -> None:
    from server.database import SessionLocal

    db = SessionLocal()
    try:
        link_chat(db, chat_id, (msg.get("chat") or {}).get("title"))
    finally:
        db.close()

    voice = msg.get("voice") or msg.get("audio")
    text = (msg.get("text") or "").strip()

    if text and text.strip().lower() in ("c", "s", "status"):
        _handle_session_command(text.strip().lower(), chat_id)
        return

    if voice:
        file_id = voice.get("file_id")
        raw = download_file(file_id)
        if not raw:
            send_message(chat_id, "⚠️ Couldn't download the voice message — try again.")
            return
        try:
            question = transcribe(raw)
        except Exception as e:  # noqa: BLE001
            send_message(chat_id, f"⚠️ Couldn't transcribe the voice message ({str(e)[:120]}). You can type the question instead.")
            return
        if not question:
            send_message(chat_id, "🤔 The voice message came through silent — say it again?")
            return
        send_message(chat_id, f"🎙️ Heard: “{question}”")
    elif text and text.startswith("/"):
        return  # commands handled elsewhere
    elif text:
        question = text
    else:
        return

    # follow-up question path — give the model the most recent
    # captured session images (dataset/question parts) as context
    question_chat_id_holder["chat_id"] = chat_id
    chat_history.add(chat_id, "user", question)
    try:
        answer = answer_followup(question, _recent_images_safe())
    except Exception as e:  # noqa: BLE001
        # retry once text-only (e.g. context images too large)
        try:
            answer = answer_followup(question)
        except Exception as e2:  # noqa: BLE001
            answer = f"⚠️ Couldn't answer that right now: {str(e2)[:150]}"
    chat_history.add(chat_id, "assistant", answer)
    send_message(chat_id, answer)


def _recent_images_safe() -> list[bytes]:
    from server.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            return recent_session_images(db)
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        return []


def start_bot() -> None:
    if not enabled():
        return
    _reset_stuck_sessions()
    threading.Thread(target=_poll_loop, daemon=True).start()


def _reset_stuck_sessions() -> None:
    """Sessions left in 'solving' by a restart would block future solves — reopen them."""
    from server.database import SessionLocal
    from server.models import CaptureSession

    try:
        db = SessionLocal()
        try:
            stuck = (
                db.query(CaptureSession)
                .filter_by(status="solving")
                .all()
            )
            if stuck:
                for s in stuck:
                    s.status = "open"
                db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        import logging

        logging.getLogger("screensolve.telegram").exception(
            "could not reset stuck sessions at startup"
        )
