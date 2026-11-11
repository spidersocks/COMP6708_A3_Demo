"""Booking must never treat pronouns like 'my' as a child name."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import booking_dialogue as bd  # noqa: E402
from services import families  # noqa: E402


def test_extract_rejects_my_child():
    assert families.extract_child_mention("I want to book a class for my child", []) == ""
    assert families.extract_child_mention("book for our kid please", []) == ""
    assert families.extract_child_mention("for Jason next week", []) == "Jason"
    assert not families._is_plausible_person_name("my")  # noqa: SLF001
    assert families._is_plausible_person_name("Mia")  # noqa: SLF001


def test_new_parent_asks_for_real_names_not_my():
    sid = "test-my-" + uuid.uuid4().hex[:8]
    out = bd.handle_turn(
        session_id=sid,
        message="Great! I want to book a class for my child",
        phone="+85290009999",
        parent_name="",
        child_name_hint="",
    )
    answer = (out.get("answer") or "").lower()
    assert "booking for **my**" not in answer
    assert "booking for my" not in answer
    assert (out.get("trace_booking") or {}).get("stage") == "need_identity"
    assert "child" in answer and "name" in answer
    assert not ((out.get("state") or {}).get("child_name") or "").strip()


def test_parse_identity_kid_possessive():
    got = bd._parse_identity(
        "My name is Jeff. My kid's name is Chester. I want to book a math lesson"
    )
    assert got.get("parent") == "Jeff"
    assert got.get("child") == "Chester"


def test_new_parent_identity_from_jeff_chester(monkeypatch):
    """Regex + DST merge must accept Jeff/Chester in one turn (no identity loop)."""
    sid = "test-jeff-" + uuid.uuid4().hex[:8]

    def _fake_dst(message, prior=None, missing=None):
        return {
            "ok": True,
            "called": True,
            "filled": {
                "parent_name": "Jeff",
                "child_name": "Chester",
                "course_key": "math",
            },
            "confidence": 0.92,
            "parent_name": "Jeff",
            "child_name": "Chester",
            "course_key": "math",
            "model": "test",
            "how": ["test"],
        }

    monkeypatch.setattr(
        "services.demo_polish.track_booking_slots",
        _fake_dst,
    )
    # Even if DST is skipped, regex alone should work; exercise full path
    out = bd.handle_turn(
        session_id=sid,
        message="My name is Jeff. My kid's name is Chester. I want to book a math lesson",
        phone="+85290008888",
        parent_name="",
        child_name_hint="",
    )
    st = out.get("state") or {}
    tb = out.get("trace_booking") or {}
    assert (st.get("parent_name") or "").lower() == "jeff"
    assert (st.get("child_name") or "").lower() == "chester"
    assert st.get("course_key") == "math"
    assert tb.get("stage") != "need_identity"
    assert tb.get("stage") != "need_parent"
    assert tb.get("stage") != "need_child"
