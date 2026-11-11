"""Contextual follow-ups should trigger retrieval query rewrite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.demo_polish import needs_retrieval_rewrite, rewrite_retrieval_query  # noqa: E402


def test_needs_rewrite_for_tell_me_more():
    hist = "Parent: Tell me about your maths courses?\nAssistant: Clevercal Math blends…"
    assert needs_retrieval_rewrite("can you tell me more about that?", hist)
    assert not needs_retrieval_rewrite(
        "What are your opening hours on Saturday?",
        hist,
    )


def test_rewrite_fail_open_without_history():
    out = rewrite_retrieval_query("tell me more about that", "")
    assert out["query"] == "tell me more about that"
    assert out["rewritten"] is False
    assert out["reason"] in {"skipped", "standalone", "disabled", "no_llm"}
