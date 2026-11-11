"""Action policy promotes RAG misses and rejects unbacked commit drafts."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import action_policy as ap  # noqa: E402
from services.intent_router import decide_intent  # noqa: E402


def test_disabled_by_default_in_unit_tests(monkeypatch):
    monkeypatch.delenv("BRIGHTNEST_ACTION_POLICY", raising=False)
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "0")
    out = ap.classify_turn("get my kid into the sounds class")
    assert out["called"] is False
    assert out["route"] == "rag"


def test_promote_book_when_confident(monkeypatch):
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "1")
    monkeypatch.setattr(ap.cheap_llm, "enabled", lambda: True)
    monkeypatch.setattr(ap.cheap_llm, "arbiter_model", lambda: "test-model")
    with patch(
        "services.action_policy.cheap_llm.complete_json",
        return_value={"act": "book", "confidence": 0.91, "reason": "wants a seat"},
    ):
        verdict = ap.classify_turn("get my kid into the sounds class please")
    assert ap.should_promote(verdict)
    assert verdict["route"] == "booking_dialogue"


def test_low_confidence_stays_rag(monkeypatch):
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "1")
    monkeypatch.setattr(ap.cheap_llm, "enabled", lambda: True)
    monkeypatch.setattr(ap.cheap_llm, "arbiter_model", lambda: "test-model")
    with patch(
        "services.action_policy.cheap_llm.complete_json",
        return_value={"act": "book", "confidence": 0.4, "reason": "maybe"},
    ):
        verdict = ap.classify_turn("phonics?")
    assert not ap.should_promote(verdict)


def test_audit_flags_fake_booking(monkeypatch):
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "1")
    monkeypatch.setattr(ap.cheap_llm, "enabled", lambda: True)
    monkeypatch.setattr(ap.cheap_llm, "arbiter_model", lambda: "test-model")
    with patch(
        "services.action_policy.cheap_llm.complete_json",
        return_value={"commit": "booked", "confidence": 0.93, "reason": "says reserved"},
    ):
        verdict = ap.audit_draft(
            "Perfect, we have booked Terry for Thursday 12 Nov at 16:00."
        )
    assert ap.draft_is_fake_commit(verdict) == "booked"


def test_audit_ignores_offer(monkeypatch):
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "1")
    monkeypatch.setattr(ap.cheap_llm, "enabled", lambda: True)
    monkeypatch.setattr(ap.cheap_llm, "arbiter_model", lambda: "test-model")
    with patch(
        "services.action_policy.cheap_llm.complete_json",
        return_value={"commit": "none", "confidence": 0.88, "reason": "still asking"},
    ):
        verdict = ap.audit_draft("Shall I book Terry? Reply yes.")
    assert ap.draft_is_fake_commit(verdict) is None


def test_decide_intent_promotes_rag_miss(monkeypatch):
    monkeypatch.setenv("BRIGHTNEST_ACTION_POLICY", "1")

    def _book(message, history_preview=""):
        return {
            "called": True,
            "act": "book",
            "route": "booking_dialogue",
            "confidence": 0.9,
            "reason": "wants in",
        }

    monkeypatch.setattr("services.action_policy.classify_turn", _book)
    decision = decide_intent(
        "we ought to get my son started on your english sounds thing",
        lang="en",
        session_id="policy-promote",
    )
    assert decision.route == "booking_dialogue"
    assert (decision.debug.get("action_policy_override") or {}).get("to") == "booking_dialogue"
