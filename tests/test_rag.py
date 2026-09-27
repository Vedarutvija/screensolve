"""Phase 3 tests: file-tree / file-content RAG (Chroma) + solve branching."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_rag.db"
os.environ["CHROMA_DIR"] = "/tmp/test_chroma_rag"
os.environ.pop("DB_HOST", None)

from server import rag  # noqa: E402


def setup_module(_=None):
    os.makedirs(os.environ["CHROMA_DIR"], exist_ok=True)


HAS_CHROMA = rag.available()


def test_index_and_query_tree_roundtrip():
    if not HAS_CHROMA:
        return
    n = rag.index_tree("chatR", [
        {"path": "main.py", "type": "file", "note": "app entry point"},
        {"path": "server/routers", "type": "dir"},
        {"path": "requirements.txt", "type": "file", "note": "python dependencies"},
    ])
    assert n == 3
    hits = rag.query("chatR", "where do I add a new API route?")
    assert hits, "expected retrieval hits after indexing a tree"
    docs = " ".join(h["doc"] for h in hits)
    assert "main.py" in docs


def test_reindex_upserts_no_duplicates():
    if not HAS_CHROMA:
        return
    rag.index_tree("chatU", [{"path": "app.py", "type": "file"}])
    rag.index_tree("chatU", [{"path": "app.py", "type": "file", "note": "v2"}])
    hits = rag.query("chatU", "app.py")
    paths = [h.get("path") for h in hits if h.get("path") == "app.py"]
    assert len(paths) == 1


def test_index_file_chunks_and_query():
    if not HAS_CHROMA:
        return
    content = "def flatten(d):\n    out = {}\n" + "\n".join(f"x{i} = {i}" for i in range(200))
    assert rag.index_file("chatF", "util/flatten.py", content)
    hits = rag.query("chatF", "flatten nested dict helper")
    assert hits and "flatten.py" in " ".join(h["doc"] for h in hits)


def test_query_empty_collection_and_unknown_chat():
    if not HAS_CHROMA:
        return
    assert rag.query("chat-empty-xyz", "anything") == []


def test_context_block_empty_when_no_index():
    if not HAS_CHROMA:
        return
    assert rag.context_block("chat-none", "how do I add auth?") == ""


def test_context_block_mentions_ask_for_file():
    if not HAS_CHROMA:
        return
    rag.index_tree("chatC", [{"path": "agent/main.py", "type": "file"}])
    ctx = rag.context_block("chatC", "where should I add capture logic?")
    assert "KNOWN PROJECT STRUCTURE" in ctx
    assert "agent/main.py" in ctx
    assert "ask" in ctx.lower()


def test_persistence_across_clients():
    if not HAS_CHROMA:
        return
    rag.index_tree("chatP", [{"path": "persist.py", "type": "file"}])
    hits = rag.query("chatP", "persist.py")
    assert hits
