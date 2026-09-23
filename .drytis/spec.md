# ScreenSolve — Blueprint

Hotkey-driven screen capture → Gemini vision → optimized Python solutions on a mobile dashboard.

## Architecture
- **agent/** — Python desktop agent: global hotkey (default Ctrl+Alt+S, configurable), captures full screen (fallback active window), POSTs PNG to server `/api/captures`. Silent — no periodic capture.
- **server/** — FastAPI + MySQL (SQLAlchemy). Endpoints: `POST /api/captures`, `GET /api/captures`, `GET /api/captures/{id}`, `GET /health`; serves dashboard at `/`.
- **dashboard/** — mobile-first web page: auto-refreshing timeline, cards show detected problem, status, step-by-step optimized solution, complexity, highlighted Python code, notes on user's partial attempt.
- **Gemini** — `gemini-2.0-flash` via google-genai SDK. Output JSON: {has_question, problem_statement, user_attempt, solution_steps[], optimized_code, time_complexity, space_complexity, notes}.

## Data model (captures)
id, image_path, status (pending/analyzing/no_question/solved/error), problem_statement, user_attempt, solution_steps JSON, optimized_code, time_complexity, space_complexity, notes, error_message, created_at, analyzed_at.

## Env
GEMINI_API_KEY (secret), DB_*, UPLOAD_DIR; agent config file for endpoint + hotkey.
