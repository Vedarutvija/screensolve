import base64
import json
import os
import re

import httpx

from server.config import GEMINI_API_KEY, GEMINI_MODEL, OPENAI_API_KEY, OPENAI_BASE_URL, VISION_MODEL

PROMPT = """You are an expert competitive-programming coach. Analyze the screenshot.

Look at the screen content and decide:
1. Does the screen contain a coding/algorithm problem statement, a question, or code the user is writing to solve something (Python expected)?
2. If the user has written/partially written a solution, extract it.

Then produce an OPTIMIZED step-by-step solution for Python.

Return ONLY a JSON object (no markdown fences) with exactly these keys:
{
  "has_question": true/false,
  "problem_statement": "clear restatement of the problem/question, or null",
  "user_attempt": "any partial code or working the user already wrote, verbatim, or null",
  "solution_steps": ["ordered, concise steps explaining the approach and why it is optimal"],
  "optimized_code": "complete, optimized, runnable Python solution with brief comments, or null",
  "time_complexity": "e.g. O(n log n), or null",
  "space_complexity": "e.g. O(n), or null",
  "notes": "feedback on the user's attempt (bugs, inefficiencies, improvements), or null"
}

Rules:
- If there is NO question/problem/solution attempt on screen, return has_question=false and null for everything else.
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
