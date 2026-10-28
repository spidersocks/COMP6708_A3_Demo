"""Availability + booking backed by synthetic admin timetable data."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import SETTINGS
from services import schedule_data as sd
from services import schedule_sheets as sheets

HK_TZ = ZoneInfo("Asia/Hong_Kong")


def _conn() -> sqlite3.Connection:
    SETTINGS.bookings_db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SETTINGS.bookings_db))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            course_key TEXT NOT NULL,
            slot_start TEXT NOT NULL,
            parent_name TEXT,
            child_name TEXT,
            phone TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            teacher_id TEXT,
            session_id TEXT
        )
        """
    )
    # Lightweight migrations for older demo DBs
    cols = {r[1] for r in c.execute("PRAGMA table_info(bookings)").fetchall()}
    if "teacher_id" not in cols:
        c.execute("ALTER TABLE bookings ADD COLUMN teacher_id TEXT")
    if "session_id" not in cols:
        c.execute("ALTER TABLE bookings ADD COLUMN session_id TEXT")
    c.commit()
    return c


def list_courses(lang: str = "en") -> List[Dict[str, str]]:
    return [{"key": k, "label": sd.course_label(k, lang)} for k in sd.COURSE_LABELS]


def list_teachers() -> List[Dict[str, Any]]:
    out = []
    for t in sheets.get_register()["teachers"]:
        out.append(
            {
                "id": t["id"],
                "name": t["name"],
                "role": t["role"],
                "courses": list(t["courses"]),
                "room_default": t["room_default"],
                "windows": list(t["windows"]),
            }
        )
    return out


def _booked_count(course_key: str, slot_start_iso: str) -> int:
    # Live admin sheet is source of truth for chat bookings
    return sheets.count_chat_bookings(course_key, slot_start_iso)


def _parse_hhmm(hhmm: str) -> time:
    h, m = map(int, hhmm.split(":"))
    return time(h, m)


def _in_window(teacher_id: str, weekday: int, start_hhmm: str) -> bool:
    t = sheets.get_register()["teacher_by_id"].get(teacher_id)
    if not t:
        return False
    start_t = _parse_hhmm(start_hhmm)
    for w in t["windows"]:
        if int(w["weekday"]) != weekday:
            continue
        if _parse_hhmm(w["start"]) <= start_t < _parse_hhmm(w["end"]):
            return True
    return False


def _session_templates_for_course(course_key: str) -> List[Dict[str, Any]]:
    reg = sheets.get_register()
    if course_key == "trial":
        return [
            {
                **slot,
                "course_key": "trial",
                "enrolled": [],
            }
            for slot in reg["trial_slots"]
        ]
    return [s for s in reg["standing_sessions"] if s["course_key"] == course_key]


def _slot_dict(
    *,
    course_key: str,
    lang: str,
    start_dt: datetime,
    template: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    iso = start_dt.isoformat()
    enrolled_n = len(template.get("enrolled") or [])
    capacity = int(template["capacity"])
    taken = enrolled_n + _booked_count(course_key, iso)
    remaining = max(0, capacity - taken)
    if remaining <= 0:
        return None
    teacher_id = template["teacher_id"]
    return {
        "course_key": course_key,
        "course_label": sd.course_label(course_key, lang),
        "slot_start": iso,
        "slot_display": start_dt.strftime("%a %d %b %Y, %H:%M"),
        "duration_min": int(template["duration_min"]),
        "seats_left": remaining,
        "capacity": capacity,
        "enrolled_count": enrolled_n,
        "teacher_id": teacher_id,
        "teacher_name": sd.teacher_name(teacher_id),
        "room": template.get("room")
        or sheets.get_register()["teacher_by_id"].get(teacher_id, {}).get("room_default", ""),
        "session_id": template["id"],
        "kind": "trial" if course_key == "trial" else "class",
    }


def get_availability(
    course_key: str = "trial",
    *,
    days_ahead: int = 14,
    lang: str = "en",
    teacher_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if course_key not in sd.COURSE_LABELS:
        course_key = "trial"
    now = datetime.now(HK_TZ)
    start_day = now.date()
    templates = _session_templates_for_course(course_key)
    if teacher_id:
        templates = [t for t in templates if t["teacher_id"] == teacher_id]

    slots_out: List[Dict[str, Any]] = []
    for offset in range(0, days_ahead + 1):
        d = start_day + timedelta(days=offset)
        wd = d.weekday()
        for tmpl in templates:
            if tmpl["weekday"] != wd:
                continue
            if not _in_window(tmpl["teacher_id"], wd, tmpl["start"]):
                # Keep data honest: only offer slots inside the teacher's map
                continue
            hour, minute = map(int, tmpl["start"].split(":"))
            start_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=HK_TZ)
            if start_dt <= now:
                continue
            slot = _slot_dict(course_key=course_key, lang=lang, start_dt=start_dt, template=tmpl)
            if slot:
                slots_out.append(slot)
    return slots_out


def create_booking(
    *,
    course_key: str,
    slot_start: str,
    parent_name: str = "",
    child_name: str = "",
    phone: str = "",
    notes: str = "",
) -> Dict[str, Any]:
    if course_key not in sd.COURSE_LABELS:
        raise ValueError(f"Unknown course: {course_key}")
    avail = {s["slot_start"]: s for s in get_availability(course_key, days_ahead=21)}
    if slot_start not in avail:
        raise ValueError("Selected slot is not available")
    if avail[slot_start]["seats_left"] <= 0:
        raise ValueError("Selected slot is full")

    slot = avail[slot_start]
    booking_id = str(uuid.uuid4())[:8]
    created = datetime.now(HK_TZ).isoformat()
    sheet_row = {
        "id": booking_id,
        "course_key": course_key,
        "slot_start": slot_start,
        "session_id": slot.get("session_id"),
        "teacher_id": slot.get("teacher_id"),
        "parent_name": parent_name,
        "child_name": child_name,
        "phone": phone,
        "notes": notes,
        "created_at": created,
    }
    # Write-through to the live CSV sheet (demo "admin spreadsheet")
    sheets.append_chat_booking(sheet_row)
    # Keep SQLite mirror for older tooling
    with _conn() as c:
        c.execute(
            """
            INSERT INTO bookings (
                id, course_key, slot_start, parent_name, child_name, phone, notes,
                created_at, teacher_id, session_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                booking_id,
                course_key,
                slot_start,
                parent_name,
                child_name,
                phone,
                notes,
                created,
                slot.get("teacher_id"),
                slot.get("session_id"),
            ),
        )
        c.commit()
    return {
        "id": booking_id,
        "course_key": course_key,
        "course_label": slot["course_label"],
        "slot_start": slot_start,
        "slot_display": slot["slot_display"],
        "parent_name": parent_name,
        "child_name": child_name,
        "phone": phone,
        "notes": notes,
        "created_at": created,
        "status": "confirmed",
        "teacher_id": slot.get("teacher_id"),
        "teacher_name": slot.get("teacher_name"),
        "room": slot.get("room"),
        "session_id": slot.get("session_id"),
        "sheet": "chat_bookings.csv",
    }


def get_booking(booking_id: str) -> Optional[Dict[str, Any]]:
    data = sheets.get_chat_booking(booking_id)
    if not data:
        with _conn() as c:
            row = c.execute("SELECT * FROM bookings WHERE id=?", (booking_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
    tid = data.get("teacher_id")
    if tid:
        data["teacher_name"] = sd.teacher_name(tid)
    data["course_label"] = sd.course_label(data.get("course_key") or "trial")
    return data


def format_availability_message(slots: List[Dict[str, Any]], lang: str = "en") -> str:
    if not slots:
        msg = {
            "en": "Sorry, no open seats in the next two weeks for that course. Try another course or leave your number for a callback.",
            "zh-HK": "未來兩星期暫時未有空位。你可以轉其他課程，或者留電話等我哋聯絡。",
            "zh-CN": "未来两周暂时没有空位。你可以换其他课程，或者留电话等我们联络。",
        }
        return msg.get(lang, msg["en"])
    label = slots[0]["course_label"]
    if lang.startswith("zh"):
        header_s = f"「{label}」近期空位："
    else:
        header_s = f"Here are upcoming openings for {label}:"

    lines = [header_s]
    for s in slots[:6]:
        teacher = s.get("teacher_name") or "teacher TBC"
        room = s.get("room") or ""
        room_bit = f", {room}" if room else ""
        lines.append(
            f"• {s['slot_display']} — {teacher}{room_bit} — {s['seats_left']} seat(s) left"
        )
    tip = {
        "en": "\nThese seats come from the centre register (teacher calendars). Reply with a time to book, or use the Book button.",
        "zh-HK": "\n呢啲係中心編班表嘅空位。回覆一個時間就可以預約，或者用「預約」掣。",
        "zh-CN": "\n这些是中心排课表上的空位。回复一个时间就可以预约，或者用「预约」按钮。",
    }.get(lang, "")
    return "\n".join(lines) + tip


def get_week_schedule(*, days: int = 7, lang: str = "en") -> Dict[str, Any]:
    """Admin-style week view: rostered sessions + remaining open seats."""
    now = datetime.now(HK_TZ)
    start_day = now.date()
    day_rows: List[Dict[str, Any]] = []
    reg = sheets.get_register()

    for offset in range(0, days):
        d = start_day + timedelta(days=offset)
        wd = d.weekday()
        sessions: List[Dict[str, Any]] = []

        for tmpl in reg["standing_sessions"]:
            if int(tmpl["weekday"]) != wd:
                continue
            hour, minute = map(int, tmpl["start"].split(":"))
            start_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=HK_TZ)
            iso = start_dt.isoformat()
            enrolled = list(tmpl.get("enrolled") or [])
            chat_extra = _booked_count(tmpl["course_key"], iso)
            capacity = int(tmpl["capacity"])
            seats_left = max(0, capacity - len(enrolled) - chat_extra)
            sessions.append(
                {
                    "session_id": tmpl["id"],
                    "kind": "class",
                    "course_key": tmpl["course_key"],
                    "course_label": sd.course_label(tmpl["course_key"], lang),
                    "slot_start": iso,
                    "slot_display": start_dt.strftime("%a %d %b, %H:%M"),
                    "duration_min": tmpl["duration_min"],
                    "teacher_id": tmpl["teacher_id"],
                    "teacher_name": sd.teacher_name(tmpl["teacher_id"]),
                    "room": tmpl.get("room"),
                    "capacity": capacity,
                    "enrolled": enrolled,
                    "enrolled_count": len(enrolled),
                    "chat_bookings": chat_extra,
                    "seats_left": seats_left,
                    "status": "open" if seats_left > 0 else "full",
                }
            )

        for tmpl in reg["trial_slots"]:
            if int(tmpl["weekday"]) != wd:
                continue
            hour, minute = map(int, tmpl["start"].split(":"))
            start_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=HK_TZ)
            if start_dt.date() == now.date() and start_dt <= now:
                continue
            iso = start_dt.isoformat()
            chat_extra = _booked_count("trial", iso)
            capacity = int(tmpl["capacity"])
            seats_left = max(0, capacity - chat_extra)
            sessions.append(
                {
                    "session_id": tmpl["id"],
                    "kind": "trial",
                    "course_key": "trial",
                    "course_label": sd.course_label("trial", lang),
                    "slot_start": iso,
                    "slot_display": start_dt.strftime("%a %d %b, %H:%M"),
                    "duration_min": tmpl["duration_min"],
                    "teacher_id": tmpl["teacher_id"],
                    "teacher_name": sd.teacher_name(tmpl["teacher_id"]),
                    "room": tmpl.get("room"),
                    "capacity": capacity,
                    "enrolled": [],
                    "enrolled_count": 0,
                    "chat_bookings": chat_extra,
                    "seats_left": seats_left,
                    "status": "open" if seats_left > 0 else "full",
                }
            )

        sessions.sort(key=lambda s: s["slot_start"])
        day_rows.append(
            {
                "date": d.isoformat(),
                "label": d.strftime("%a %d %b"),
                "sessions": sessions,
            }
        )

    return {
        "generated_at": now.isoformat(),
        "timezone": "Asia/Hong_Kong",
        "source": "csv_admin_sheets",
        "sheets_dir": reg["sheets_dir"],
        "teachers": list_teachers(),
        "days": day_rows,
    }


def get_teacher_availability_map(teacher_id: str, *, days_ahead: int = 14, lang: str = "en") -> Dict[str, Any]:
    teacher = sheets.get_register()["teacher_by_id"].get(teacher_id)
    if not teacher:
        raise ValueError(f"Unknown teacher: {teacher_id}")
    open_slots: List[Dict[str, Any]] = []
    for course in teacher["courses"]:
        open_slots.extend(
            get_availability(course, days_ahead=days_ahead, lang=lang, teacher_id=teacher_id)
        )
    open_slots.sort(key=lambda s: s["slot_start"])
    return {
        "teacher": {
            "id": teacher["id"],
            "name": teacher["name"],
            "role": teacher["role"],
            "courses": list(teacher["courses"]),
            "room_default": teacher["room_default"],
            "windows": list(teacher["windows"]),
        },
        "open_slots": open_slots,
        "open_slot_count": len(open_slots),
    }
