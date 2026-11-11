"""Hybrid dense∥BM25 channel pack (3 dense + BM25 supplements)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.sparse import SparseIndex, channel_pack, tokenize  # noqa: E402


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


def test_channel_pack_takes_dense_then_sparse_supplements():
    dense = [
        {
            "id": f"d{i}",
            "text": f"dense {i}",
            "score": 0.9 - i * 0.05,
            "metadata": {"source": f"d{i}.md"},
        }
        for i in range(1, 6)
    ]
    sparse = [
        {
            "id": "s1",
            "text": "math exact",
            "score": 12.0,
            "metadata": {"source": "math.md"},
        },
        {
            "id": "d1",
            "text": "dense 1",
            "score": 1.0,
            "metadata": {"source": "d1.md"},
        },
        {
            "id": "s2",
            "text": "fees exact",
            "score": 8.0,
            "metadata": {"source": "fees.md"},
        },
    ]
    packed, dbg = channel_pack(dense, sparse, dense_n=3, keep_k=5)
    assert dbg["mode"] == "channel_pack"
    sources = [h["metadata"]["source"] for h in packed]
    assert sources[:3] == ["d1.md", "d2.md", "d3.md"]
    assert "math.md" in sources
    assert "fees.md" in sources
    assert len(packed) == 5
    assert dbg["dense_kept"] == 3
    assert dbg["sparse_added"] == 2
    # d1 appears in both pools — counted once, not double-kept
    assert sources.count("d1.md") == 1
    assert (packed[0].get("fusion") or {}).get("channels") == "both"


def test_channel_pack_skips_duplicate_sparse():
    dense = [
        {
            "id": "a",
            "text": "a",
            "score": 0.9,
            "metadata": {"source": "a.md"},
        },
        {
            "id": "b",
            "text": "b",
            "score": 0.8,
            "metadata": {"source": "b.md"},
        },
        {
            "id": "c",
            "text": "c",
            "score": 0.7,
            "metadata": {"source": "c.md"},
        },
    ]
    sparse = [
        {
            "id": "a",
            "text": "a",
            "score": 5.0,
            "metadata": {"source": "a.md"},
        },
        {
            "id": "x",
            "text": "x",
            "score": 4.0,
            "metadata": {"source": "x.md"},
        },
    ]
    packed, dbg = channel_pack(dense, sparse, dense_n=3, keep_k=4)
    sources = [h["metadata"]["source"] for h in packed]
    assert sources == ["a.md", "b.md", "c.md", "x.md"]
    assert dbg["skipped_dupes"] >= 1
    assert dbg["sparse_added"] == 1
