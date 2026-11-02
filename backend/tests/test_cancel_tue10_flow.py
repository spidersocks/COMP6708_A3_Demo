from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import cancel_dialogue as cd  # noqa: E402

UPCOMING = [
    {
        "slot_start": "2026-12-01T10:00:00+08:00",
        "slot_display": "Tue 01 Dec 2026, 10:00",
        "course_key": "phonics",
        "course_label": "Phonics",
        "teacher_name": "Ms. Avery Chan",
        "teacher_id": "avery",
        "kind": "enrolment",
        "session_id": "s1",
        "booking_id": "",
    },
    {
        "slot_start": "2026-12-08T10:00:00+08:00",
        "slot_display": "Tue 08 Dec 2026, 10:00",
        "course_key": "phonics",
        "course_label": "Phonics",
        "teacher_name": "Ms. Avery Chan",
        "teacher_id": "avery",
        "kind": "enrolment",
        "session_id": "s2",
        "booking_id": "",
    },
    {
        "slot_start": "2026-12-15T10:00:00+08:00",
        "slot_display": "Tue 15 Dec 2026, 10:00",
        "course_key": "phonics",
        "course_label": "Phonics",
        "teacher_name": "Ms. Avery Chan",
        "teacher_id": "avery",
        "kind": "enrolment",
        "session_id": "s3",
        "booking_id": "",
    },
]


def test_cancel_tue_10_goes_to_confirm():
    sid = "pytest-cancel-tue10"
    cd.clear_state(sid)
    with patch(
        "services.cancel_dialogue.book_dlg.child_upcoming_sessions",
        return_value=UPCOMING,
    ):
        r1 = cd.handle_turn(
            session_id=sid,
            message="Can I cancel Jason's class next Tuesday?",
            phone="+852 9000 0000",
            parent_name="Demo Parent",
            child_name_hint="Jason Chan",
        )
        assert r1["trace_cancel"]["stage"] == "need_which"
        assert "Tue 08" in r1["answer"]

        r2 = cd.handle_turn(
            session_id=sid,
            message="Cancel Tue 08",
            phone="+852 9000 0000",
            parent_name="Demo Parent",
            child_name_hint="Jason Chan",
        )
        assert r2["trace_cancel"]["stage"] == "confirm", r2["answer"]
        assert "08 Dec" in r2["answer"] or "Tue 08" in r2["answer"]
        assert "Which session?" not in r2["answer"]
