"""Hybrid BM25 + dense RRF fusion."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.sparse import SparseIndex, rrf_fuse, tokenize  # noqa: E402


def test_tokenize_english_and_cjk():
    toks = tokenize("Math programmes 數學課程")
    assert "math" in toks
    assert "programmes" in toks
    # CJK bigrams, not every unigram
    assert "數學" in toks
    assert "數" not in toks


def test_tokenize_drops_stopwords_and_single_letters():
    toks = tokenize("I want phonics with my child")
    assert "i" not in toks
    assert "with" not in toks
    assert "my" not in toks
    assert "phonics" in toks
    assert "want" in toks
    assert "child" in toks


def test_bm25_does_not_rank_on_stopword_i():
    idx = SparseIndex()
    idx.rebuild(
        [
            {
                "id": "noise",
                "text": "I am with him. I think I will.",
                "metadata": {"source": "noise.md", "language": "en"},
            },
            {
                "id": "phonic",
                "text": "Phonics programmes build early reading skills.",
                "metadata": {"source": "phonics.md", "language": "en"},
            },
        ]
    )
    hits = idx.query("I want phonics", top_k=2, language="en")
    assert hits
    assert hits[0]["metadata"]["source"] == "phonics.md"
    matched = set(tokenize("I want phonics")) & set(tokenize(hits[0]["text"]))
    assert "i" not in matched
    assert "phonics" in matched


def test_bm25_prefers_keyword_doc():
    idx = SparseIndex()
    idx.rebuild(
        [
            {
                "id": "a",
                "text": "Pets are not allowed at the centre.",
                "metadata": {"source": "pets.md", "language": "en", "title": "Pets"},
            },
            {
                "id": "b",
                "text": "Clevercal Math programmes blend conceptual teaching with practice.",
                "metadata": {
                    "source": "clevercal.md",
                    "language": "en",
                    "title": "Math",
                },
            },
        ]
    )
    hits = idx.query("math programmes Clevercal", top_k=2, language="en")
    assert hits
    assert hits[0]["metadata"]["source"] == "clevercal.md"


def test_rrf_promotes_sparse_only_hit():
    dense = [
        {
            "id": "d1",
            "text": "pets",
            "score": 0.9,
            "metadata": {"source": "pets.md"},
        }
    ]
    sparse = [
        {
            "id": "s1",
            "text": "math",
            "score": 12.0,
            "metadata": {"source": "math.md"},
        },
        {
            "id": "d1",
            "text": "pets",
            "score": 1.0,
            "metadata": {"source": "pets.md"},
        },
    ]
    fused, dbg = rrf_fuse(dense, sparse, rrf_k=60, limit=5)
    assert dbg["mode"] == "hybrid_rrf"
    sources = [h["metadata"]["source"] for h in fused]
    assert "math.md" in sources
    both = [h for h in fused if (h.get("fusion") or {}).get("channels") == "both"]
    assert both and both[0]["metadata"]["source"] == "pets.md"
