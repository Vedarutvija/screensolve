"""Chroma-backed RAG over the user's project structure.

The user shows a file-tree screenshot (or a file's contents) — ScreenSolve
indexes it so later answers are grounded in the ACTUAL project layout, and the
bot can ask the user to show a specific file when it needs more.

Persistence: chromadb.PersistentClient on disk (survives restarts).
Graceful degradation: if chromadb (or its embedding backend) is unavailable,
index/query become no-ops and solves proceed without RAG context — never a
crash.
"""

import logging
import os
import re
import threading

logger = logging.getLogger("screensolve.rag")

_PERSIST_DIR = os.getenv("CHROMA_DIR", "/workspace/data/chroma")
_COLL_PREFIX = "proj_"

_client = None
_lock = threading.Lock()


def _get_client():
    global _client
    with _lock:
        if _client is None:
            import chromadb

            os.makedirs(_PERSIST_DIR, exist_ok=True)
            _client = chromadb.PersistentClient(path=_PERSIST_DIR)
        return _client


def _collection(chat_id: str):
    name = _COLL_PREFIX + re.sub(r"[^A-Za-z0-9_-]", "_", str(chat_id))[:40]
    return _get_client().get_or_create_collection(
        name=name, metadata={"hnsw:space": "cosine"}
    )


def available() -> bool:
    try:
        _get_client()
        return True
    except Exception:  # noqa: BLE001
        logger.warning("chroma unavailable — RAG disabled", exc_info=True)
        return False


def index_tree(chat_id: str, entries: list[dict], source: str = "screenshot") -> int:
    """Index a parsed file-tree. entries: [{"path": "server/main.py",
    "type": "file"|"dir", "note": optional description}]. Upserts by path."""
    entries = [e for e in entries if (e.get("path") or "").strip()]
    if not entries or not available():
        return 0
    try:
        col = _collection(chat_id)
        ids, docs, metas = [], [], []
        for i, e in enumerate(entries):
            path = e["path"].strip()
            ids.append(re.sub(r"[^A-Za-z0-9_-]", "_", path)[:120] or f"e{i}")
            kind = e.get("type") or ("dir" if path.endswith("/") else "file")
            note = (e.get("note") or "").strip()
            doc = f"{kind}: {path}" + (f" — {note}" if note else "")
            docs.append(doc)
            metas.append({"path": path, "kind": kind, "source": source})
        col.upsert(ids=ids, documents=docs, metadatas=metas)
        return len(ids)
    except Exception:  # noqa: BLE001
        logger.exception("index_tree failed")
        return 0


def index_file(chat_id: str, path: str, content: str) -> bool:
    """Index one file's contents (from a file-content capture) in chunks so
    retrieval can point at the relevant part of the file."""
    path = (path or "").strip()
    if not path or not available():
        return False
    content = (content or "").strip()
    if not content:
        return False
    try:
        col = _collection(chat_id)
        chunks = _chunk(content)
        ids, docs, metas = [], [], []
        for i, ch in enumerate(chunks):
            ids.append((re.sub(r"[^A-Za-z0-9_-]", "_", path)[:100] + f"_c{i}"))
            docs.append(f"file: {path}\n{ch}")
            metas.append({"path": path, "kind": "file_content", "chunk": i})
        col.upsert(ids=ids, documents=docs, metadatas=metas)
        return True
    except Exception:  # noqa: BLE001
        logger.exception("index_file failed")
        return False


def _chunk(text: str, size: int = 1200) -> list[str]:
    lines, out, buf = text.splitlines(), [], ""
    for ln in lines:
        if len(buf) + len(ln) + 1 > size and buf:
            out.append(buf.rstrip())
            buf = ""
        buf += ln + "\n"
    if buf.strip():
        out.append(buf.rstrip())
    return out or [text[:size]]


def query(chat_id: str, question: str, n: int = 6) -> list[dict]:
    """Retrieve the most relevant indexed structure/file entries for a question."""
    if not available() or not (question or "").strip():
        return []
    try:
        col = _collection(chat_id)
        if col.count() == 0:
            return []
        res = col.query(query_texts=[question[:800]], n_results=min(n, col.count()))
        out = []
        for docs, metas in zip(res.get("documents") or [], res.get("metadatas") or []):
            for doc, meta in zip(docs, metas):
                out.append({"doc": doc, **(meta or {})})
        return out
    except Exception:  # noqa: BLE001
        logger.exception("rag query failed")
        return []


def context_block(chat_id: str, question: str, n: int = 6) -> str:
    """Rendered RAG context to prepend to a solve prompt ('' when nothing)."""
    hits = query(chat_id, question, n)
    if not hits:
        return ""
    lines = "\n".join(f"- {h['doc']}" for h in hits)
    return (
        "\n\nKNOWN PROJECT STRUCTURE (from screenshots the user showed earlier) — "
        "ground your answer in these real paths; if the solution requires a file "
        "NOT listed here, explicitly ask the user to capture/show that file:\n"
        + lines
    )
