from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from server.config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=280)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_startup_migrations() -> None:
    """Lightweight idempotent column migrations for tables created before a
    new column existed (project uses create_all, which never alters tables)."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    if "capture_sessions" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("capture_sessions")}
        if "chat_id" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE capture_sessions ADD COLUMN chat_id VARCHAR(64) DEFAULT 'dashboard'"
                ))
                conn.execute(text(
                    "ALTER TABLE capture_sessions ADD INDEX ix_capture_sessions_chat_id (chat_id)"
                ))
