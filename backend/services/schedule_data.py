"""Compatibility façade — register now lives in CSV admin sheets."""
from __future__ import annotations

from services import schedule_sheets as sheets

COURSE_LABELS = sheets.COURSE_LABELS

_SEED_VERSION = "2026-10-28-v3"


def _ensure_seed() -> None:
    root = sheets.sheets_dir()
    ver = root / ".seed_version"
    if ver.exists() and ver.read_text(encoding="utf-8").strip() == _SEED_VERSION:
        sheets.seed_sheets_if_needed(force=False)
        return
    sheets.seed_sheets_if_needed(force=True)
    ver.write_text(_SEED_VERSION, encoding="utf-8")


_ensure_seed()


def __getattr__(name: str):
    reg = sheets.get_register()
    mapping = {
        "TEACHERS": "teachers",
        "STANDING_SESSIONS": "standing_sessions",
        "TRIAL_SLOTS": "trial_slots",
        "TEACHER_BY_ID": "teacher_by_id",
    }
    if name in mapping:
        return reg[mapping[name]]
    raise AttributeError(name)


def course_label(course_key: str, lang: str = "en") -> str:
    return sheets.course_label(course_key, lang)


def teacher_name(teacher_id: str) -> str:
    return sheets.teacher_name(teacher_id)
