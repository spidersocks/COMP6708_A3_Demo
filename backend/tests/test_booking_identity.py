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
