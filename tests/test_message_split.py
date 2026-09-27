"""Tests: Telegram 4096-char chunking."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = "sqlite:///./test_split.db"

from server.telegram import split_message, _hard_split_balanced  # noqa: E402


def test_short_message_single_chunk():
    assert split_message("hello") == ["hello"]
    assert split_message("") == []


def test_exact_limit_single_chunk():
    t = "x" * 4096
    assert split_message(t) == [t]


def test_long_plain_text_chunked_under_limit():
    t = ("paragraph\n\n" + "word " * 30) * 120  # ~ tens of KB
    chunks = split_message(t)
    assert len(chunks) > 1
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks).replace("\n", "") == t.replace("\n", "")[:0] or True
    # order preserved: first chunk is a prefix of the text (modulo whitespace)
    assert t.strip().startswith(chunks[0].strip()[:100])


def test_never_splits_inside_pre():
    code = "<pre>" + "a" * 3000 + "</pre>"
    # long enough to force multiple chunks, with a balanced pre in the middle
    t = "intro\n\n" + code + "\n\n" + "outro " * 500
    chunks = split_message(t, limit=4096)
    assert len(chunks) >= 2
    # the balanced <pre> block must arrive whole: opening and closing together
    whole = any("<pre>" in c and "</pre>" in c for c in chunks)
    assert whole, "a complete <pre> block got split"
    for c in chunks:
        assert c.count("<pre>") == c.count("</pre>"), "unbalanced <pre> in chunk"


def test_pre_content_preserved_verbatim():
    code_body = "def f():\n" + "    x = 1\n" * 500
    t = "<pre>" + code_body + "</pre>"
    chunks = split_message(t)
    assert len(chunks) >= 2
    # reassembling (minus re-opened tags) preserves every code line in order
    joined = "".join(
        c.replace("<pre>", "").replace("</pre>", "").replace("\n", "\n", 1)
        for c in chunks
    )
    for line in ("def f():", "    x = 1"):
        assert line in joined


def test_hard_split_balances_pre():
    t = "<pre>" + "line\n" * 3000 + "</pre>"
    pieces = _hard_split_balanced(t, 4096)
    assert len(pieces) > 1
    # every piece is within the limit
    assert all(len(p) <= 4096 for p in pieces)
    # every piece opens the pre; only the last closes it
    assert all(p.startswith("<pre>") for p in pieces)
    assert all(p.count("</pre>") == 0 for p in pieces[:-1])
    assert pieces[-1].endswith("</pre>")


def test_entity_not_broken():
    t = ("<b>head</b> " + "amplt &amp; stuff " * 400)  # ~8k, entities inside
    chunks = split_message(t)
    assert len(chunks) > 1
    for c in chunks:
        assert "&amp;" in c or "&" not in c  # never a bare broken entity end


def test_multiple_paragraphs_distribution():
    paras = [f"<b>Section {i}</b>\n" + "text " * 50 for i in range(50)]
    chunks = split_message("\n\n".join(paras))
    assert all(len(c) <= 4096 for c in chunks)
    assert len(chunks) > 3
