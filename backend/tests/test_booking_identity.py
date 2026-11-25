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


def test_new_parent_ui_hint_does_not_stick_david():
    """Demo UI may send a prior resolved child_name; blank WhatsApp must ignore it."""
    sid = "test-hint-david-" + uuid.uuid4().hex[:8]
    out = bd.handle_turn(
        session_id=sid,
        message="I'd like to book a phonics class",
        phone="+85290009999",
        parent_name="",
        child_name_hint="David",
    )
    st = out.get("state") or {}
    answer = (out.get("answer") or "").lower()
    assert (st.get("child_name") or "").strip() == ""
    assert "david" not in answer
    assert (out.get("trace_booking") or {}).get("stage") in {
        "need_identity",
        "need_parent",
        "need_child",
        "need_course",
        "need_when",
    }


def test_demo_blank_parent_never_matches_stale_roster_row():
    """A Sean/David row left on +852 9000 9999 must not make the number 'known'."""
    # Poison the sheet the way chat booking used to
    families.ensure_family_on_sheet(
        phone="+852 9000 9999",
        parent_name="Sean",
        child_name="David",
        notes="should be ignored",
    )
    # ensure_family must refuse the sentinel number
    assert families.is_demo_blank_parent("+852 9000 9999")
    fam = families.list_family_by_phone("+852 9000 9999")
    assert fam.get("matched") is False
    assert not (fam.get("parent_name") or "").strip()
    assert not (fam.get("children") or [])
    # Cleanup helper removes any leaked rows
    families.clear_demo_blank_parent_families()
    rows = [
        r
        for r in families._read_families()  # noqa: SLF001
        if families.is_demo_blank_parent(r.get("phone") or "")
    ]
    assert rows == []


def test_parse_identity_kid_possessive():
    got = bd._parse_identity(
        "My name is Jeff. My kid's name is Chester. I want to book a math lesson"
    )
    assert got.get("parent") == "Jeff"
    assert got.get("child") == "Chester"


def test_son_started_is_not_a_child_name():
    got = bd._parse_identity("I'd like to get my son started on phonics")
    assert "child" not in got
    assert not families._is_plausible_person_name("Started On")  # noqa: SLF001
    assert families._is_plausible_person_name("Terry Gunton")  # noqa: SLF001


def test_parse_identity_named_david():
    got = bd._parse_identity(
        "I'm General Humpty and my child is named David. I want to do phonics"
    )
    assert got.get("parent") == "General Humpty"
    assert got.get("child") == "David"


def test_new_parent_identity_from_jeff_chester(monkeypatch):
    """Regex + DST merge must accept Jeff/Chester in one turn (no identity loop)."""
    sid = "test-jeff-" + uuid.uuid4().hex[:8]

    def _fake_dst(message, prior=None, missing=None, history_preview=""):
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


def test_programme_title_never_becomes_child_name():
    assert families.extract_child_mention(
        "I think he's ready for Little Sound Explorer. I would love to arrange a placement",
        [],
    ) == ""
    assert not families._is_plausible_person_name("Little Sound")  # noqa: SLF001
    assert not families._is_plausible_person_name("Little Sound Explorer")  # noqa: SLF001
    assert families.looks_like_programme_label("Sound Explorer")
    assert families._is_plausible_person_name("Marcus")  # noqa: SLF001


def test_parse_his_name_is_marcus_not_programme():
    got = bd._parse_identity(
        "His name is not Little Sound. That's the program I want him to join. "
        "His name is Marcus. My name is James"
    )
    assert got.get("parent") == "James"
    assert got.get("child") == "Marcus"


def test_ready_for_little_sound_does_not_lock_child(monkeypatch):
    """Programme mention must set programme_label, never child_name."""
    sid = "test-lse-" + uuid.uuid4().hex[:8]

    def _fake_dst(message, prior=None, missing=None, history_preview=""):
        # Simulate a naive LLM that copies the programme into child_name — hard gate must drop it
        return {
            "ok": True,
            "called": True,
            "filled": {"child_name": "Little Sound", "course_key": "phonics"},
            "confidence": 0.95,
            "model": "test",
            "how": ["test"],
        }

    monkeypatch.setattr("services.demo_polish.track_booking_slots", _fake_dst)
    out = bd.handle_turn(
        session_id=sid,
        message=(
            "I think he's ready for Little Sound Explorer. That's a great suggestion. "
            "I would love to arrange a placement assessment."
        ),
        phone="+85290007777",
        parent_name="",
        child_name_hint="",
    )
    st = out.get("state") or {}
    answer = out.get("answer") or ""
    assert "little sound" not in (st.get("child_name") or "").lower()
    assert "little sound" in (st.get("programme_label") or "").lower()
    assert "booking for **little sound**" not in answer.lower()


def test_marcus_james_corrects_contaminated_child(monkeypatch):
    """If a prior turn poisoned child_name, explicit His/My name must override."""
    sid = "test-marcus-" + uuid.uuid4().hex[:8]

    def _noop_dst(message, prior=None, missing=None, history_preview=""):
        return {"ok": False, "called": False, "reason": "test_skip", "how": ["test"]}

    monkeypatch.setattr("services.demo_polish.track_booking_slots", _noop_dst)

    # Turn 1: programme interest (must not set child)
    bd.handle_turn(
        session_id=sid,
        message="I think he's ready for Little Sound Explorer. Arrange a placement assessment.",
        phone="+85290006666",
        parent_name="",
        child_name_hint="",
    )
    # Inject contamination the way older builds did
    st0 = bd.get_state(sid) or {}
    st0["child_name"] = "Little Sound"
    st0["programme_label"] = "Little Sound Explorer"
    st0["course_key"] = "trial"
    bd.save_state(sid, st0)

    out = bd.handle_turn(
        session_id=sid,
        message=(
            "His name is not Little Sound. That's the program I want him to join. "
            "His name is Marcus. My name is James"
        ),
        phone="+85290006666",
        parent_name="",
        child_name_hint="",
    )
    st = out.get("state") or {}
    answer = (out.get("answer") or "").lower()
    assert (st.get("child_name") or "").lower() == "marcus"
    assert (st.get("parent_name") or "").lower() == "james"
    assert "for **little sound**" not in answer
    assert "little sound**" not in answer or "explorer" in answer  # programme ok in copy
