"""CRAG-lite corrective rewrite — never proactive on turn 1."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rag.pipeline import _grade_retrieval  # noqa: E402
from services.demo_polish import (  # noqa: E402
    needs_retrieval_rewrite,
    rewrite_retrieval_query,
)


def test_first_turn_more_about_programmes_not_deictic():
    msg = "Hi I love you! I want to know more about your math programmes"
    # No prior — even with include_new-style echo, prior strip empties it
    fake = f"Parent: {msg}"
    assert not needs_retrieval_rewrite(msg, fake)
    out = rewrite_retrieval_query(msg, fake, force=True)
    assert out["rewritten"] is False
    assert out["reason"] == "no_prior"


def test_deictic_with_prior():
    hist = "Parent: Tell me about maths?\nBot: Clevercal Math blends…"
    assert needs_retrieval_rewrite("can you tell me more about that?", hist)


def test_grade_correct_vs_incorrect():
    strong = [
        {
            "text": "math fees",
            "score": 0.55,
            "metadata": {"source": "en/programmes/Clevercal.md", "title": "Math"},
        }
    ]
    g = _grade_retrieval("math programmes", strong, local_embeddings=False)
    assert g["grade"] == "correct"

    weak = [
        {
            "text": "pets policy",
            "score": 0.15,
            "metadata": {"source": "en/policies/pets.md", "title": "Pets"},
        }
    ]
    g2 = _grade_retrieval("math programmes", weak, local_embeddings=False)
    assert g2["grade"] == "incorrect"


def test_force_rewrite_skipped_without_prior():
    out = rewrite_retrieval_query(
        "tell me more about that",
        "",
        grade="incorrect",
        force=True,
    )
    assert out["rewritten"] is False
    assert out["reason"] == "no_prior"
