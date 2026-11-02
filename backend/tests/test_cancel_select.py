"""Cancel clarify: date phrases must pick a session, not re-list."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.cancel_dialogue import (  # noqa: E402
    _detect_day_of_month,
    _detect_index,
    _filter_targets,
)


def test_tue_10_is_day_not_option_index():
    assert _detect_day_of_month("Cancel Tue 10") == 10
    assert _detect_index("Cancel Tue 10", 3) is None
    assert _detect_index("2", 3) == 1
    assert _detect_index("option 2", 3) == 1


def test_filter_targets_by_day_of_month():
    items = [
        {
            "slot_start": "2026-11-03T10:00:00+08:00",
            "slot_display": "Tue 03 Nov 2026, 10:00",
            "course_key": "phonics",
            "teacher_name": "Ms. Avery Chan",
        },
        {
            "slot_start": "2026-11-10T10:00:00+08:00",
            "slot_display": "Tue 10 Nov 2026, 10:00",
            "course_key": "phonics",
            "teacher_name": "Ms. Avery Chan",
        },
        {
            "slot_start": "2026-11-17T10:00:00+08:00",
            "slot_display": "Tue 17 Nov 2026, 10:00",
            "course_key": "phonics",
            "teacher_name": "Ms. Avery Chan",
        },
    ]
    out = _filter_targets(items, course_key=None, weekday=1, text="Cancel Tue 10")
    assert len(out) == 1
    assert "11-10" in out[0]["slot_start"]
