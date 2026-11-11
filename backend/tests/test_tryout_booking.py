"""Try-out / placement must book live trial seats — never SheetPro-only promises."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import booking_dialogue as bd  # noqa: E402
from services.intent_router import decide_intent  # noqa: E402


def test_fresh_holiday_signup_is_not_ejected():
    """'holiday' inside a signup must not bounce a new turn to RAG."""
    sid = "yield-" + uuid.uuid4().hex[:8]
    assert bd.should_yield_to_rag(sid, "Sign us up for a holiday phonics brush-up") is False
    bd.save_state(
        sid,
        {
            "stage": "need_when_asked",
            "phone": "+85290001111",
            "parent_name": "A",
            "child_name": "B",
            "course_key": "phonics",
            "when": {},
            "child_schedule": [],
        },
    )
    assert bd.should_yield_to_rag(sid, "How much is the holiday intensive?") is True
    assert bd.should_yield_to_rag(sid, "Book the holiday class this week") is False


def test_tryout_is_booking_request():
    assert bd.is_booking_request("Hello I want to try out phonics for my child")
    assert bd.is_tryout_request("I'd love to try a phonics class")
    assert not bd.is_tryout_request("Do you offer a trial for my 5 year old?")
    assert not bd.is_booking_request("Do you offer a trial for my 5 year old?")


def test_tryout_routes_to_booking_dialogue():
    assert (
        decide_intent(
            "Hello I want to try out phonics for my child",
            lang="en",
            session_id="tryout-route",
        ).route
        == "booking_dialogue"
    )


def test_tryout_phonics_uses_trial_seats(monkeypatch):
    sid = "test-tryout-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(
        "services.demo_polish.track_booking_slots",
        lambda *a, **k: {"ok": False, "called": False, "filled": {}, "confidence": 0},
    )
    out = bd.handle_turn(
        session_id=sid,
        message="Hello I want to try out phonics for my child",
        phone="+85290007777",
        parent_name="",
        child_name_hint="",
    )
    assert (out.get("trace_booking") or {}).get("stage") == "need_identity"
    assert (out.get("state") or {}).get("course_key") == "trial"
    assert not out.get("admin_followup")

    out2 = bd.handle_turn(
        session_id=sid,
        message="I'm David Gunton. My child is Terry Gunton. Yes, phonics",
        phone="+85290007777",
        parent_name="",
        child_name_hint="",
    )
    st = out2.get("state") or {}
    tb = out2.get("trace_booking") or {}
    assert (st.get("parent_name") or "").lower().startswith("david")
    assert (st.get("child_name") or "").lower().startswith("terry")
    assert st.get("course_key") == "trial"
    assert tb.get("stage") in {"need_when", "offer_slots", "need_when_asked"}
    assert not out2.get("admin_followup")
    assert "reach out shortly" not in (out2.get("answer") or "").lower()


def test_confirm_assessment_pivots_to_trial_not_admin_todo(monkeypatch):
    sid = "test-assess-" + uuid.uuid4().hex[:8]
    monkeypatch.setattr(
        "services.demo_polish.track_booking_slots",
        lambda *a, **k: {"ok": False, "called": False, "filled": {}, "confidence": 0},
    )
    bd.save_state(
        sid,
        {
            "stage": "confirm_assessment",
            "phone": "+85290006666",
            "parent_name": "David Gunton",
            "child_name": "Terry Gunton",
            "course_key": "phonics",
            "programme_label": "Phonics",
            "hide_register": True,
            "when": {},
            "child_schedule": [],
        },
    )
    out = bd.handle_turn(
        session_id=sid,
        message="yes",
        phone="+85290006666",
        parent_name="David Gunton",
        child_name_hint="Terry Gunton",
    )
    assert not out.get("admin_followup")
    st = out.get("state") or {}
    assert st.get("course_key") == "trial"
    assert (out.get("trace_booking") or {}).get("stage") in {
        "need_when",
        "offer_slots",
        "need_when_asked",
    }
    low = (out.get("answer") or "").lower()
    assert "reach out shortly" not in low
    assert "team will" not in low
