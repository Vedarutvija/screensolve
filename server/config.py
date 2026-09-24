import os

try:
    from dotenv import load_dotenv

    load_dotenv("/workspace/.env")
except ImportError:
    pass


def _default_db_url() -> str:
    host = os.getenv("DB_HOST", "127.0.0.1")
    port = os.getenv("DB_PORT", "3306")
    name = os.getenv("DB_NAME", "screensolve")
    user = os.getenv("DB_USER", "root")
    pwd = os.getenv("DB_PASSWORD", "")
    auth = f"{user}:{pwd}@" if pwd else f"{user}@"
    return f"mysql+pymysql://{auth}{host}:{port}/{name}"


DATABASE_URL = os.getenv("DATABASE_URL") or _default_db_url()
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/workspace/data/captures")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o-mini")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
PORT = int(os.getenv("PORT", "8000"))

os.makedirs(UPLOAD_DIR, exist_ok=True)
