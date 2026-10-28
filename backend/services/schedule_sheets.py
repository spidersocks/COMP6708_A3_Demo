"""Local CSV 'admin sheets' — demo stand-in for Google Sheets / Excel.

Files under backend/data/sheets/:
  teachers.csv, windows.csv, sessions.csv, enrolments.csv,
  trial_slots.csv, chat_bookings.csv

Chat bookings append to chat_bookings.csv so the demo can show the bot
writing back into the same sheet pack an admin would open.
"""
from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import SETTINGS

_LOCK = threading.Lock()

SHEET_NAMES = (
    "teachers.csv",
    "windows.csv",
    "sessions.csv",
    "enrolments.csv",
    "trial_slots.csv",
    "families.csv",
    "chat_bookings.csv",
)

COURSE_LABELS = {
    "phonics": {"en": "Phonics", "zh-HK": "自然拼音", "zh-CN": "自然拼音"},
    "mandarin": {"en": "Mandarin", "zh-HK": "普通話", "zh-CN": "普通话"},
    "writing": {"en": "Creative Writing", "zh-HK": "創意寫作", "zh-CN": "创意写作"},
    "playgroup": {"en": "Playgroup", "zh-HK": "幼兒遊戲班", "zh-CN": "幼儿游戏班"},
    "trial": {"en": "Trial Assessment", "zh-HK": "試堂評估", "zh-CN": "试听评估"},
    "math": {"en": "Early Math", "zh-HK": "早期數學", "zh-CN": "早期数学"},
}


def sheets_dir() -> Path:
    root = Path(SETTINGS.schedule_sheets_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _write_csv(path: Path, fieldnames: List[str], rows: List[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def _read_csv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def seed_sheets_if_needed(*, force: bool = False) -> Path:
    """Create a fuller synthetic register the first time (or when force=True)."""
    root = sheets_dir()
    marker = root / "teachers.csv"
    if marker.exists() and not force:
        return root

    teachers = [
        {"id": "avery", "name": "Ms. Avery Chan", "role": "Lead Phonics", "courses": "phonics|trial|writing", "room_default": "Room A"},
        {"id": "wei", "name": "Mr. Wei Lin", "role": "Mandarin lead", "courses": "mandarin|trial", "room_default": "Room B"},
        {"id": "priya", "name": "Ms. Priya Das", "role": "Writing studio lead", "courses": "writing|phonics|trial", "room_default": "Studio 1"},
        {"id": "maya", "name": "Ms. Maya Cheung", "role": "Playgroup facilitator", "courses": "playgroup|trial", "room_default": "Play Hall"},
        {"id": "jordan", "name": "Mr. Jordan Blake", "role": "Phonics & conversation", "courses": "phonics|trial|writing", "room_default": "Room C"},
        {"id": "hana", "name": "Ms. Hana Zhou", "role": "Junior Mandarin", "courses": "mandarin|trial|playgroup", "room_default": "Room B"},
        {"id": "tom", "name": "Mr. Tom Reyes", "role": "Early Math lead", "courses": "math|phonics|trial", "room_default": "Room D"},
        {"id": "sofia", "name": "Ms. Sofia Almeida", "role": "Playgroup & settling", "courses": "playgroup|trial", "room_default": "Play Hall"},
    ]

    windows = [
        # Avery
        {"teacher_id": "avery", "weekday": 1, "start": "09:30", "end": "12:30"},
        {"teacher_id": "avery", "weekday": 1, "start": "14:30", "end": "17:30"},
        {"teacher_id": "avery", "weekday": 3, "start": "09:30", "end": "12:30"},
        {"teacher_id": "avery", "weekday": 4, "start": "13:30", "end": "16:00"},
        {"teacher_id": "avery", "weekday": 5, "start": "09:30", "end": "16:30"},
        # Wei
        {"teacher_id": "wei", "weekday": 0, "start": "10:00", "end": "13:00"},
        {"teacher_id": "wei", "weekday": 2, "start": "14:00", "end": "18:00"},
        {"teacher_id": "wei", "weekday": 5, "start": "10:00", "end": "14:30"},
        # Priya
        {"teacher_id": "priya", "weekday": 2, "start": "09:30", "end": "12:30"},
        {"teacher_id": "priya", "weekday": 4, "start": "14:00", "end": "18:00"},
        {"teacher_id": "priya", "weekday": 5, "start": "12:00", "end": "16:00"},
        # Maya
        {"teacher_id": "maya", "weekday": 0, "start": "09:00", "end": "12:00"},
        {"teacher_id": "maya", "weekday": 3, "start": "09:00", "end": "12:00"},
        {"teacher_id": "maya", "weekday": 5, "start": "09:00", "end": "12:30"},
        # Jordan
        {"teacher_id": "jordan", "weekday": 0, "start": "14:00", "end": "18:00"},
        {"teacher_id": "jordan", "weekday": 2, "start": "14:00", "end": "18:00"},
        {"teacher_id": "jordan", "weekday": 4, "start": "09:30", "end": "12:30"},
        {"teacher_id": "jordan", "weekday": 5, "start": "09:30", "end": "13:00"},
        # Hana
        {"teacher_id": "hana", "weekday": 1, "start": "10:00", "end": "13:00"},
        {"teacher_id": "hana", "weekday": 3, "start": "14:00", "end": "18:00"},
        {"teacher_id": "hana", "weekday": 5, "start": "10:00", "end": "14:00"},
        # Tom
        {"teacher_id": "tom", "weekday": 0, "start": "15:00", "end": "18:00"},
        {"teacher_id": "tom", "weekday": 2, "start": "10:00", "end": "13:00"},
        {"teacher_id": "tom", "weekday": 4, "start": "15:00", "end": "18:00"},
        {"teacher_id": "tom", "weekday": 5, "start": "10:00", "end": "13:00"},
        # Sofia
        {"teacher_id": "sofia", "weekday": 1, "start": "09:00", "end": "12:00"},
        {"teacher_id": "sofia", "weekday": 4, "start": "09:00", "end": "12:00"},
        {"teacher_id": "sofia", "weekday": 5, "start": "09:00", "end": "12:00"},
    ]

    sessions = [
        # Phonics — Avery / Jordan / Priya / Tom
        {"id": "ph-tue-1000", "teacher_id": "avery", "course_key": "phonics", "weekday": 1, "start": "10:00", "duration_min": 60, "capacity": 6, "room": "Room A"},
        {"id": "ph-tue-1500", "teacher_id": "avery", "course_key": "phonics", "weekday": 1, "start": "15:00", "duration_min": 60, "capacity": 6, "room": "Room A"},
        {"id": "ph-thu-1000", "teacher_id": "avery", "course_key": "phonics", "weekday": 3, "start": "10:00", "duration_min": 60, "capacity": 6, "room": "Room A"},
        {"id": "ph-sat-1030", "teacher_id": "avery", "course_key": "phonics", "weekday": 5, "start": "10:30", "duration_min": 60, "capacity": 6, "room": "Room A"},
        {"id": "ph-sat-1400", "teacher_id": "priya", "course_key": "phonics", "weekday": 5, "start": "14:00", "duration_min": 60, "capacity": 6, "room": "Studio 1"},
        {"id": "ph-mon-1600", "teacher_id": "jordan", "course_key": "phonics", "weekday": 0, "start": "16:00", "duration_min": 60, "capacity": 6, "room": "Room C"},
        {"id": "ph-wed-1600", "teacher_id": "jordan", "course_key": "phonics", "weekday": 2, "start": "16:00", "duration_min": 60, "capacity": 6, "room": "Room C"},
        {"id": "ph-fri-1000", "teacher_id": "jordan", "course_key": "phonics", "weekday": 4, "start": "10:00", "duration_min": 60, "capacity": 6, "room": "Room C"},
        {"id": "ph-sat-1100j", "teacher_id": "jordan", "course_key": "phonics", "weekday": 5, "start": "11:00", "duration_min": 60, "capacity": 6, "room": "Room C"},
        {"id": "ph-wed-1100t", "teacher_id": "tom", "course_key": "phonics", "weekday": 2, "start": "11:00", "duration_min": 60, "capacity": 6, "room": "Room D"},
        # Mandarin — Wei / Hana
        {"id": "md-mon-1100", "teacher_id": "wei", "course_key": "mandarin", "weekday": 0, "start": "11:00", "duration_min": 60, "capacity": 8, "room": "Room B"},
        {"id": "md-wed-1600", "teacher_id": "wei", "course_key": "mandarin", "weekday": 2, "start": "16:00", "duration_min": 60, "capacity": 8, "room": "Room B"},
        {"id": "md-sat-1130", "teacher_id": "wei", "course_key": "mandarin", "weekday": 5, "start": "11:30", "duration_min": 60, "capacity": 8, "room": "Room B"},
        {"id": "md-tue-1100", "teacher_id": "hana", "course_key": "mandarin", "weekday": 1, "start": "11:00", "duration_min": 60, "capacity": 8, "room": "Room B"},
        {"id": "md-thu-1600", "teacher_id": "hana", "course_key": "mandarin", "weekday": 3, "start": "16:00", "duration_min": 60, "capacity": 8, "room": "Room B"},
        {"id": "md-sat-1030h", "teacher_id": "hana", "course_key": "mandarin", "weekday": 5, "start": "10:30", "duration_min": 60, "capacity": 8, "room": "Room B"},
        # Writing — Priya / Jordan
        {"id": "wr-wed-1000", "teacher_id": "priya", "course_key": "writing", "weekday": 2, "start": "10:00", "duration_min": 75, "capacity": 6, "room": "Studio 1"},
        {"id": "wr-fri-1530", "teacher_id": "priya", "course_key": "writing", "weekday": 4, "start": "15:30", "duration_min": 75, "capacity": 6, "room": "Studio 1"},
        {"id": "wr-sat-1300", "teacher_id": "priya", "course_key": "writing", "weekday": 5, "start": "13:00", "duration_min": 75, "capacity": 6, "room": "Studio 1"},
        {"id": "wr-mon-1430", "teacher_id": "jordan", "course_key": "writing", "weekday": 0, "start": "14:30", "duration_min": 75, "capacity": 6, "room": "Room C"},
        # Playgroup — Maya / Sofia / Hana
        {"id": "pg-mon-0930", "teacher_id": "maya", "course_key": "playgroup", "weekday": 0, "start": "09:30", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        {"id": "pg-thu-0930", "teacher_id": "maya", "course_key": "playgroup", "weekday": 3, "start": "09:30", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        {"id": "pg-sat-0930", "teacher_id": "maya", "course_key": "playgroup", "weekday": 5, "start": "09:30", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        {"id": "pg-tue-0930", "teacher_id": "sofia", "course_key": "playgroup", "weekday": 1, "start": "09:30", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        {"id": "pg-fri-0930", "teacher_id": "sofia", "course_key": "playgroup", "weekday": 4, "start": "09:30", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        {"id": "pg-sat-1100s", "teacher_id": "sofia", "course_key": "playgroup", "weekday": 5, "start": "11:00", "duration_min": 45, "capacity": 8, "room": "Play Hall"},
        # Early math — Tom
        {"id": "ma-mon-1530", "teacher_id": "tom", "course_key": "math", "weekday": 0, "start": "15:30", "duration_min": 60, "capacity": 6, "room": "Room D"},
        {"id": "ma-wed-1000", "teacher_id": "tom", "course_key": "math", "weekday": 2, "start": "10:00", "duration_min": 60, "capacity": 6, "room": "Room D"},
        {"id": "ma-fri-1530", "teacher_id": "tom", "course_key": "math", "weekday": 4, "start": "15:30", "duration_min": 60, "capacity": 6, "room": "Room D"},
        {"id": "ma-sat-1030", "teacher_id": "tom", "course_key": "math", "weekday": 5, "start": "10:30", "duration_min": 60, "capacity": 6, "room": "Room D"},
    ]

    # Varied roster fill so some classes look nearly full
    enrol_seed = {
        "ph-tue-1000": [("Emma Wong", "Mrs Wong"), ("Leo Chan", "Mr Chan"), ("Jason Chan", "Demo Parent"), ("Sophie Ng", "Ms Ng"), ("Ryan Ho", "Mrs Ho")],
        "ph-tue-1500": [("Mia Lau", "Mrs Lau"), ("Noah Ip", "Mr Ip"), ("Chloe Yip", "Ms Yip")],
        "ph-thu-1000": [("Emma Wong", "Mrs Wong"), ("Leo Chan", "Mr Chan"), ("Jason Chan", "Demo Parent"), ("Sophie Ng", "Ms Ng"), ("Ryan Ho", "Mrs Ho"), ("Aiden Tam", "Mrs Tam")],
        "ph-sat-1030": [("Grace Fung", "Mrs Fung"), ("Ethan Kwok", "Mr Kwok")],
        "ph-sat-1400": [("Isla Mak", "Ms Mak"), ("Owen Tsui", "Mr Tsui"), ("Hannah Leung", "Mrs Leung"), ("Jacob Poon", "Mrs Poon"), ("Lily Siu", "Ms Siu")],
        "ph-mon-1600": [("Ken Lo", "Mr Lo"), ("Bella Chu", "Ms Chu"), ("Aaron Tse", "Mrs Tse")],
        "ph-wed-1600": [("Ken Lo", "Mr Lo"), ("Bella Chu", "Ms Chu")],
        "ph-fri-1000": [("Ivy So", "Mrs So"), ("Max Lam", "Mr Lam"), ("Nina Wu", "Ms Wu"), ("Jay Fan", "Mrs Fan")],
        "ph-sat-1100j": [("Ivy So", "Mrs So"), ("Max Lam", "Mr Lam")],
        "ph-wed-1100t": [("Cara Ng", "Ms Ng"), ("Ben Yiu", "Mr Yiu")],
        "md-mon-1100": [("Katie Cheng", "Mrs Cheng"), ("Brian Lam", "Mr Lam"), ("Zoe Hui", "Ms Hui"), ("Daniel Au", "Mrs Au"), ("Eva Chow", "Mrs Chow"), ("Marcus Tang", "Mr Tang")],
        "md-wed-1600": [("Katie Cheng", "Mrs Cheng"), ("Brian Lam", "Mr Lam"), ("Zoe Hui", "Ms Hui"), ("Daniel Au", "Mrs Au")],
        "md-sat-1130": [("Amy Ko", "Mrs Ko"), ("Chris Mok", "Mr Mok"), ("Nina Yeung", "Ms Yeung")],
        "md-tue-1100": [("Rita Fan", "Mrs Fan"), ("Paul Gin", "Mr Gin"), ("Sue Man", "Ms Man")],
        "md-thu-1600": [("Rita Fan", "Mrs Fan"), ("Paul Gin", "Mr Gin"), ("Sue Man", "Ms Man"), ("Ian Or", "Mrs Or"), ("Joy Yim", "Ms Yim")],
        "md-sat-1030h": [("Amy Ko", "Mrs Ko"), ("Chris Mok", "Mr Mok")],
        "wr-wed-1000": [("Olivia Fong", "Mrs Fong"), ("Lucas Kwan", "Mr Kwan"), ("Ella Sit", "Ms Sit"), ("Nathan Yu", "Mrs Yu")],
        "wr-fri-1530": [("Olivia Fong", "Mrs Fong"), ("Lucas Kwan", "Mr Kwan"), ("Ella Sit", "Ms Sit")],
        "wr-sat-1300": [("Nathan Yu", "Mrs Yu"), ("Sara Pang", "Ms Pang")],
        "wr-mon-1430": [("Theo Pak", "Mr Pak"), ("Mila On", "Ms On"), ("Hugo Yan", "Mrs Yan")],
        "pg-mon-0930": [("Baby A", "Mrs A"), ("Baby B", "Mr B"), ("Baby C", "Ms C"), ("Baby D", "Mrs D"), ("Baby E", "Mrs E")],
        "pg-thu-0930": [("Baby A", "Mrs A"), ("Baby B", "Mr B"), ("Baby C", "Ms C"), ("Baby F", "Mrs F"), ("Baby G", "Ms G"), ("Baby H", "Mr H"), ("Baby I", "Mrs I")],
        "pg-sat-0930": [("Baby D", "Mrs D"), ("Baby E", "Mrs E"), ("Baby J", "Ms J")],
        "pg-tue-0930": [("Baby K", "Mrs K"), ("Baby L", "Mr L"), ("Baby M", "Ms M"), ("Baby N", "Mrs N")],
        "pg-fri-0930": [("Baby K", "Mrs K"), ("Baby L", "Mr L"), ("Baby O", "Ms O")],
        "pg-sat-1100s": [("Baby P", "Mrs P"), ("Baby Q", "Mr Q")],
        "ma-mon-1530": [("Sam U", "Mrs U"), ("Tess V", "Mr V"), ("Will X", "Ms X")],
        "ma-wed-1000": [("Sam U", "Mrs U"), ("Tess V", "Mr V"), ("Will X", "Ms X"), ("Yuki Z", "Mrs Z"), ("Jason Chan", "Demo Parent")],
        "ma-fri-1530": [("Sam U", "Mrs U"), ("Yuki Z", "Mrs Z")],
        "ma-sat-1030": [("Tess V", "Mr V"), ("Will X", "Ms X"), ("Cora Qi", "Ms Qi"), ("Dean Xu", "Mr Xu")],
    }
    enrolments = []
    for sid, kids in enrol_seed.items():
        for child, parent in kids:
            enrolments.append({"session_id": sid, "child": child, "parent": parent})

    families = [
        {
            "phone": "+852 9000 0000",
            "parent_name": "Demo Parent",
            "child_name": "Jason Chan",
            "preferred_courses": "phonics|math",
            "notes": "Primary demo WhatsApp family",
        },
        {
            "phone": "+852 9000 0000",
            "parent_name": "Demo Parent",
            "child_name": "Mia Chan",
            "preferred_courses": "playgroup",
            "notes": "Sibling on same number",
        },
        {
            "phone": "+852 9123 4567",
            "parent_name": "Mrs Wong",
            "child_name": "Emma Wong",
            "preferred_courses": "phonics",
            "notes": "",
        },
        {
            "phone": "+852 9555 1212",
            "parent_name": "Mrs Cheng",
            "child_name": "Katie Cheng",
            "preferred_courses": "mandarin",
            "notes": "",
        },
    ]

    trial_slots = [
        {"id": "tr-mon-1200", "teacher_id": "wei", "weekday": 0, "start": "12:00", "duration_min": 45, "capacity": 1, "room": "Room B"},
        {"id": "tr-mon-1700", "teacher_id": "jordan", "weekday": 0, "start": "17:00", "duration_min": 45, "capacity": 1, "room": "Room C"},
        {"id": "tr-mon-1630t", "teacher_id": "tom", "weekday": 0, "start": "16:30", "duration_min": 45, "capacity": 1, "room": "Room D"},
        {"id": "tr-tue-1630", "teacher_id": "avery", "weekday": 1, "start": "16:30", "duration_min": 45, "capacity": 1, "room": "Room A"},
        {"id": "tr-tue-1130h", "teacher_id": "hana", "weekday": 1, "start": "12:00", "duration_min": 45, "capacity": 1, "room": "Room B"},
        {"id": "tr-tue-1100s", "teacher_id": "sofia", "weekday": 1, "start": "11:00", "duration_min": 45, "capacity": 1, "room": "Play Hall"},
        {"id": "tr-wed-1400", "teacher_id": "wei", "weekday": 2, "start": "14:00", "duration_min": 45, "capacity": 1, "room": "Room B"},
        {"id": "tr-wed-1115", "teacher_id": "priya", "weekday": 2, "start": "11:15", "duration_min": 45, "capacity": 1, "room": "Studio 1"},
        {"id": "tr-wed-1700j", "teacher_id": "jordan", "weekday": 2, "start": "17:00", "duration_min": 45, "capacity": 1, "room": "Room C"},
        {"id": "tr-wed-1200t", "teacher_id": "tom", "weekday": 2, "start": "12:00", "duration_min": 45, "capacity": 1, "room": "Room D"},
        {"id": "tr-thu-1700h", "teacher_id": "hana", "weekday": 3, "start": "17:00", "duration_min": 45, "capacity": 1, "room": "Room B"},
        {"id": "tr-fri-1400", "teacher_id": "avery", "weekday": 4, "start": "14:00", "duration_min": 45, "capacity": 1, "room": "Room A"},
        {"id": "tr-fri-1700", "teacher_id": "priya", "weekday": 4, "start": "17:00", "duration_min": 45, "capacity": 1, "room": "Studio 1"},
        {"id": "tr-fri-1100s", "teacher_id": "sofia", "weekday": 4, "start": "11:00", "duration_min": 45, "capacity": 1, "room": "Play Hall"},
        {"id": "tr-fri-1700t", "teacher_id": "tom", "weekday": 4, "start": "17:00", "duration_min": 45, "capacity": 1, "room": "Room D"},
        {"id": "tr-sat-1100", "teacher_id": "maya", "weekday": 5, "start": "11:00", "duration_min": 45, "capacity": 1, "room": "Play Hall"},
        {"id": "tr-sat-1315", "teacher_id": "wei", "weekday": 5, "start": "13:15", "duration_min": 45, "capacity": 1, "room": "Room B"},
        {"id": "tr-sat-1230j", "teacher_id": "jordan", "weekday": 5, "start": "12:15", "duration_min": 45, "capacity": 1, "room": "Room C"},
        {"id": "tr-sat-1200t", "teacher_id": "tom", "weekday": 5, "start": "12:00", "duration_min": 45, "capacity": 1, "room": "Room D"},
        {"id": "tr-sat-1030h", "teacher_id": "hana", "weekday": 5, "start": "13:00", "duration_min": 45, "capacity": 1, "room": "Room B"},
    ]

    with _LOCK:
        _write_csv(root / "teachers.csv", ["id", "name", "role", "courses", "room_default"], teachers)
        _write_csv(root / "windows.csv", ["teacher_id", "weekday", "start", "end"], windows)
        _write_csv(
            root / "sessions.csv",
            ["id", "teacher_id", "course_key", "weekday", "start", "duration_min", "capacity", "room"],
            sessions,
        )
        _write_csv(root / "enrolments.csv", ["session_id", "child", "parent"], enrolments)
        _write_csv(
            root / "families.csv",
            ["phone", "parent_name", "child_name", "preferred_courses", "notes"],
            families,
        )
        _write_csv(
            root / "trial_slots.csv",
            ["id", "teacher_id", "weekday", "start", "duration_min", "capacity", "room"],
            trial_slots,
        )
        if force or not (root / "chat_bookings.csv").exists():
            _write_csv(
                root / "chat_bookings.csv",
                [
                    "id",
                    "course_key",
                    "slot_start",
                    "session_id",
                    "teacher_id",
                    "parent_name",
                    "child_name",
                    "phone",
                    "notes",
                    "created_at",
                ],
                [],
            )
        (root / "README.txt").write_text(
            "BrightNest demo admin sheets (CSV).\n"
            "Edit in Excel / Google Sheets (download/upload) — the API reloads on each request.\n"
            "families.csv maps WhatsApp numbers → parents/children for conversational booking.\n"
            "chat_bookings.csv is appended by the booking tool when a parent books in chat.\n",
            encoding="utf-8",
        )
    return root


def _load_bundle() -> Dict[str, Any]:
    seed_sheets_if_needed()
    root = sheets_dir()
    teachers_raw = _read_csv(root / "teachers.csv")
    windows_raw = _read_csv(root / "windows.csv")
    sessions_raw = _read_csv(root / "sessions.csv")
    enrol_raw = _read_csv(root / "enrolments.csv")
    trials_raw = _read_csv(root / "trial_slots.csv")

    enrol_by_session: Dict[str, List[Dict[str, str]]] = {}
    for row in enrol_raw:
        enrol_by_session.setdefault(row["session_id"], []).append(
            {"child": row.get("child", ""), "parent": row.get("parent", "")}
        )

    windows_by_teacher: Dict[str, List[Dict[str, Any]]] = {}
    for row in windows_raw:
        windows_by_teacher.setdefault(row["teacher_id"], []).append(
            {
                "weekday": int(row["weekday"]),
                "start": row["start"],
                "end": row["end"],
            }
        )

    teachers = []
    for row in teachers_raw:
        teachers.append(
            {
                "id": row["id"],
                "name": row["name"],
                "role": row.get("role", ""),
                "courses": [c for c in (row.get("courses") or "").split("|") if c],
                "room_default": row.get("room_default", ""),
                "windows": windows_by_teacher.get(row["id"], []),
            }
        )

    standing = []
    for row in sessions_raw:
        standing.append(
            {
                "id": row["id"],
                "teacher_id": row["teacher_id"],
                "course_key": row["course_key"],
                "weekday": int(row["weekday"]),
                "start": row["start"],
                "duration_min": int(row["duration_min"]),
                "capacity": int(row["capacity"]),
                "room": row.get("room", ""),
                "enrolled": enrol_by_session.get(row["id"], []),
            }
        )

    trials = []
    for row in trials_raw:
        trials.append(
            {
                "id": row["id"],
                "teacher_id": row["teacher_id"],
                "weekday": int(row["weekday"]),
                "start": row["start"],
                "duration_min": int(row["duration_min"]),
                "capacity": int(row["capacity"]),
                "room": row.get("room", ""),
            }
        )

    return {
        "teachers": teachers,
        "standing_sessions": standing,
        "trial_slots": trials,
        "teacher_by_id": {t["id"]: t for t in teachers},
        "sheets_dir": str(root),
    }


def get_register() -> Dict[str, Any]:
    with _LOCK:
        return _load_bundle()


def course_label(course_key: str, lang: str = "en") -> str:
    labels = COURSE_LABELS.get(course_key) or COURSE_LABELS["trial"]
    return labels.get(lang, labels["en"])


def teacher_name(teacher_id: str) -> str:
    reg = get_register()
    t = reg["teacher_by_id"].get(teacher_id)
    return t["name"] if t else teacher_id


def count_chat_bookings(course_key: str, slot_start_iso: str) -> int:
    root = sheets_dir()
    rows = _read_csv(root / "chat_bookings.csv")
    return sum(
        1
        for r in rows
        if r.get("course_key") == course_key and r.get("slot_start") == slot_start_iso
    )


def append_chat_booking(row: Dict[str, Any]) -> None:
    root = sheets_dir()
    path = root / "chat_bookings.csv"
    fieldnames = [
        "id",
        "course_key",
        "slot_start",
        "session_id",
        "teacher_id",
        "parent_name",
        "child_name",
        "phone",
        "notes",
        "created_at",
    ]
    with _LOCK:
        seed_sheets_if_needed()
        existing = _read_csv(path)
        existing.append({k: row.get(k, "") for k in fieldnames})
        _write_csv(path, fieldnames, existing)


def get_chat_booking(booking_id: str) -> Optional[Dict[str, Any]]:
    rows = _read_csv(sheets_dir() / "chat_bookings.csv")
    for r in rows:
        if r.get("id") == booking_id:
            return dict(r)
    return None


def reseed(force: bool = True) -> Dict[str, Any]:
    seed_sheets_if_needed(force=force)
    reg = get_register()
    return {
        "sheets_dir": reg["sheets_dir"],
        "teachers": len(reg["teachers"]),
        "sessions": len(reg["standing_sessions"]),
        "trial_slots": len(reg["trial_slots"]),
    }
