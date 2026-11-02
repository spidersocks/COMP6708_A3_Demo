"""Intent arbiter: only review structured picks; parse JSON safely."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import intent_arbiter as ia  # noqa: E402


def test_should_review_skips_hard_routes():
    assert ia.should_review("rag") is False
    assert ia.should_review("staff_escalation") is False
    assert ia.should_review("greeting") is False


def test_should_review_skips_active_cancel(monkeypatch):
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(ia.SETTINGS, "dashscope_api_key", "x")
    assert ia.should_review("cancel_dialogue", debug={"cancel_active": True}) is False
    assert ia.should_review("teachers", debug={}) is True
    assert ia.should_review("confused", debug={}) is True
    assert ia.should_review("off_domain", debug={}) is True


def test_review_reroutes_to_rag(monkeypatch):
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(ia.SETTINGS, "dashscope_api_key", "x")
    monkeypatch.setattr(ia.SETTINGS, "intent_arbiter_timeout_s", 2.0)

    with patch(
        "services.intent_arbiter.cheap_llm.complete_json",
        return_value={"action": "rag", "route": "rag", "reason": "needs KB narrative"},
    ):
        out = ia.review_intent(
            "Do you offer phonics classes for 5 year olds?",
            proposed_route="teachers",
            scores={"teachers": 0.95},
            debug={},
        )
    assert out["action"] == "rag"
    assert out["route"] == "rag"
