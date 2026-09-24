import base64
import json
import os
import re

import httpx

from server.config import GEMINI_API_KEY, GEMINI_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL, VISION_MODEL

PROMPT = """You are an expert programming tutor.

Look at the screenshot and determine whether a clear coding question, programming problem, error message, or code-related request is visible. Count ANY of these as a question:
- a problem statement (LeetCode/HackerRank style or from notes)
- a search query about how to do something in code (e.g. "nested json flatten python")
- an error message or traceback the user is facing
- code the user is writing/debugging toward a goal

If there is genuinely NO coding-related content on screen (e.g. a blank desktop, social media, unrelated documents), set has_question=false. Otherwise has_question=true — even if the problem is only implied by a search query or error.

When has_question=true, solve it step-by-step with an ADDITIVE pattern: each subsequent step ACCUMULATES the previous steps — it repeats all code built so far, expanded or restructured, so by the final step the full working solution is complete. Every element of solution_steps must contain a "step" explanation plus "code" holding the cumulative code so far.

Return ONLY a JSON object (no markdown fences) with exactly these keys:
{
  "has_question": true/false,
  "problem_statement": "clear restatement of the problem/question, or null",
  "user_attempt": "any partial code or working the user already wrote, verbatim, or null",
  "solution_steps": [
    {"step": "short explanation of what this step adds and why",
     "code": "the ENTIRE code accumulated so far, including everything from previous steps plus this step's addition"}
  ],
  "optimized_code": "the complete, final, optimized, runnable Python solution (same as the last step's code, cleaned up), or null",
  "time_complexity": "e.g. O(n log n), or null",
  "space_complexity": "e.g. O(n), or null",
  "notes": "feedback on the user's attempt/next steps (bugs, inefficiencies, improvements), or null"
}

Example of the additive pattern (steps shown abbreviated):
step 1: lets convert the given integer to str
  code: N = 10010001\\nn = str(N)
step 2: lets define a function to find the gap
  code: N = 10010001\\nn = str(N)\\ndef find_gap(n):
step 3: lets initialize empty string for appending the substrings
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
        ),
    )
    return _extract_json(response.text or "")


def _analyze_openai(image_bytes: bytes, mime_type: str) -> dict:
    b64 = base64.b64encode(image_bytes).decode()
    resp = httpx.post(
        f"{OPENAI_BASE_URL.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        json={
            "model": VISION_MODEL,
            "temperature": 0.2,
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
        timeout=120,
    )
    resp.raise_for_status()
    return _extract_json(resp.json()["choices"][0]["message"]["content"])


def analyze_image(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    if GEMINI_API_KEY:
        return _analyze_gemini(image_bytes, mime_type)
    if OPENAI_API_KEY:
        return _analyze_openai(image_bytes, mime_type)
    raise RuntimeError("No vision API key configured (set GEMINI_API_KEY or OPENAI_API_KEY)")
