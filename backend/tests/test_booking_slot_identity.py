"""Same clock + different teachers must not collapse or swap on confirm."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import booking as booking_svc  # noqa: E402
from services import booking_dialogue as bd  # noqa: E402


def test_slot_identity_distinguishes_teachers():
    a = {
        "slot_start": "2026-11-20T17:00:00+08:00",
        "teacher_id": "tom",
        "session_id": "trial-tom",
    }
    b = {
        "slot_start": "2026-11-20T17:00:00+08:00",
        "teacher_id": "priya",
        "session_id": "trial-priya",
    }
    assert booking_svc.slot_identity(a) != booking_svc.slot_identity(b)


def test_week_schedule_scopes_chat_books_by_teacher(monkeypatch):
    """Priya Fri 17:00 book must not appear on Tom's Fri 17:00 trial card."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from services import schedule_sheets as sheets

    hk = ZoneInfo("Asia/Hong_Kong")
    fri = datetime(2026, 11, 27, 17, 0, tzinfo=hk).isoformat()
    calls = []

    def _scoped(course_key, slot_start_iso, teacher_id=None, session_id=None):
        calls.append(
            {
                "course_key": course_key,
                "slot_start": slot_start_iso,
                "teacher_id": teacher_id,
                "session_id": session_id,
            }
        )
        if (
            course_key == "trial"
            and slot_start_iso == fri
            and teacher_id == "priya"
            and session_id == "tr-fri-1700"
        ):
            return [
                {
                    "id": "e61e8918",
                    "child": "Steven Fontaine",
                    "parent": "Sean Fontaine",
                    "phone": "+85290009999",
                    "teacher_id": "priya",
                    "session_id": "tr-fri-1700",
                    "via": "chat",
                }
            ]
        return []

    monkeypatch.setattr(sheets, "list_chat_bookings", _scoped)

    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 27, 9, 0, tzinfo=tz or hk)

    monkeypatch.setattr(booking_svc, "datetime", _DT)

    week = booking_svc.get_week_schedule(days=1, lang="en")
    fri_sessions = week["days"][0]["sessions"]
    trials_1700 = [
        s
        for s in fri_sessions
        if s.get("course_key") == "trial" and str(s.get("slot_start") or "").startswith("2026-11-27T17:00")
    ]
    assert len(trials_1700) >= 2
    by_teacher = {s["teacher_id"]: s for s in trials_1700}
    assert "priya" in by_teacher and "tom" in by_teacher
    priya_kids = [e.get("child") for e in by_teacher["priya"].get("enrolled") or []]
    tom_kids = [e.get("child") for e in by_teacher["tom"].get("enrolled") or []]
    assert "Steven Fontaine" in priya_kids
    assert "Steven Fontaine" not in tom_kids
    # Calendar must ask list_chat_bookings with teacher+session, not bare timestamp
    scoped = [
        c
        for c in calls
        if c["course_key"] == "trial" and c["slot_start"] == fri
    ]
    assert scoped
    assert all(c.get("teacher_id") and c.get("session_id") for c in scoped)


def test_friday_1700_lists_both_teachers_not_duplicate(monkeypatch):
    sid = "test-fri-" + uuid.uuid4().hex[:8]
    phone = "+85290005555"

    def _noop_dst(message, prior=None, missing=None, history_preview=""):
        return {"ok": False, "called": False, "reason": "test", "how": ["test"]}

    monkeypatch.setattr("services.demo_polish.track_booking_slots", _noop_dst)

    # Seed new-parent booking state mid-offer
    fri = "2026-11-20T17:00:00+08:00"
    tom = {
        "slot_start": fri,
        "slot_display": "Fri 20 Nov 2026, 17:00",
        "teacher_id": "tom",
        "teacher_name": "Mr. Tom Reyes",
        "session_id": "trial-tom-fri",
        "room": "Room D",
        "seats_left": 1,
        "course_key": "trial",
    }
    priya = {
        "slot_start": fri,
        "slot_display": "Fri 20 Nov 2026, 17:00",
        "teacher_id": "priya",
        "teacher_name": "Ms. Priya Das",
        "session_id": "trial-priya-fri",
        "room": "Studio 1",
        "seats_left": 1,
        "course_key": "trial",
    }
    other = {
        "slot_start": "2026-11-20T14:00:00+08:00",
        "slot_display": "Fri 20 Nov 2026, 14:00",
        "teacher_id": "avery",
        "teacher_name": "Ms. Avery Chan",
        "session_id": "trial-avery-fri",
        "room": "Room A",
        "seats_left": 1,
        "course_key": "trial",
    }

    def _fake_avail(course_key, days_ahead=21, lang="en", teacher_id=None):
        slots = [tom, priya, other]
        if teacher_id:
            slots = [s for s in slots if s["teacher_id"] == teacher_id]
        return slots

    monkeypatch.setattr(booking_svc, "get_availability", _fake_avail)

    bd.save_state(
        sid,
        {
            "phone": phone,
            "parent_name": "DaMarcus",
            "child_name": "Jason",
            "course_key": "trial",
            "programme_label": "Little Linguist",
            "is_new_family": True,
            "hide_register": True,
            "stage": "offer_slots",
            "when": {"window": "this_week"},
            "offered_slot_keys": [
                booking_svc.slot_identity(tom),
                booking_svc.slot_identity(priya),
                booking_svc.slot_identity(other),
            ],
            "offered_slot_starts": [tom["slot_start"], priya["slot_start"], other["slot_start"]],
            "child_schedule": [],
        },
    )

    out = bd.handle_turn(
        session_id=sid,
        message="Friday 17:00",
        phone=phone,
        parent_name="DaMarcus",
        child_name_hint="Jason",
    )
    answer = out.get("answer") or ""
    slots = out.get("slots") or []
    teachers = [s.get("teacher_id") for s in slots]
    assert teachers.count("tom") <= 1
    assert "priya" in teachers
    assert "tom" in teachers
    assert answer.lower().count("tom reyes") <= 1
    assert "priya" in answer.lower()

    # Pick Priya explicitly → confirm Priya, then yes books Priya (not Tom)
    out2 = bd.handle_turn(
        session_id=sid,
        message="Friday 17:00 Priya",
        phone=phone,
        parent_name="DaMarcus",
        child_name_hint="Jason",
    )
    st2 = out2.get("state") or {}
    sel = st2.get("selected_slot") or (out2.get("trace_booking") or {}).get("selected") or {}
    assert sel.get("teacher_id") == "priya"

    booked_holder = {}

    def _fake_book(**kwargs):
        booked_holder.update(kwargs)
        return {
            "id": "testref",
            "slot_display": "Fri 20 Nov 2026, 17:00",
            "teacher_name": "Ms. Priya Das",
            "room": "Studio 1",
            "course_label": "Trial Assessment",
        }

    monkeypatch.setattr(booking_svc, "create_booking", _fake_book)
    out3 = bd.handle_turn(
        session_id=sid,
        message="yes",
        phone=phone,
        parent_name="DaMarcus",
        child_name_hint="Jason",
    )
    assert booked_holder.get("teacher_id") == "priya"
    assert "priya" in (out3.get("answer") or "").lower()
    assert "tom reyes" not in (out3.get("answer") or "").lower()


def test_jason_does_not_inherit_jason_chan_register():
    # Exact-name: bare "Jason" must not see Jason Chan standing sessions
    sessions = bd.child_upcoming_sessions("Jason", phone="+85290004444")
    # Jason Chan's register classes should not appear for bare Jason
    for s in sessions:
        if s.get("kind") == "register":
            raise AssertionError(f"bare Jason inherited register seat: {s}")
