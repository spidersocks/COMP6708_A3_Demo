"""Age-band questions must ground on faq/age docs, not citation-gate."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.pipeline import (  # noqa: E402
    _content_tokens,
    _off_topic,
    _query_age_digits,
    chat_with_rag,
)


def test_age_digits_kept_in_tokens():
    toks = _content_tokens("Do you offer phonics classes for 5 year olds?")
    assert "5" in toks
    assert "phonics" in toks
    assert "year" in toks
    assert "old" in toks


def test_query_age_digits():
    assert _query_age_digits("phonics for my 5 year old") == {"5"}
    assert "5" in _query_age_digits("classes for 5 year olds?")


def test_phonics_five_year_old_not_gated():
    result = chat_with_rag(
        "Do you offer phonics classes for 5 year olds?",
        language="en",
        debug=True,
    )
    dbg = result.get("debug") or {}
    assert not dbg.get("knowledge_gap"), dbg
    cites = result.get("citations") or []
    assert cites, "expected age FAQ citations"
    sources = " ".join(str(c.get("source") or "") for c in cites)
    assert "age_4-5" in sources or "age_5-6" in sources
    answer = (result.get("answer") or "").lower()
    assert "phonic" in answer
    assert not _off_topic(
        "Do you offer phonics classes for 5 year olds?",
        {"metadata": {"source": cites[0].get("source")}, "text": cites[0].get("snippet") or ""},
    )
