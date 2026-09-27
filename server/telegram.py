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


TG_MSG_LIMIT = 4096


def split_message(text: str, limit: int = TG_MSG_LIMIT) -> list[str]:
    """Split a Telegram HTML message into chunks of <= limit chars.

    Never splits inside a <pre>…</pre> block; prefers paragraph boundaries,
    then newlines, then a hard split as last resort. Re-opens/re-closes any
    <pre> block that a chunk boundary cut so every chunk is valid HTML on its
    own."""
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []

    # split points: after blank-line paragraphs, never inside <pre>
    paragraphs: list[str] = []
    buf: list[str] = []
    in_pre = False
    i = 0
    while i < len(text):
        if text.startswith("<pre>", i):
            in_pre = True
        elif text.startswith("</pre>", i):
            in_pre = False
            i += len("</pre>")
            buf.append("</pre>")
            paragraphs.append("".join(buf))
            buf = []
            continue
        buf.append(text[i])
        if not in_pre and text[i : i + 2] == "\n\n":
            paragraphs.append("".join(buf))
            buf = []
        i += 1
    if buf:
        paragraphs.append("".join(buf))

    # drop split markers left inside a paragraph (a <pre> opening mid-paragraph)
    chunks: list[str] = []
    cur = ""
    for p in paragraphs:
        p = p.strip("\n")
        if not p:
            continue
        candidate = f"{cur}\n\n{p}" if cur else p
        if len(candidate) <= limit:
            cur = candidate
            continue
        if cur:
            chunks.append(cur)
        if len(p) <= limit:
            cur = p
            continue
        # single paragraph too long → hard-split on newlines then chars,
        # keeping <pre> balanced per piece
        pieces = _hard_split_balanced(p, limit)
        chunks.extend(pieces[:-1])
        cur = pieces[-1]
    if cur:
        chunks.append(cur)
    return chunks


def _hard_split_balanced(text: str, limit: int) -> list[str]:
    """Hard-split text into <=limit pieces. If the text STARTS inside an open
    <pre> (opened in an earlier chunk), every piece re-opens it and only the
    last piece closes it — so concatenated pieces reproduce the original, and
    each piece alone renders fine as an open pre (Telegram tolerates this at
    message end; the NEXT chunk re-opens the block explicitly)."""
    pieces: list[str] = []
    cur = ""
    # treat as "open pre" when the text starts inside one: either it begins
    # with <pre> (a block we're about to split mid-way) or it has an unbalanced tag
    open_pre = (
        text.lstrip().startswith("<pre>")
        or text.count("<pre>") > text.count("</pre>")
    )
    prefix, suffix = ("<pre>", "</pre>") if open_pre else ("", "")
    for line in text.split("\n"):
        candidate = f"{cur}\n{line}" if cur else line
        if len(prefix + candidate + suffix) <= limit:
            cur = candidate
            continue
        if cur:
            pieces.append(prefix + cur)
        cur = line
        while len(prefix + cur) > limit - len(suffix):
            cut = limit - len(prefix) - len(suffix)
            pieces.append(prefix + cur[:cut])
            cur = cur[cut:]
    if cur:
        pieces.append(prefix + cur + suffix)
    return pieces


def send_message(chat_id: str, text: str) -> bool:
    ok_all = True
    chunks = split_message(text)
    for n, chunk in enumerate(chunks, 1):
        body = chunk if len(chunks) == 1 else f"{chunk}\n\n<i>— {n}/{len(chunks)} —</i>"
        ok = _call("sendMessage", chat_id=chat_id, text=body, parse_mode="HTML",
                   disable_web_page_preview=True)
        ok_all = ok_all and bool(ok and ok.get("ok"))
    return ok_all


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


def send_photo_url(chat_id: str, url: str, caption: str | None = None) -> bool:
    """Send an image by URL (for real-world reference images in answers)."""
    if not enabled():
        return False
    try:
        data = {"chat_id": str(chat_id), "photo": url}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "HTML"
        r = httpx.post(f"{API}{TELEGRAM_BOT_TOKEN}/sendPhoto",
                       data=data, timeout=60)
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
C) TOOL / SETUP / WORKFLOW guidance — the user asks HOW to use, test, run, or set
   up something ("how do I test this in Postman?", "how do I call this API?",
   "how do I deploy this?", "how do I install/run it?").

For case A, answer in the ADDITIVE live-coding tutor style — steps are REQUIRED:
- Split the work into granular steps; each step introduces exactly ONE concept.
- Every step's explanation starts with WHY we do this before WHAT we add.
- **Bold** the key terms in every explanation: function names, data structures,
  algorithms, and any O(...) complexity (e.g. **hash map**, **O(n)**, **recursion**).
- Each step's code block contains the ENTIRE code accumulated so far (previous
  steps + this step's addition, new part marked with a short comment).
- The final code block is the complete, clean, runnable version (no narration comments).

For case C, give CLICK-BY-CLICK tool steps the user can follow exactly:
- Number each ACTION: what to open, what to click, what to select, what to type.
- Under each action, give the EXACT payload for that step (URL, headers, JSON
  body, command) in a code fence — grounded in the captured problem/code when
  available (use its actual endpoint paths, parameter names, and values).
- Cover the complete flow start to finish, including how to verify the result.

For case B, answer concisely but still format nicely: use **bold** for key terms
and ``` code fences for any code snippet.

Markdown formatting (**bold**, *italic*, `inline code`, ``` fences) is supported
everywhere in your reply and will be rendered — use it generously.

Output format (STRICT — these markers are machine-parsed):
- Always start with exactly one line: <<<MODE:STEPS>>>, <<<MODE:TEXT>>>, or <<<MODE:TOOL>>>
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
- For <<<MODE:TOOL>>>, after the marker repeat for each action:
<<<STEP>>>
<the action: open/click/select/type...>
<<<DETAIL>>>
```text
<exact payload/URL/command for this step — omit this fence entirely if none>
```
- In MODE:TOOL answers you MUST add an IMAGE line inside EXACTLY TWO steps (the two most visual ones — a screen, dashboard, dialog or button the user will see). Put it on its own line inside the step block, AFTER the detail fence:
IMAGE: <2-5 word image search query for that UI, e.g. "postman new request screen" or "pinecone console dashboard">
(NEVER skip this — every MODE:TOOL answer must contain exactly 2 IMAGE lines.)
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
    parts = ["<b>🛠 Updated solution — building it up</b>"]
    for i, s in enumerate(steps, 1):
        text = markdown_to_telegram_html(s.get("step") or "")
        code = s.get("code")
        if code:
            parts.append(f"<b>🔹 Step {i}</b>\n{text}\n<pre><code class=\"language-python\">{e(code)}</code></pre>")
        else:
            parts.append(f"<b>🔹 Step {i}</b>\n{text}")
    if final_code:
        parts.append("\n<b>✅ Final code</b>\n<pre><code class=\"language-python\">" + e(final_code) + "</code></pre>")
    return "\n\n".join(parts)


_img_cache: dict = {}  # query -> (timestamp, url)
_IMG_TTL = 86400


def search_image(query: str) -> str | None:
    """Find one relevant real-world image URL for a UI/tool query via
    DuckDuckGo. Cached; returns None on any failure — callers must treat the
    image as optional."""
    import time as _time

    query = (query or "").strip()[:80]
    if not query:
        return None
    hit = _img_cache.get(query)
    if hit and _time.time() - hit[0] < _IMG_TTL:
        return hit[1]
    url = None
    try:
        from ddgs import DDGS

        with DDGS() as d:
            for r in d.images(query, max_results=5):
                cand = (r.get("image") or "").strip()
                if cand.startswith("http") and not cand.lower().endswith((".svg", ".gif")):
                    url = cand
                    break
    except Exception:  # noqa: BLE001 — image is strictly optional
        return None
    if url:
        _img_cache[query] = (_time.time(), url)
    return url


def _parse_tool_answer(body: str) -> str:
    """Render MODE:TOOL steps: each STEP is an action, optional DETAIL holds the
    exact payload for that step, optional IMAGE resolves to a real image link.
    Falls back to cleaned raw text if malformed."""
    e = html.escape
    chunks = body.split("<<<STEP>>>")[1:]
    if not chunks:
        return body.replace("<<<", "").strip()
    parts = ["<b>🧪 How to do it — step by step:</b>"]
    img_refs: list[str] = []  # (step_number, url) collected, rendered per step
    for i, chunk in enumerate(chunks, 1):
        img_query = None
        # IMAGE: line — model-suggested image search query; may appear anywhere
        # in the step block (action, inside detail, or after the detail fence)
        m = re.search(r"^\s*IMAGE:\s*(.+)$", chunk, re.MULTILINE)
        if m:
            img_query = m.group(1).strip()
            chunk = re.sub(r"^\s*IMAGE:.*$\n?", "", chunk, flags=re.MULTILINE)
        if "<<<DETAIL>>>" in chunk:
            action, detail = chunk.split("<<<DETAIL>>>", 1)
            action = action.strip()
            detail = detail.strip()
        else:
            action, detail = chunk.strip(), ""
        # IMAGE: may appear in the action OR the detail block — strip both
        if "IMAGE:" in detail:
            detail = re.sub(r"^\s*IMAGE:.*$\n?", "", detail, flags=re.MULTILINE).strip()
        action_html = markdown_to_telegram_html(action)
        if detail:
            fm = re.search(r"```[a-zA-Z0-9_+-]*[ \t]*\n(.*?)```", detail, re.DOTALL)
            payload = (fm.group(1) if fm else detail).rstrip()
            parts.append(
                f"<b>🔹 Step {i}</b>\n{action_html}\n<pre>{e(payload)}</pre>"
            )
        else:
            parts.append(f"<b>🔹 Step {i}</b>\n{action_html}")
        if img_query:
            url = search_image(img_query)
            if url:
                img_refs.append((i, url))
                parts[-1] += f"\n[img:{url}]"
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
        "mode": r"(?:<{2,3}|\*\*)\s*MODE\s*:?\s*(_?STEPS?|_?TEXT|_?TOOL|_?GUIDE)\s*(?:\*\*|>{2,3})",
        "step": r"(?:<{2,3}|\*\*)\s*STEP\b(?!S\b)\s*(?:\*\*|>{2,3})",
        "code": r"(?:<{2,3}|\*\*)\s*CODE\s*(?:\*\*|>{2,3})",
        "detail": r"(?:<{2,3}|\*\*)\s*DETAIL\s*(?:\*\*|>{2,3})",
        "final": r"(?:<{2,3}|\*\*)\s*FINAL[_\s-]*CODE\s*(?:\*\*|>{2,3})",
    }

    def _canon(m: re.Match) -> str:
        word = (m.group(1) or "").upper().replace(" ", "").lstrip("_")
        if word.startswith("TOOL"):
            word = "TOOL"
        elif word.startswith("GUIDE"):
            word = "TOOL"
        return f"<<<MODE:{word}>>>"

    text = re.sub(_NOISE["mode"], _canon, text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["step"], "<<<STEP>>>", text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["code"], "<<<CODE>>>", text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["detail"], "<<<DETAIL>>>", text, flags=re.IGNORECASE)
    text = re.sub(_NOISE["final"], "<<<FINAL_CODE>>>", text, flags=re.IGNORECASE)

    marker_re = re.compile(r"<<<MODE:(STEPS|TEXT|TOOL)>>>")
    m = marker_re.search(text)
    if not m:
        return "text", text.strip()
    mode = m.group(1).lower()
    body = text[m.end():].strip()
    if mode == "text":
        return "text", body

    if mode == "tool":
        return "steps", _parse_tool_answer(body)

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
    chat_id = question_chat_id_holder.get("chat_id", "")
    history = chat_history.recent(chat_id, limit=14)
    hist_text = "\n".join(
        f"[{m['role']}] {m['content'][:800]}" for m in history
    ) or "(none — no captures delivered to this chat yet)"
    # ground the answer in the user's indexed project structure (no-op when empty)
    try:
        from server import rag

        structure = rag.context_block(chat_id, question)
    except Exception:  # noqa: BLE001
        structure = ""
    full = FOLLOWUP_PROMPT.format(history=hist_text, question=question) + structure
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


def recent_session_images(db, chat_id: str | None = None) -> list[bytes]:
    """Images from the most recently active capture session (any status with
    captured parts) — used to give voice/text follow-ups visual context.
    Scoped to the asking chat, and CLOSED sessions are skipped so a previous
    question's screenshots never leak into the next one (the \"Q\" reset)."""
    from server.models import Capture, CaptureSession

    q = db.query(CaptureSession)
    if chat_id is not None:
        q = q.filter(CaptureSession.chat_id == str(chat_id))
    sess_ids = [
        s.id for s in q.filter(CaptureSession.status != "closed")
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


def answer_question_with_image(question: str, image_bytes: bytes, chat_id: str) -> str:
    """Solve ONE question given as image + optional text together (the mixed
    input path). Uses the stepped tutor pipeline; stores the solution summary
    in chat history for follow-ups."""
    from server.gemini import analyze_images_multi

    extra = question.strip() or None
    result = analyze_images_multi([image_bytes], extra_instruction=extra)
    if not result.get("has_question"):
        return (
            "🤔 I couldn't find a coding question in what you sent. "
            "Try rephrasing, or capture the screen with the question visible."
        )
    # render the structured solution via the same formatter as the capture flow
    class _Cap:  # minimal shape for format_solution
        problem_statement = result.get("problem_statement")
        solution_steps = result.get("solution_steps")
        optimized_code = result.get("optimized_code")
        time_complexity = result.get("time_complexity")
        space_complexity = result.get("space_complexity")
        notes = result.get("notes")

    rendered = format_solution(_Cap())
    chat_history.add(
        chat_id, "assistant",
        f"Solution (mixed input).\nProblem: {(result.get('problem_statement') or '')[:500]}\n"
        f"Final code:\n{(result.get('optimized_code') or '')[:1500]}",
    )
    return rendered


def answer_and_store(question: str, chat_id: str, include_images: bool = True) -> str:
    """Shared follow-up pipeline used by both the Telegram bot and the
    dashboard chat: store the question, answer with recent capture images as
    context, store the answer, return the rendered text. The question may be
    voice/text-only (no captures at all) — then it is solved directly as a
    fresh question with images=[] unless current-session images exist."""
    question_chat_id_holder["chat_id"] = chat_id
    chat_history.add(chat_id, "user", question)

    def _run(with_imgs: bool) -> str:
        imgs = _recent_images_safe(question_chat_id_holder.get("chat_id")) if with_imgs else []
        return answer_followup(question, imgs)

    def _send_images(text: str) -> None:
        # tool-guidance answers may carry [img:URL] refs — send them as photos
        for m in re.findall(r"\[img:(https?://[^\]\s]+)\]", text)[:2]:
            send_photo_url(chat_id, m, "from your answer steps")

    try:
        answer = _run(include_images)
    except Exception as e:  # noqa: BLE001
        if not include_images:
            answer = f"⚠️ Couldn't answer that right now: {str(e)[:150]}"
        else:
            # retry once text-only (e.g. context images too large)
            try:
                answer = _run(False)
            except Exception as e2:  # noqa: BLE001
                answer = f"⚠️ Couldn't answer that right now: {str(e2)[:150]}"
    chat_history.add(chat_id, "assistant", answer)
    if "[img:" in answer:
        _send_images(answer)
    return answer


def _handle_session_command(cmd: str, chat_id: str) -> None:
    from server.database import SessionLocal
    from server.models import Capture, CaptureSession
    from server.routers import agent as agent_api
    from server.gemini import analyze_images_multi

    db = SessionLocal()
    try:
        if cmd == "q":
            from server.routers.agent import reset_question_context

            result = reset_question_context(db, chat_id)
            if result["closed_sessions"]:
                send_message(
                    chat_id,
                    "✅ Question ended — context cleared. "
                    "The next capture/question starts fresh.",
                )
            else:
                send_message(
                    chat_id,
                    "✅ No open question — context cleared anyway. "
                    "The next capture/question starts fresh.",
                )
            return

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
            sess = (
                db.query(CaptureSession)
                .filter_by(status="open", chat_id=str(chat_id))
                .first()
            )
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
                    and m["content"].strip().lower() not in ("c", "s", "q", "status")
                ]
                extra = " ".join(pending[-3:]).strip() or None
                # ground the solve in the indexed project structure (no-op if none)
                try:
                    from server import rag as _rag

                    _structure = _rag.context_block(
                        chat_id, extra or "the captured question"
                    )
                except Exception:  # noqa: BLE001
                    _structure = ""
                result = analyze_images_multi(
                    images, extra_instruction=(extra or "") + _structure or None
                )
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
            cap.image_type = (result.get("image_type") or "").strip().lower() or None
            db.add(cap)
            sess.status = "solved"
            sess.solved_at = now
            db.commit()
            db.refresh(cap)

            # file-tree / file-content captures feed the project RAG index
            img_type = (result.get("image_type") or "").strip().lower()
            tree_entries = result.get("tree_entries") or []
            if img_type in ("file_tree", "file_content") and tree_entries:
                from server import rag

                if img_type == "file_tree":
                    n = rag.index_tree(chat_id, tree_entries, source=f"capture#{cap.id}")
                    send_message(
                        chat_id,
                        f"🗂 Learned your project structure — indexed {n} file(s)/folder(s). "
                        "Capture any file you want me to know, or just ask your question: "
                        "answers will use this structure.",
                    )
                else:
                    p = (tree_entries[0] or {}).get("path", "unknown.py")
                    content = cap.user_attempt or ""
                    ok = rag.index_file(chat_id, p, content)
                    if ok:
                        send_message(
                            chat_id,
                            f"📄 Indexed the contents of `{p}`. Show more files, or ask your question.",
                        )
                return

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
            sess = (
                db.query(CaptureSession)
                .filter_by(status="open", chat_id=str(chat_id))
                .first()
            )
            if not sess:
                send_message(chat_id, "No open session. Send c to start capturing, or Q to end the current question.")
            else:
                parts = db.query(Capture).filter_by(session_id=sess.id, status="captured").count()
                send_message(chat_id, f"Open session: {parts} part(s) captured. c = add part, s = solve, Q = end this question.")
    finally:
        db.close()


def format_solution(capture) -> str:
    parts = ["<b>📸 ScreenSolve — new solution</b>"]
    if capture.problem_statement:
        problem = markdown_to_telegram_html(capture.problem_statement)
        parts.append("\n<b>📋 Problem</b>\n<blockquote>" + problem + "</blockquote>")
    if capture.solution_steps:
        items = []
        for i, s in enumerate(capture.solution_steps):
            if isinstance(s, dict):
                text = markdown_to_telegram_html(s.get("step") or "")
                code = s.get("code")
                if code:
                    items.append(
                        f"<b>🔹 Step {i+1}</b>\n{text}\n<pre><code class=\"language-python\">{html.escape(code)}</code></pre>")
                else:
                    items.append(f"<b>🔹 Step {i+1}</b>\n{text}")
            else:
                items.append(f"<b>🔹 Step {i+1}</b>\n" + markdown_to_telegram_html(str(s)))
        parts.append("\n<b>🧩 Approach — building it up</b>\n" + "\n\n▪️\n\n".join(items))
    if capture.optimized_code:
        parts.append("\n<b>✅ Optimized code</b>\n<pre><code class=\"language-python\">" + html.escape(capture.optimized_code) + "</code></pre>")
    badges = [b for b in (capture.time_complexity, capture.space_complexity) if b]
    if badges:
        parts.append("\n<b>⚡ Complexity:</b> " + " · ".join(markdown_to_telegram_html(b) for b in badges))
    if capture.notes:
        parts.append("\n<b>💡 Feedback</b>\n" + markdown_to_telegram_html(capture.notes))
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

    if text and text.strip().lower() in ("c", "s", "q", "status"):
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
    send_message(chat_id, answer := answer_and_store(question, chat_id))


def _recent_images_safe(chat_id: str | None = None) -> list[bytes]:
    from server.database import SessionLocal

    try:
        db = SessionLocal()
        try:
            return recent_session_images(db, chat_id)
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
