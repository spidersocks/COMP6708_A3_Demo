"""Unit tests for demo polish helpers (mocked DashScope)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import demo_polish  # noqa: E402


def test_pick_cancel_candidate(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_cancel_pick", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    candidates = [
        {"slot_display": "Tue 03 Nov", "course_label": "Phonics", "teacher_name": "Avery"},
        {"slot_display": "Tue 10 Nov", "course_label": "Phonics", "teacher_name": "Avery"},
    ]
    with patch(
        "services.demo_polish.cheap_llm.complete_json",
        return_value={"action": "pick", "index": 2, "reason": "tue 10"},
    ):
        out = demo_polish.pick_cancel_candidate("Cancel Tue 10", candidates)
    assert out["action"] == "pick"
    assert out["index"] == 1


def test_parse_booking_hints(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_booking_parse", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    with patch(
        "services.demo_polish.cheap_llm.complete_json",
        return_value={
            "course_key": "phonics",
            "weekday": 1,
            "window": "next_week",
            "teacher_hint": None,
            "confidence": 0.9,
        },
    ):
        out = demo_polish.parse_booking_hints("phonics next Tuesday please")
    assert out["ok"] is True
    assert out["course_key"] == "phonics"
    assert out["weekday"] == 1


def test_track_booking_slots_identity(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_booking_parse", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    with patch(
        "services.demo_polish.cheap_llm.complete_json",
        return_value={
            "parent_name": "Jeff",
            "child_name": "Chester",
            "course_key": "math",
            "weekday": None,
            "window": None,
            "teacher_hint": None,
            "confidence": 0.95,
            "notes": "stated names",
        },
    ):
        out = demo_polish.track_booking_slots(
            "My name is Jeff. My kid's name is Chester. math lesson",
            missing=["parent_name", "child_name", "course_key"],
        )
    assert out["ok"] is True
    assert out["filled"]["parent_name"] == "Jeff"
    assert out["filled"]["child_name"] == "Chester"
    assert out["filled"]["course_key"] == "math"
    assert out["role"] == "booking_dst"


def test_track_booking_slots_rejects_kinship(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_booking_parse", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    with patch(
        "services.demo_polish.cheap_llm.complete_json",
        return_value={
            "parent_name": "my",
            "child_name": "kid",
            "course_key": None,
            "confidence": 0.9,
        },
    ):
        out = demo_polish.track_booking_slots("book for my kid")
    assert out["ok"] is False
    assert out.get("filled") in ({}, None) or not out.get("filled")


def test_track_cancel_slots(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_booking_parse", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    with patch(
        "services.demo_polish.cheap_llm.complete_json",
        return_value={
            "child_name": "mia",
            "course_key": "phonics",
            "weekday": 1,
            "confidence": 0.9,
        },
    ):
        out = demo_polish.track_cancel_slots(
            "cancel Mia's phonics Tuesday",
            known_children=["Mia Wong", "Jason Wong"],
            missing=["child_name"],
        )
    assert out["ok"] is True
    assert out["filled"]["child_name"] == "Mia Wong"
    assert out["role"] == "cancel_dst"


def test_polish_gap_reply(monkeypatch):
    monkeypatch.setattr(demo_polish.SETTINGS, "arbiter_gap_rewrite", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "intent_arbiter_enabled", True)
    monkeypatch.setattr(demo_polish.cheap_llm.SETTINGS, "dashscope_api_key", "x")
    with patch(
        "services.demo_polish.cheap_llm.complete_text",
        return_value="I don't have that on file yet — ask to speak with our team and we'll follow up.",
    ):
        out = demo_polish.polish_gap_reply(
            "scuba?",
            "I don't have a solid answer for that in our centre materials yet.",
            gap_reason="no_retrieval",
        )
    assert out["polished"] is True
    assert "solid answer" not in out["answer"].lower()
