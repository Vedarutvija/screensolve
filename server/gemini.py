import base64
import json
import re

import httpx

from server.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    VISION_MODEL,
)

PROMPT = """You are an expert programming tutor.

Look at the screenshot and determine whether a clear coding question, programming problem, error message, or code-related request is visible. Count ANY of these as a question:
- a problem statement (LeetCode/HackerRank style or from notes)
- a search query about how to do something in code (e.g. "nested json flatten python")
- an error message or traceback the user is facing
- code the user is writing/debugging toward a goal

If there is genuinely NO coding-related content on screen (e.g. a blank desktop, social media, unrelated documents), set has_question=false. Otherwise has_question=true — even if the problem is only implied by a search query or error.

When has_question=true, solve it like a LIVE coding interview (TopTal style): think out loud, one small concept at a time, explaining WHY before writing anything.

STRICT RULES for solution_steps:
1. GRANULARITY: each step introduces exactly ONE concept — e.g. "define the function signature", "create an empty dict/list", "handle the nested-dict case", "handle the list case", "handle leaf values", "wire up the entry point", "handle edge cases". Never bundle two ideas into one step.
2. REASONING FIRST: every step's explanation must start with WHY we are doing this before what. Example: "We create an empty dictionary here because we need a single place to collect flattened key-value pairs as the recursion walks the structure — every branch will write into it." Then a sentence on WHAT this step adds.
3. ADDITIVE CODE: each step's "code" contains the ENTIRE code accumulated so far, with this step's addition highlighted by a brief inline comment. The final step's code must be the complete working solution.
4. NARRATE like a tutor talking while typing: "let's...", "now we...", "notice that...".
5. Keep it to at most 10 steps; if the solution is trivial, fewer steps is fine.
6. After the code in optimized_code, keep the code clean (final version without the narration comments).

Return ONLY a JSON object (no markdown fences) with exactly these keys:
{
  "has_question": true/false,
  "problem_statement": "clear restatement of the problem/question, or null",
  "user_attempt": "any partial code or working the user already wrote, verbatim, or null",
  "solution_steps": [
    {"step": "WHY this step exists first (reasoning), then WHAT it adds — tutor voice",
     "code": "the ENTIRE code accumulated so far, including everything from previous steps plus this step's addition"}
  ],
  "optimized_code": "the complete, final, optimized, runnable Python solution (same as the last step's code, cleaned up), or null",
  "time_complexity": "e.g. O(n log n), or null",
  "space_complexity": "e.g. O(n), or null",
  "notes": "feedback on the user's attempt/next steps (bugs, inefficiencies, improvements), or null"
}

Example of the additive, reasoning-first pattern (steps shown abbreviated):
step 1: "We start by converting the given integer to a string, because binary
gaps are about the characters of the representation, not the number's value —
string indexing lets us scan runs of zeros easily."
  code: N = 10010001\\nn = str(N)
step 2: "Now we define a function to hold the gap-finding logic — wrapping it
in a function keeps the scan reusable and testable instead of loose script
code."
  code: N = 10010001\\nn = str(N)\\ndef find_gap(n):
step 3: "We create an empty string here because we need a place to build up
each run of bits as we walk through them character by character."
  code: N = 10010001\\nn = str(N)\\ndef find_gap(n):\\n    new_string = ""
(each step repeats everything before it, then adds)

Rules:
- Prefer Python solutions unless the screen clearly demands another language.
- Prefer the most efficient approach; mention alternatives briefly in steps.
- Do not invent problems that are not visible."""


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON in model response")
    return json.loads(text[start : end + 1])


def _analyze_gemini(image_bytes: bytes, mime_type: str) -> dict:
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            PROMPT,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.2,
            max_output_tokens=8192,
        ),
    )
    return _extract_json(response.text or "")


def _analyze_openai(image_bytes: bytes, mime_type: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            resp = httpx.post(
                f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                json={
                    "model": VISION_MODEL,
                    "temperature": 0.2,
                    "max_tokens": 8192,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:{mime_type};base64,{b64}"},
                                },
                            ],
                        }
                    ],
                },
                timeout=300,
            )
            resp.raise_for_status()
            return _extract_json(resp.json()["choices"][0]["message"]["content"])
        except (httpx.TimeoutException, httpx.HTTPStatusError) as e:
            # retry once on transient gateway slowness / 5xx
            if isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500:
                raise
            last_err = e
    raise last_err  # type: ignore[misc]


def analyze_image(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    if GEMINI_API_KEY:
        return _analyze_gemini(image_bytes, mime_type)
    if OPENAI_API_KEY:
        return _analyze_openai(image_bytes, mime_type)
    raise RuntimeError("No vision API key configured (set GEMINI_API_KEY or OPENAI_API_KEY)")


MULTI_PREFIX = """These are {n} screenshots of the SAME screen, taken while the user
scrolled through one coding question (part 1, then part 2, ... in order).
Read them IN ORDER and stitch the content together into one complete question
before solving. Overlapping regions should be merged, not re-solved.

"""

JSON_ONLY_SUFFIX = """

CRITICAL OUTPUT RULE: your entire response must be ONE raw JSON object and
nothing else — no markdown fences, no prose before or after, no explanation.
Start your response with { and end it with }."""


def analyze_images_multi(image_parts: list[bytes], mime_type: str = "image/png") -> dict:
    """Analyze several screenshots (scroll parts) as ONE combined question."""
    if not image_parts:
        raise ValueError("no images")
    if len(image_parts) == 1:
        return analyze_image(image_parts[0], mime_type)

    prompt = MULTI_PREFIX.format(n=len(image_parts)) + PROMPT
    last_err = None
    for attempt in range(3):
        try:
            if attempt == 1:
                prompt = MULTI_PREFIX.format(n=len(image_parts)) + PROMPT + JSON_ONLY_SUFFIX
            return _analyze_multi_once(image_parts, mime_type, prompt,
                                       max_tokens=8192 if attempt < 2 else 16384)
        except (ValueError, KeyError) as e:
            last_err = e
            continue
    raise RuntimeError(
        f"The vision model could not produce a structured answer after 3 attempts "
        f"(last error: {last_err}). Try fewer parts or send s again."
    )


def _analyze_multi_once(image_parts: list[bytes], mime_type: str, prompt: str,
                        max_tokens: int) -> dict:
    import logging

    if GEMINI_API_KEY:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=GEMINI_API_KEY)
        contents: list = [
            types.Part.from_bytes(data=p, mime_type=mime_type) for p in image_parts
        ]
        contents.append(prompt)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                max_output_tokens=max_tokens,
            ),
        )
        return _extract_json(response.text or "")

    if OPENAI_API_KEY:
        content: list = [{"type": "text", "text": prompt}]
        for p in image_parts:
            b64 = base64.b64encode(p).decode()
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime_type};base64,{b64}"},
            })
        resp = httpx.post(
            f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json={
                "model": VISION_MODEL,
                "temperature": 0.2,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            },
            timeout=300,
        )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"] or ""
        logging.getLogger("screensolve").warning(
            "multi-image response len=%s head=%r", len(text), text[:150]
        )
        return _extract_json(text)

    raise RuntimeError("No vision API key configured")
