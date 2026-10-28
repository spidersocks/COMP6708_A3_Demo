"""Multi-turn booking dialogue: resolve child from WhatsApp number + name,
inspect their register, clarify course/time, then offer conflict-free slots.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config import SETTINGS
from services import booking as booking_svc
from services import families
from services import schedule_sheets as sheets
from services.schedule_data import course_label

HK_TZ = ZoneInfo("Asia/Hong_Kong")
_lock = threading.Lock()

_BOOK_REQUEST_RE = re.compile(
    r"\b("
    r"book|booking|reserve|enrol|enroll|sign\s*up|register|"
    r"can\s+i\s+book|could\s+i\s+book|want\s+to\s+book|need\s+to\s+book|"
    r"make\s+a\s+booking|book\s+a\s+class|book\s+a\s+lesson|"
    r"any\s+(?:class|lesson|slot)s?\s+for|"
    r"預約|预约|报名|報名"
    r")\b",
    re.I,
)
# Abort the *booking chat* — not "cancel my class" (that belongs to cancel_dialogue)
_ABORT_RE = re.compile(
    r"\b(never\s*mind|forget\s+it|stop\s+booking|cancel\s+booking|算了|唔使啦)\b",
    re.I,
)
# Leave booking and answer via RAG / admin-gap instead
_OFF_TOPIC_RE = re.compile(
    r"("
    r"\b(how\s+much|fee|fees|price|pricing|cost|tuition|學費|学费|幾錢|多少钱)\b|"
    r"\b(opening\s+hours?|what\s+time\s+do\s+you|holiday|weather)\b|"
    r"\b(refund|discontinu|policy|policies|allergy|allergies|homework|progress)\b|"
    r"\b(where\s+are\s+you|address|location|parking)\b"
    r")",
    re.I,
)
# Activities we do not run — do not trap these inside the phonics booking loop
_UNKNOWN_PROGRAMME_RE = re.compile(
    r"\b("
    r"scuba|diving|swim(?:ming)?|piano|violin|guitar|ballet|dance|"
    r"karate|taekwondo|football|soccer|tennis|coding|robotics|"
    r"cooking|art\s*class|painting|drama|chess"
    r")\b",
    re.I,
)
_COURSE_HINTS = [
    (re.compile(r"phonic|拼音", re.I), "phonics"),
    (re.compile(r"mandarin|putonghua|普通話|普通话", re.I), "mandarin"),
    (re.compile(r"writ|寫作|写作", re.I), "writing"),
    (re.compile(r"play\s*group|遊戲班|游戏班", re.I), "playgroup"),
    (re.compile(r"\bmath\b|數學|数学|numeracy", re.I), "math"),
    (re.compile(r"trial|試堂|试听|assessment|評估|评估", re.I), "trial"),
]
_TEACHER_HINTS = [
    # "Ms Chen" is a common parent typo for Avery Chan in the demo roster
    (re.compile(r"\b(avery|ms\.?\s*chan|ms\.?\s*chen)\b", re.I), "avery"),
    (re.compile(r"\b(wei|mr\.?\s*lin)\b", re.I), "wei"),
    (re.compile(r"\b(priya|ms\.?\s*das)\b", re.I), "priya"),
    (re.compile(r"\b(maya|ms\.?\s*cheung)\b", re.I), "maya"),
    (re.compile(r"\b(jordan|mr\.?\s*blake)\b", re.I), "jordan"),
    (re.compile(r"\b(hana|ms\.?\s*zhou)\b", re.I), "hana"),
    (re.compile(r"\b(tom|mr\.?\s*reyes)\b", re.I), "tom"),
    (re.compile(r"\b(sofia|ms\.?\s*almeida)\b", re.I), "sofia"),
]
_WEEKDAY_RE = [
    (re.compile(r"\bmon(?:day)?\b", re.I), 0),
    (re.compile(r"\btue(?:s(?:day)?)?\b", re.I), 1),
    (re.compile(r"\bwed(?:nesday)?\b", re.I), 2),
    (re.compile(r"\bthu(?:rs(?:day)?)?\b", re.I), 3),
    (re.compile(r"\bfri(?:day)?\b", re.I), 4),
    (re.compile(r"\bsat(?:urday)?\b", re.I), 5),
    (re.compile(r"\bsun(?:day)?\b", re.I), 6),
]


def _conn() -> sqlite3.Connection:
    SETTINGS.bookings_db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SETTINGS.bookings_db))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS booking_dialogue (
            session_id TEXT PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    c.commit()
    return c


def get_state(session_id: str) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    with _lock:
        with _conn() as c:
            row = c.execute(
                "SELECT state_json FROM booking_dialogue WHERE session_id=?",
                (session_id,),
            ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["state_json"])
    except json.JSONDecodeError:
        return None


def save_state(session_id: str, state: Dict[str, Any]) -> None:
    with _lock:
        with _conn() as c:
            c.execute(
                """
                INSERT INTO booking_dialogue (session_id, state_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  state_json=excluded.state_json,
                  updated_at=excluded.updated_at
                """,
                (session_id, json.dumps(state, ensure_ascii=False), datetime.now(HK_TZ).isoformat()),
            )
            c.commit()


def clear_state(session_id: str) -> None:
    with _lock:
        with _conn() as c:
            c.execute("DELETE FROM booking_dialogue WHERE session_id=?", (session_id,))
            c.commit()


def is_booking_request(text: str) -> bool:
    return bool(_BOOK_REQUEST_RE.search(text or ""))


def is_active(session_id: str) -> bool:
    st = get_state(session_id)
    return bool(st and st.get("stage") and st.get("stage") != "done")


def _detect_course(text: str) -> Optional[str]:
    for pat, key in _COURSE_HINTS:
        if pat.search(text or ""):
            return key
    return None


def unknown_programme(text: str) -> Optional[str]:
    """Return a named activity if the parent asked about something we don't teach."""
    if _detect_course(text):
        return None
    m = _UNKNOWN_PROGRAMME_RE.search(text or "")
    return m.group(1) if m else None


def _is_booking_refine(text: str) -> bool:
    """Signals that should stay inside the booking dialogue."""
    if _detect_course(text) or _detect_teacher(text) or _parse_when(text):
        return True
    if re.search(
        r"\b((option\s*)?\d{1,2}|different|another|refine|slot|seats?|earlier|later)\b",
        text or "",
        re.I,
    ):
        return True
    return False


def should_yield_to_rag(session_id: str, text: str) -> bool:
    """Active booking must not swallow fee/FAQ/unknown-programme questions."""
    st = get_state(session_id)
    active = bool(st and st.get("stage") and st.get("stage") != "done")
    if unknown_programme(text):
        return True
    if _OFF_TOPIC_RE.search(text or ""):
        return True
    if not active:
        return False
    if _is_booking_refine(text):
        return False
    if is_booking_request(text) and _detect_course(text):
        return False
    # Active offer / clarify loop + unrelated question → leave booking
    if re.search(r"[?？]", text or "") or re.match(
        r"^(how|what|when|where|why|do|does|is|are|can|could|would)\b",
        text or "",
        re.I,
    ):
        return True
    # Short non-refine follow-ups ("How much", "fees", "ok thanks") while offering seats
    if st.get("stage") in {"offer_slots", "no_slots", "need_course", "need_when_asked"}:
        if not is_booking_request(text) and not _is_booking_refine(text):
            return True
    return False


def _detect_teacher(text: str) -> Optional[str]:
    for pat, key in _TEACHER_HINTS:
        if pat.search(text or ""):
            return key
    return None


def _detect_weekday(text: str) -> Optional[int]:
    for pat, wd in _WEEKDAY_RE:
        if pat.search(text or ""):
            return wd
    return None


def _parse_when(text: str) -> Dict[str, Any]:
    t = text or ""
    out: Dict[str, Any] = {}
    if re.search(r"\bnext\s+week\b", t, re.I):
        out["window"] = "next_week"
    elif re.search(r"\bthis\s+week\b", t, re.I):
        out["window"] = "this_week"
    elif re.search(r"\btomorrow\b", t, re.I):
        out["window"] = "tomorrow"
    wd = _detect_weekday(t)
    if wd is not None:
        out["weekday"] = wd
    return out


def _window_bounds(when: Dict[str, Any]) -> Tuple[datetime, datetime]:
    now = datetime.now(HK_TZ)
    today = now.date()
    window = when.get("window") or "next_14_days"
    if window == "tomorrow":
        start = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=HK_TZ)
        end = start + timedelta(days=1)
    elif window == "this_week":
        # Remaining days through Sunday
        days_to_sun = 6 - today.weekday()
        start = now
        end = datetime.combine(today + timedelta(days=days_to_sun + 1), datetime.min.time(), tzinfo=HK_TZ)
    elif window == "next_week":
        days_to_mon = (7 - today.weekday()) % 7
        if days_to_mon == 0:
            days_to_mon = 7
        start = datetime.combine(today + timedelta(days=days_to_mon), datetime.min.time(), tzinfo=HK_TZ)
        end = start + timedelta(days=7)
    else:
        start = now
        end = now + timedelta(days=14)
    return start, end


def child_upcoming_sessions(child_name: str, *, days_ahead: int = 21) -> List[Dict[str, Any]]:
    """Standing register + chat bookings for this child."""
    reg = sheets.get_register()
    now = datetime.now(HK_TZ)
    out: List[Dict[str, Any]] = []
    target = (child_name or "").strip().lower()

    for sess in reg["standing_sessions"]:
        enrolled_names = [e["child"].lower() for e in (sess.get("enrolled") or [])]
        if target not in enrolled_names and not any(target.split()[0] == n.split()[0] for n in enrolled_names if n):
            # also allow first-name match
            first = target.split()[0] if target else ""
            if not first or not any(n.split()[0] == first for n in enrolled_names if n):
                continue
        for offset in range(0, days_ahead + 1):
            d = (now + timedelta(days=offset)).date()
            if d.weekday() != int(sess["weekday"]):
                continue
            hour, minute = map(int, sess["start"].split(":"))
            start_dt = datetime(d.year, d.month, d.day, hour, minute, tzinfo=HK_TZ)
            if start_dt <= now:
                continue
            end_dt = start_dt + timedelta(minutes=int(sess["duration_min"]))
            out.append(
                {
                    "kind": "register",
                    "course_key": sess["course_key"],
                    "course_label": course_label(sess["course_key"]),
                    "teacher_id": sess["teacher_id"],
                    "teacher_name": sheets.teacher_name(sess["teacher_id"]),
                    "slot_start": start_dt.isoformat(),
                    "slot_end": end_dt.isoformat(),
                    "slot_display": start_dt.strftime("%a %d %b %Y, %H:%M"),
                    "room": sess.get("room"),
                }
            )

    for row in sheets._read_csv(sheets.sheets_dir() / "chat_bookings.csv"):  # noqa: SLF001
        cn = (row.get("child_name") or "").strip().lower()
        if not cn:
            continue
        if cn != target and cn.split()[0] != (target.split()[0] if target else ""):
            continue
        try:
            start_dt = datetime.fromisoformat(row["slot_start"])
        except Exception:
            continue
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=HK_TZ)
        if start_dt <= now:
            continue
        dur = 60
        end_dt = start_dt + timedelta(minutes=dur)
        out.append(
            {
                "kind": "chat_booking",
                "course_key": row.get("course_key"),
                "course_label": course_label(row.get("course_key") or "trial"),
                "teacher_id": row.get("teacher_id"),
                "teacher_name": sheets.teacher_name(row.get("teacher_id") or ""),
                "slot_start": start_dt.isoformat(),
                "slot_end": end_dt.isoformat(),
                "slot_display": start_dt.strftime("%a %d %b %Y, %H:%M"),
                "room": "",
                "booking_id": row.get("id"),
            }
        )

    out.sort(key=lambda x: x["slot_start"])
    return out


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _filter_slots(
    slots: List[Dict[str, Any]],
    *,
    when: Dict[str, Any],
    child_busy: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    start_bound, end_bound = _window_bounds(when)
    weekday = when.get("weekday")
    busy = []
    for b in child_busy:
        try:
            bs = datetime.fromisoformat(b["slot_start"])
            be = datetime.fromisoformat(b["slot_end"])
            if bs.tzinfo is None:
                bs = bs.replace(tzinfo=HK_TZ)
            if be.tzinfo is None:
                be = be.replace(tzinfo=HK_TZ)
            busy.append((bs, be))
        except Exception:
            continue

    kept = []
    for s in slots:
        try:
            st = datetime.fromisoformat(s["slot_start"])
        except Exception:
            continue
        if st.tzinfo is None:
            st = st.replace(tzinfo=HK_TZ)
        if st < start_bound or st >= end_bound:
            continue
        if weekday is not None and st.weekday() != weekday:
            continue
        en = st + timedelta(minutes=int(s.get("duration_min") or 60))
        if any(_overlaps(st, en, bs, be) for bs, be in busy):
            s = {**s, "blocked_reason": "child_conflict"}
            continue
        kept.append(s)
    return kept


def _format_child_schedule(child_name: str, items: List[Dict[str, Any]]) -> str:
    if not items:
        return f"{child_name} has no upcoming classes on the register in the next few weeks."
    lines = [f"{child_name}'s upcoming classes:"]
    for it in items[:5]:
        lines.append(
            f"• {it['slot_display']} — {it['course_label']} with {it.get('teacher_name') or 'TBC'}"
        )
    if len(items) > 5:
        lines.append(f"• …and {len(items) - 5} more")
    return "\n".join(lines)


def handle_turn(
    *,
    session_id: str,
    message: str,
    phone: str = "",
    parent_name: str = "",
    child_name_hint: str = "",
    lang: str = "en",
) -> Dict[str, Any]:
    """Process one conversational booking turn. Returns answer/slots/trace/state."""
    text = (message or "").strip()
    state = get_state(session_id) or {
        "stage": "start",
        "phone": phone or "",
        "parent_name": parent_name or "",
        "child_name": "",
        "course_key": None,
        "teacher_id": None,
        "when": {},
        "child_schedule": [],
    }

    # Refresh identity from this turn / UI fields
    if phone:
        state["phone"] = phone
    if parent_name:
        state["parent_name"] = parent_name

    if _ABORT_RE.search(text):
        clear_state(session_id)
        return {
            "answer": "No problem — I've stopped the booking chat. Say if you'd like to start again.",
            "slots": None,
            "provider": "booking_dialogue",
            "trace_booking": {"stage": "cancelled"},
            "state": None,
        }

    # Unknown programme (scuba, piano, …) or off-topic ask → leave booking for RAG
    unk = unknown_programme(text)
    if unk or should_yield_to_rag(session_id, text):
        clear_state(session_id)
        return {
            "answer": "",
            "slots": None,
            "provider": "booking_dialogue",
            "yield_to_rag": True,
            "trace_booking": {
                "stage": "yield_to_rag",
                "unknown_programme": unk,
                "reason": "unknown_programme" if unk else "off_topic",
            },
            "state": None,
        }

    # Merge hints from this utterance
    course = _detect_course(text)
    teacher = _detect_teacher(text)
    when_bits = _parse_when(text)
    if course:
        state["course_key"] = course
    if teacher:
        state["teacher_id"] = teacher
    if when_bits:
        merged_when = dict(state.get("when") or {})
        merged_when.update(when_bits)
        state["when"] = merged_when

    family = families.list_family_by_phone(state.get("phone") or "")
    known = [c["child_name"] for c in family.get("children") or []]
    mentioned = families.extract_child_mention(text, known)
    if not mentioned and child_name_hint and not state.get("child_name"):
        mentioned = child_name_hint

    steps: List[str] = []

    # --- Resolve child ---
    if not state.get("child_name"):
        resolved = families.resolve_child(
            phone=state.get("phone") or "",
            mentioned_name=mentioned,
            fallback_child=child_name_hint,
        )
        steps.append(f"family_match={resolved['family'].get('matched')}")
        if not resolved["ok"]:
            if resolved.get("reason") == "unknown_number":
                save_state(session_id, {**state, "stage": "need_phone"})
                return {
                    "answer": (
                        "I can help book that. Which WhatsApp number should I use for the family "
                        "account? (In this demo, try **+852 9000 0000** — Demo Parent / Jason Chan.)"
                    ),
                    "slots": None,
                    "provider": "booking_dialogue",
                    "trace_booking": {
                        "stage": "need_phone",
                        "reason": "unknown_number",
                        "steps": steps,
                    },
                    "state": state,
                }
            names = ", ".join(c["child_name"] for c in resolved.get("candidates") or [])
            save_state(session_id, {**state, "stage": "need_child"})
            if resolved.get("ambiguous") or resolved.get("reason") == "need_child_name":
                return {
                    "answer": (
                        f"Happy to help. On **{family.get('parent_name') or 'this number'}** I have: "
                        f"{names}. Which child should I book for?"
                    ),
                    "slots": None,
                    "provider": "booking_dialogue",
                    "trace_booking": {
                        "stage": "need_child",
                        "family": family.get("parent_name"),
                        "candidates": [c["child_name"] for c in resolved.get("candidates") or []],
                        "steps": steps,
                    },
                    "state": state,
                }
            return {
                "answer": (
                    f"I couldn't match that name to the family on {state.get('phone')}. "
                    f"Children on file: {names or 'none'}. Who should I book for?"
                ),
                "slots": None,
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "need_child",
                    "reason": resolved.get("reason"),
                    "steps": steps,
                },
                "state": state,
            }

        child = resolved["child"]
        state["child_name"] = child["child_name"]
        state["parent_name"] = state.get("parent_name") or family.get("parent_name") or ""
        state["preferred_courses"] = list(child.get("preferred_courses") or [])
        state["child_schedule"] = child_upcoming_sessions(child["child_name"])
        steps.append(f"resolved_child={child['child_name']}")
        steps.append(f"child_upcoming={len(state['child_schedule'])}")

    # Re-resolve if user named a different child mid-flow
    elif mentioned:
        resolved = families.resolve_child(
            phone=state.get("phone") or "",
            mentioned_name=mentioned,
        )
        if resolved["ok"] and resolved["child"]["child_name"] != state.get("child_name"):
            child = resolved["child"]
            state["child_name"] = child["child_name"]
            state["preferred_courses"] = list(child.get("preferred_courses") or [])
            state["child_schedule"] = child_upcoming_sessions(child["child_name"])
            state["course_key"] = None
            steps.append(f"switched_child={child['child_name']}")

    child_name = state["child_name"]
    schedule_blurb = _format_child_schedule(child_name, state.get("child_schedule") or [])

    # --- Clarify course ---
    if not state.get("course_key"):
        preferred = state.get("preferred_courses") or []
        save_state(session_id, {**state, "stage": "need_course"})
        pref_txt = ", ".join(course_label(c) for c in preferred) if preferred else "Phonics, Mandarin, Writing, Math, Playgroup, or Trial"
        return {
            "answer": (
                f"Got it — booking for **{child_name}**"
                f"{' (family: ' + state.get('parent_name') + ')' if state.get('parent_name') else ''}.\n\n"
                f"{schedule_blurb}\n\n"
                f"Which class should I look at? "
                + (f"On file they usually take: {pref_txt}." if preferred else f"Options: {pref_txt}.")
            ),
            "slots": None,
            "provider": "booking_dialogue",
            "trace_booking": {
                "stage": "need_course",
                "child_name": child_name,
                "phone": state.get("phone"),
                "parent_name": state.get("parent_name"),
                "child_schedule": state.get("child_schedule"),
                "preferred_courses": preferred,
                "steps": steps,
            },
            "state": state,
        }

    # --- Default when-window if still empty ---
    if not (state.get("when") or {}).get("window") and state.get("when", {}).get("weekday") is None:
        # Ask once unless message already implied urgency via booking request alone
        if not when_bits and state.get("stage") != "need_when_asked":
            save_state(session_id, {**state, "stage": "need_when_asked"})
            return {
                "answer": (
                    f"Looking at **{course_label(state['course_key'])}** for **{child_name}**.\n\n"
                    f"{schedule_blurb}\n\n"
                    "When works best — **this week**, **next week**, or a weekday (e.g. Tuesday)?"
                ),
                "slots": None,
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "need_when",
                    "child_name": child_name,
                    "course_key": state["course_key"],
                    "child_schedule": state.get("child_schedule"),
                    "steps": steps,
                },
                "state": state,
            }
        merged_when = dict(state.get("when") or {})
        merged_when["window"] = "next_14_days"
        state["when"] = merged_when

    # --- Offer slots ---
    raw_slots = booking_svc.get_availability(
        state["course_key"],
        days_ahead=21,
        lang=lang,
        teacher_id=state.get("teacher_id"),
    )
    filtered = _filter_slots(
        raw_slots,
        when=state.get("when") or {},
        child_busy=state.get("child_schedule") or [],
    )
    steps.append(f"raw_slots={len(raw_slots)}")
    steps.append(f"after_conflict_filter={len(filtered)}")

    when = state.get("when") or {}
    when_label = when.get("window") or "the next two weeks"
    if when.get("weekday") is not None:
        when_label = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"][
            when["weekday"]
        ]

    if not filtered:
        save_state(session_id, {**state, "stage": "no_slots"})
        teacher_bit = (
            f" with {sheets.teacher_name(state['teacher_id'])}" if state.get("teacher_id") else ""
        )
        return {
            "answer": (
                f"I checked **{course_label(state['course_key'])}**{teacher_bit} for **{child_name}** "
                f"({when_label}), skipping times that clash with their existing classes.\n\n"
                f"{schedule_blurb}\n\n"
                "No open seats matched. Want a different course, teacher, or week?"
            ),
            "slots": None,
            "provider": "booking_dialogue",
            "trace_booking": {
                "stage": "no_slots",
                "child_name": child_name,
                "course_key": state["course_key"],
                "teacher_id": state.get("teacher_id"),
                "when": when,
                "child_schedule": state.get("child_schedule"),
                "raw_slots": len(raw_slots),
                "steps": steps,
            },
            "state": state,
        }

    offer = filtered[:6]
    state["stage"] = "offer_slots"
    state["offered_slot_starts"] = [s["slot_start"] for s in offer]
    save_state(session_id, state)

    lines = [
        f"Here are open **{course_label(state['course_key'])}** seats for **{child_name}** "
        f"({when_label}) that don't clash with their register:",
    ]
    for s in offer:
        lines.append(
            f"• {s['slot_display']} — {s.get('teacher_name')} · {s.get('room')} · {s['seats_left']} left"
        )
    lines.append("\nTap **Book** on a slot, or tell me another day/teacher to refine.")

    return {
        "answer": "\n".join(lines),
        "slots": offer,
        "provider": "booking_dialogue",
        "trace_booking": {
            "stage": "offer_slots",
            "child_name": child_name,
            "phone": state.get("phone"),
            "parent_name": state.get("parent_name"),
            "course_key": state["course_key"],
            "teacher_id": state.get("teacher_id"),
            "when": when,
            "child_schedule": state.get("child_schedule"),
            "offered": len(offer),
            "steps": steps,
        },
        "state": state,
    }
