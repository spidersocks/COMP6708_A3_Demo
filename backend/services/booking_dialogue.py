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
    r"("
    r"\b("
    r"book|booking|reserve|enrol|enroll|sign\s*up|register|"
    r"can\s+i\s+book|could\s+i\s+book|want\s+to\s+book|need\s+to\s+book|"
    r"make\s+a\s+booking|book\s+a\s+class|book\s+a\s+lesson|"
    r"any\s+(?:class|lesson|slot)s?\s+for"
    r")\b|"
    r"預約|预约|报名|報名|報讀|报读|想幫.{0,12}報|想帮.{0,12}报"
    r")",
    re.I,
)
# "try out phonics" / "want to try" — seat booking, not a FAQ about trials
_TRYOUT_RE = re.compile(
    r"("
    r"try\s+out|"
    r"want(?:s)?\s+to\s+try|"
    r"(?:like|love)\s+to\s+try|"
    r"(?:can|could)\s+(?:i|we)\s+try|"
    r"arrange\s+(?:a\s+)?(?:trial|placement|assessment)|"
    r"(?:book|schedule)\s+(?:a\s+)?(?:trial|placement|assessment)|"
    r"試(一)?下|试(一)?下|想試|想试"
    r")",
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
    r"\b(where\s+are\s+you|address|location|parking)\b|"
    r"\b(need\s+to\s+book|do\s+i\s+need|must\s+i|before\s+class|first\s+time|"
    r"not\s+attended|never\s+attended)\b.{0,40}\b(trial|assessment)\b"
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
# Named KB programmes map into the live seat register keys (phonics / trial / …)
_COURSE_HINTS = [
    # English progression names (must win before bare "spell"/"sound" noise)
    (
        re.compile(
            r"little\s+sound\s+explorer|sound\s+explorers?|little\s+linguist|"
            r"smart\s+spell|language\s+arts|小小發音|小小发音|拼音探索",
            re.I,
        ),
        "phonics",
    ),
    (re.compile(r"phonic|拼音", re.I), "phonics"),
    (re.compile(r"mandarin|putonghua|普通話|普通话", re.I), "mandarin"),
    (re.compile(r"writ|寫作|写作", re.I), "writing"),
    (re.compile(r"play\s*group|遊戲班|游戏班", re.I), "playgroup"),
    (re.compile(r"\bmath\b|early\s+math|數學|数学|numeracy", re.I), "math"),
    (
        re.compile(
            r"\b(trial|試堂|试听|diagnostic\s+assessment|placement\s+assessment|"
            r"assessment|評估|评估)\b",
            re.I,
        ),
        "trial",
    ),
]
_NAMED_ENGLISH_PROGRAMME_RE = re.compile(
    r"little\s+sound\s+explorer|sound\s+explorers?|little\s+linguist|"
    r"smart\s+spell|language\s+arts|小小發音|小小发音",
    re.I,
)
_AFFIRM_RE = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok|okay|please|go\s+ahead|confirm|"
    r"好|係|是|可以|要|嗯|ok啦|好呀|好的)\s*[!.。！？]*\s*$",
    re.I,
)
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


def is_trial_policy_question(text: str) -> bool:
    """Whether a trial is required / comes before class — not a seat-booking request."""
    return bool(
        re.search(
            r"("
            r"\b(do\s+i|do\s+we|does\s+(he|she|my\s+(kid|child))|must\s+i|have\s+to|"
            r"need\s+to|required|necessary|should\s+i)\b.{0,50}\b(trial|assessment)\b|"
            r"\b(trial|assessment)\b.{0,50}\b(first|before\s+(class|enrol|enroll|joining)|"
            r"required|necessary)\b|"
            r"\b(not\s+attended|never\s+attended|first\s+time|hasn'?t\s+attended)\b"
            r".{0,60}\b(trial|assessment)\b|"
            r"要不要先(試堂|试听)|需(不|唔)?需要(先)?(試堂|试听)|要先(試堂|试听)"
            r")",
            text or "",
            re.I,
        )
    )


def is_tryout_request(text: str) -> bool:
    """Parent wants to try / arrange a trial or placement — book a live trial seat."""
    t = text or ""
    if is_trial_policy_question(t):
        return False
    return bool(_TRYOUT_RE.search(t))


def is_booking_request(text: str) -> bool:
    t = text or ""
    if is_trial_policy_question(t):
        return False
    # "cancel my booking" belongs to cancel_dialogue — do not also award book.
    from services import cancel_dialogue as cancel_svc

    if cancel_svc.is_cancel_request(t):
        return False
    if is_tryout_request(t):
        return True
    return bool(_BOOK_REQUEST_RE.search(t))


def is_active(session_id: str) -> bool:
    st = get_state(session_id)
    return bool(st and st.get("stage") and st.get("stage") != "done")


def _detect_course(text: str) -> Optional[str]:
    for pat, key in _COURSE_HINTS:
        if pat.search(text or ""):
            return key
    return None


def _named_english_programme(text: str) -> Optional[str]:
    m = _NAMED_ENGLISH_PROGRAMME_RE.search(text or "")
    if not m:
        return None
    raw = re.sub(r"\s+", " ", m.group(0)).strip()
    # Canonicalise plurals for staff sheet copy
    low = raw.lower()
    if "sound explorer" in low:
        return "Little Sound Explorer"
    if "linguist" in low:
        return "Little Linguist"
    if "smart spell" in low:
        return "Smart Spell"
    if "language arts" in low:
        return "Language Arts"
    return raw.title()


def unknown_programme(text: str) -> Optional[str]:
    """Return a named activity if the parent asked about something we don't teach."""
    if _detect_course(text):
        return None
    m = _UNKNOWN_PROGRAMME_RE.search(text or "")
    return m.group(1) if m else None


def is_affirmative(text: str) -> bool:
    return bool(_AFFIRM_RE.match((text or "").strip()))


def _is_booking_refine(text: str) -> bool:
    """Signals that should stay inside the booking dialogue."""
    if _detect_course(text) or _detect_teacher(text) or _parse_when(text):
        return True
    if _named_english_programme(text):
        return True
    if is_affirmative(text):
        return True
    if _has_slot_pick_signal(text):
        return True
    if _parse_identity(text):
        return True
    if re.search(
        r"\b((option\s*)?\d{1,2}|different|another|refine|slot|seats?|earlier|later)\b",
        text or "",
        re.I,
    ):
        return True
    return False


def should_yield_to_rag(session_id: str, text: str) -> bool:
    """Active booking must not swallow fee/FAQ/unknown-programme questions.

    A fresh turn is not ejected here. Detectors and the action policy already
    chose booking; a word like "holiday" or "fee" inside that sentence must
    not bounce it back to RAG. Keywords only interrupt a dialogue that is open.
    """
    st = get_state(session_id)
    active = bool(st and st.get("stage") and st.get("stage") != "done")
    if not active:
        return bool(unknown_programme(text))
    # Still a booking act — stay, even if the sentence also mentions fees/holiday.
    if is_booking_request(text) or _is_booking_refine(text):
        return False
    if st.get("stage") in {"confirm_assessment", "confirm_slot"} and is_affirmative(text):
        return False
    if st.get("stage") in {"need_identity", "need_parent", "need_child"} and _parse_identity(text):
        return False
    if unknown_programme(text) or is_trial_policy_question(text) or _OFF_TOPIC_RE.search(text or ""):
        return True
    # Active offer / clarify loop + unrelated question → leave booking
    if re.search(r"[?？]", text or "") or re.match(
        r"^(how|what|when|where|why|do|does|is|are|can|could|would)\b",
        text or "",
        re.I,
    ):
        # "Can I book Jordan …" is still a booking refine
        if is_booking_request(text) or _has_slot_pick_signal(text):
            return False
        return True
    # Short non-refine follow-ups while offering seats / clarifying when
    # (need_course + known programme name is handled by _is_booking_refine above)
    if st.get("stage") in {"offer_slots", "no_slots", "need_when_asked", "confirm_slot"}:
        if not is_booking_request(text) and not _is_booking_refine(text):
            return True
    if st.get("stage") in {"need_course", "need_identity", "need_parent", "need_child"}:
        # Bare junk at need_course still yields; programme names do not
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


def _detect_clock(text: str) -> Optional[Tuple[int, int]]:
    """Parse '10:00', '10.00', '10am', '10 pm' → (hour, minute) 24h."""
    t = text or ""
    m = re.search(r"\b(\d{1,2})[:.](\d{2})\b", t)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            # Bare afternoon centre hours: 1–6 without am/pm → treat as PM
            if 1 <= h <= 6 and not re.search(r"\b(am|pm)\b", t, re.I):
                # Keep as-is if already looks like 10:00 style morning; 10 is fine
                pass
            return h, mi
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", t, re.I)
    if m:
        h = int(m.group(1))
        ap = m.group(2).lower()
        if h == 12:
            h = 0
        if ap == "pm":
            h += 12
        if 0 <= h <= 23:
            return h, 0
    m = re.search(r"\b(\d{1,2})\s*o'?clock\b", t, re.I)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0
    return None


def _detect_day_of_month(text: str) -> Optional[int]:
    t = text or ""
    patterns = (
        r"\b(?:mon|tue|wed|thu|fri|sat|sun)\w*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b",
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        r"\b(?:on\s+)?(?:the\s+)(\d{1,2})(?:st|nd|rd|th)\b",
        r"\b(\d{1,2})(?:st|nd|rd|th)\b",
    )
    for pat in patterns:
        m = re.search(pat, t, re.I)
        if not m:
            continue
        day = int(m.group(1))
        if 1 <= day <= 31:
            return day
    return None


def _norm_person_name(raw: str) -> str:
    s = (raw or "").strip(" .,;:!?")
    if not s:
        return ""
    # Drop accidental sentence continuation ("Chester. I want…")
    s = re.split(r"[.!?]+", s, maxsplit=1)[0].strip()
    # "child is named David" / "called Mia" — "named"/"called" are not part of the name
    s = re.sub(r"^(?:named|called)\s+", "", s, flags=re.I).strip()
    parts = [p for p in re.split(r"\s+", s) if p]
    # Keep at most first+last; reject if a token looks like a sentence word
    _stop = {
        "i",
        "want",
        "to",
        "book",
        "a",
        "the",
        "my",
        "and",
        "looking",
        "for",
        "is",
        "am",
        "named",
        "called",
        "do",
        "phonics",
        "math",
        "mandarin",
        "writing",
        "playgroup",
        "trial",
    }
    parts = [p for p in parts if p.lower() not in _stop][:2]
    s = " ".join(parts).strip(" .,;:!?")
    if not s:
        return ""
    return s.title() if s.islower() or s.isupper() else s


def _parse_identity(text: str) -> Dict[str, str]:
    """Pull parent / child names from a new-parent reply."""
    t = (text or "").strip()
    out: Dict[str, str] = {}
    if not t:
        return out

    # Allow "kid's name" / "child is named X" (possessive + named/called)
    _child_cue = (
        r"(?:my\s+)?(?:child|kid|son|daughter|girl|boy)(?:'?s)?"
        r"(?:\s*name)?(?:\s+is)?(?:\s+(?:named|called))?"
    )
    # No '.' inside names — periods end the capture (avoids "Jeff. My" / "Chester. I")
    _name = r"([A-Za-z][A-Za-z']*(?:\s+[A-Za-z][A-Za-z']*)?)"
    patterns = [
        (
            r"(?:i'?m|i\s+am|my\s+name\s+is|parent(?:\s*name)?(?:\s+is)?)\s+"
            + _name
            + r".{0,60}?"
            + r"(?:"
            + _child_cue
            + r"|booking\s+for|for)\s+"
            + _name,
            "parent",
            "child",
        ),
        (
            _child_cue
            + r"\s+"
            + _name
            + r".{0,60}?"
            + r"(?:parent(?:\s*name)?(?:\s+is)?|i'?m|i\s+am|my\s+name\s+is)\s+"
            + _name,
            "child",
            "parent",
        ),
        (
            r"parent\s*[:\-]\s*([A-Za-z][A-Za-z\s'.]{1,40}?)\s*[,\n;/|]+\s*"
            r"child\s*[:\-]\s*([A-Za-z][A-Za-z\s'.]{1,40})",
            "parent",
            "child",
        ),
    ]
    for pat, a, b in patterns:
        m = re.search(pat, t, re.I | re.S)
        if not m:
            continue
        va, vb = _norm_person_name(m.group(1)), _norm_person_name(m.group(2))
        if families._is_plausible_person_name(va) and families._is_plausible_person_name(vb):  # noqa: SLF001
            out[a] = va
            out[b] = vb
            return out

    # "Jane Lee / Mia Lee" or "Jane Lee, Mia Lee"
    m = re.match(
        r"^\s*([A-Za-z][A-Za-z'.]+(?:\s+[A-Za-z][A-Za-z'.]+)?)\s*[/|,]\s*"
        r"([A-Za-z][A-Za-z'.]+(?:\s+[A-Za-z][A-Za-z'.]+)?)\s*$",
        t,
    )
    if m:
        p, c = _norm_person_name(m.group(1)), _norm_person_name(m.group(2))
        if families._is_plausible_person_name(p) and families._is_plausible_person_name(c):  # noqa: SLF001
            out["parent"] = p
            out["child"] = c
            return out

    # Separate cues in one message: "My name is Jeff. My kid's name is Chester."
    mp = re.search(
        r"(?:i'?m|i\s+am|my\s+name\s+is|parent(?:\s*name)?(?:\s+is)?)\s+" + _name,
        t,
        re.I,
    )
    mc = re.search(_child_cue + r"\s+" + _name, t, re.I)
    if mp:
        p = _norm_person_name(mp.group(1))
        if families._is_plausible_person_name(p):  # noqa: SLF001
            out["parent"] = p
    if mc:
        c = _norm_person_name(mc.group(1))
        if families._is_plausible_person_name(c):  # noqa: SLF001
            out["child"] = c
    if out.get("parent") and out.get("child"):
        return out
    if out.get("parent") or out.get("child"):
        return out

    # Single plausible name when only one field is missing (handled by caller)
    m = re.match(r"^\s*([A-Za-z][A-Za-z'.]+(?:\s+[A-Za-z][A-Za-z'.]+)?)\s*$", t)
    if m and families._is_plausible_person_name(m.group(1)):  # noqa: SLF001
        out["single"] = _norm_person_name(m.group(1))
    return out


def _match_offered_slots(
    slots: List[Dict[str, Any]],
    text: str,
    *,
    teacher_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Narrow offered seats by weekday / clock / day-of-month / teacher in the reply."""
    out = list(slots or [])
    if not out:
        return out
    wd = _detect_weekday(text)
    clock = _detect_clock(text)
    day = _detect_day_of_month(text)
    tid = teacher_id or _detect_teacher(text)

    if tid:
        by_t = [s for s in out if s.get("teacher_id") == tid]
        if by_t:
            out = by_t
    if wd is not None:
        kept = []
        for s in out:
            try:
                st = datetime.fromisoformat(str(s.get("slot_start") or ""))
                if st.weekday() == wd:
                    kept.append(s)
            except Exception:
                continue
        if kept:
            out = kept
    if day is not None:
        kept = []
        for s in out:
            try:
                st = datetime.fromisoformat(str(s.get("slot_start") or ""))
                if st.day == day:
                    kept.append(s)
            except Exception:
                continue
        if kept:
            out = kept
    if clock is not None:
        hh, mm = clock
        kept = []
        for s in out:
            try:
                st = datetime.fromisoformat(str(s.get("slot_start") or ""))
                if st.hour == hh and st.minute == mm:
                    kept.append(s)
            except Exception:
                continue
        if kept:
            out = kept
        else:
            # Allow "10" / "10am" style soft match on hour only when unique among pool
            soft = []
            for s in out:
                try:
                    st = datetime.fromisoformat(str(s.get("slot_start") or ""))
                    if st.hour == hh:
                        soft.append(s)
                except Exception:
                    continue
            if soft:
                out = soft
    return out


def _has_slot_pick_signal(text: str) -> bool:
    if _detect_clock(text) is not None:
        return True
    if _detect_day_of_month(text) is not None:
        return True
    if _detect_weekday(text) is not None and (
        _detect_teacher(text) or _detect_clock(text) is not None or _detect_day_of_month(text) is not None
    ):
        return True
    if re.search(r"\b((option\s*)?#?\d{1,2})\b", text or "", re.I):
        return True
    if _detect_teacher(text) and (_detect_weekday(text) is not None or _detect_day_of_month(text) is not None):
        return True
    return False


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
    history_preview: str = "",
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
        "hide_register": False,
    }

    # Refresh identity from this turn / UI fields
    if phone:
        state["phone"] = phone
    if parent_name:
        state["parent_name"] = parent_name

    # Try-out / trial / assessment → live Trial Assessment seats (never SheetPro-only)
    course_hint = _detect_course(text)
    tryout = is_tryout_request(text) or course_hint == "trial"
    if tryout or re.search(r"\btrial\b|試堂|试听|placement\s+assessment", text or "", re.I):
        state["hide_register"] = True
        interest = course_hint if course_hint and course_hint != "trial" else None
        if interest:
            state["interest_course"] = interest
            if not state.get("programme_label"):
                state["programme_label"] = course_label(interest)
        # Force trial register even when "phonics" also matched in the same utterance
        if tryout or course_hint == "trial":
            state["course_key"] = "trial"

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
    if should_yield_to_rag(session_id, text):
        unk = unknown_programme(text)
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

    # Merge hints from this utterance (regex first)
    course = _detect_course(text)
    teacher = _detect_teacher(text)
    when_bits = _parse_when(text)
    named_prog = _named_english_programme(text)
    if named_prog:
        state["programme_label"] = named_prog
    if course:
        # Try-out already locked course_key=trial — keep interest on the side
        if state.get("course_key") == "trial" and course != "trial":
            state["interest_course"] = course
            if not state.get("programme_label"):
                state["programme_label"] = course_label(course)
        else:
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
        # UI hint only for known demo families — never invent a child from a blank new parent
        if family.get("matched") or families._is_plausible_person_name(child_name_hint):  # noqa: SLF001
            mentioned = child_name_hint

    steps: List[str] = []
    identity = _parse_identity(text)
    regex_identity = dict(identity)
    if identity.get("parent") and not family.get("matched"):
        state["parent_name"] = identity["parent"]
    if identity.get("child") and not state.get("child_name"):
        mentioned = identity["child"]
    elif identity.get("single"):
        # Fill whichever identity field is still missing for a new parent
        if not family.get("matched") and not (state.get("parent_name") or "").strip():
            state["parent_name"] = identity["single"]
        elif not state.get("child_name"):
            mentioned = identity["single"]

    # Prefer UI-provided parent name once the New Parent profile is chosen
    if not family.get("matched") and parent_name and not (state.get("parent_name") or "").strip():
        if families._is_plausible_person_name(parent_name):  # noqa: SLF001
            state["parent_name"] = parent_name

    # Cheap-LLM DST when regex left identity / schedule slots empty
    dst_missing: List[str] = []
    if not family.get("matched"):
        if not (state.get("parent_name") or "").strip():
            dst_missing.append("parent_name")
        child_probe = (state.get("child_name") or mentioned or "").strip()
        if not child_probe or not families._is_plausible_person_name(child_probe):  # noqa: SLF001
            dst_missing.append("child_name")
    if not (state.get("course_key") or state.get("programme_label")):
        dst_missing.append("course_key")
    if not (state.get("when") or {}):
        dst_missing.extend(["weekday", "window"])
    if not teacher and not state.get("teacher_id"):
        dst_missing.append("teacher_hint")

    if dst_missing:
        try:
            from services import demo_polish

            prior_slots = {
                "parent_name": state.get("parent_name") or "",
                "child_name": state.get("child_name") or mentioned or "",
                "course_key": state.get("course_key") or "",
                "weekday": (state.get("when") or {}).get("weekday"),
                "window": (state.get("when") or {}).get("window"),
                "teacher_hint": state.get("teacher_id") or "",
                "stage": state.get("stage") or "",
            }
            dst = demo_polish.track_booking_slots(
                text,
                prior=prior_slots,
                missing=dst_missing,
                history_preview=history_preview,
            )
            dst["regex_identity"] = regex_identity
            dst["regex_course"] = course
            dst["regex_when"] = when_bits or {}
            state["arbiter_booking"] = dst
            state["booking_dst"] = dst
            if dst.get("ok"):
                filled = dst.get("filled") or {}
                if (
                    filled.get("parent_name")
                    and not family.get("matched")
                    and not (state.get("parent_name") or "").strip()
                    and families._is_plausible_person_name(filled["parent_name"])  # noqa: SLF001
                ):
                    state["parent_name"] = filled["parent_name"]
                    steps.append("dst_parent")
                if (
                    filled.get("child_name")
                    and families._is_plausible_person_name(filled["child_name"])  # noqa: SLF001
                    and not (
                        mentioned and families._is_plausible_person_name(mentioned)  # noqa: SLF001
                    )
                ):
                    mentioned = filled["child_name"]
                    steps.append("dst_child")
                if not course and filled.get("course_key"):
                    filled_course = filled["course_key"]
                    if state.get("course_key") == "trial" and filled_course != "trial":
                        state["interest_course"] = filled_course
                        if not state.get("programme_label"):
                            state["programme_label"] = course_label(filled_course)
                        steps.append("dst_interest_course")
                    else:
                        course = filled_course
                        state["course_key"] = filled_course
                        steps.append("dst_course")
                if not when_bits:
                    soft_when: Dict[str, Any] = {}
                    if filled.get("window"):
                        soft_when["window"] = filled["window"]
                    if filled.get("weekday") is not None:
                        soft_when["weekday"] = filled["weekday"]
                    if soft_when:
                        when_bits = soft_when
                        merged_when = dict(state.get("when") or {})
                        merged_when.update(when_bits)
                        state["when"] = merged_when
                        steps.append("dst_when")
                if not teacher and filled.get("teacher_hint"):
                    teacher = _detect_teacher(str(filled["teacher_hint"])) or teacher
                    if teacher:
                        state["teacher_id"] = teacher
                        steps.append("dst_teacher")
        except Exception as e:
            print(f"[BOOKING] DST failed: {e}", flush=True)

    # --- New parent / unknown WhatsApp: collect parent + child names ---
    if not family.get("matched"):
        state["is_new_family"] = True
        # Never keep pronoun / kinship fragments as child_name ("my", "child", …)
        if (state.get("child_name") or "").strip() and not families._is_plausible_person_name(  # noqa: SLF001
            state.get("child_name") or ""
        ):
            state["child_name"] = ""
        child_ok = bool(
            (state.get("child_name") or "").strip()
            and families._is_plausible_person_name(state.get("child_name") or "")  # noqa: SLF001
        ) or bool(
            mentioned and families._is_plausible_person_name(mentioned)  # noqa: SLF001
        )
        need_parent = not (state.get("parent_name") or "").strip()
        need_child = not child_ok
        need_class = not (state.get("course_key") or state.get("programme_label"))
        if need_parent or need_child:
            if need_parent and need_child:
                stage = "need_identity"
                if need_class:
                    prompt = (
                        "Happy to help with a booking. I don't have a family on this WhatsApp yet.\n\n"
                        "Please reply with **your name**, **your child's name**, and which **class** "
                        "(Phonics, Math, Mandarin, Writing, Playgroup, or Trial) — "
                        "e.g. *I'm Alex Wong, child is Mia Wong, looking at Math*."
                    )
                else:
                    prompt = (
                        "Happy to help with a booking. I don't have a family on this WhatsApp yet.\n\n"
                        "Please reply with **your name** and **your child's name** "
                        "(e.g. *I'm Alex Wong, child is Mia Wong*)."
                    )
            elif need_parent:
                kid = (
                    mentioned
                    if mentioned and families._is_plausible_person_name(mentioned)  # noqa: SLF001
                    else (state.get("child_name") or "").strip()
                )
                stage = "need_parent"
                prompt = (
                    f"Thanks — booking for **{kid}**. "
                    "What's the **parent's name** I should put on the booking?"
                    + (
                        " And which **class** should I look at?"
                        if need_class
                        else ""
                    )
                )
            else:
                stage = "need_child"
                prompt = (
                    f"Thanks **{state.get('parent_name')}**. "
                    "What's your **child's name** for the class register?"
                    + (
                        " And which **class** should I look at?"
                        if need_class
                        else ""
                    )
                )
            save_state(session_id, {**state, "stage": stage})
            return {
                "answer": prompt,
                "slots": None,
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": stage,
                    "phone": state.get("phone"),
                    "parent_name": state.get("parent_name") or "",
                    "child_name": state.get("child_name") or mentioned or "",
                    "steps": steps,
                    "booking_dst": state.get("booking_dst") or state.get("arbiter_booking"),
                    "arbiter_booking": state.get("arbiter_booking"),
                },
                "state": state,
            }
        if mentioned and families._is_plausible_person_name(mentioned):  # noqa: SLF001
            state["child_name"] = mentioned
            state["preferred_courses"] = []
            state["child_schedule"] = []
            state["hide_register"] = True
            steps.append(f"new_family_child={mentioned}")

    # --- Resolve child (known family sheet) ---
    if not state.get("child_name"):
        resolved = families.resolve_child(
            phone=state.get("phone") or "",
            mentioned_name=mentioned,
            fallback_child=child_name_hint if family.get("matched") else "",
        )
        steps.append(f"family_match={resolved['family'].get('matched')}")
        if not resolved["ok"]:
            if resolved.get("reason") in {"unknown_number", "need_identity"}:
                save_state(session_id, {**state, "stage": "need_identity", "is_new_family": True})
                return {
                    "answer": (
                        "Happy to help with a booking. I don't have a family on this WhatsApp yet.\n\n"
                        "Please reply with **your name**, **your child's name**, and which **class** "
                        "(e.g. *I'm Alex Wong, child is Mia Wong, looking at Math*)."
                    ),
                    "slots": None,
                    "provider": "booking_dialogue",
                    "trace_booking": {
                        "stage": "need_identity",
                        "reason": resolved.get("reason"),
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

    # Re-resolve if user named a different child mid-flow (known families only)
    elif mentioned and family.get("matched"):
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
    show_register = (
        not state.get("hide_register")
        and (state.get("course_key") not in {None, "trial"})
        and bool(state.get("child_schedule"))
    )
    schedule_blurb = (
        _format_child_schedule(child_name, state.get("child_schedule") or [])
        if show_register
        else ""
    )

    # --- Assessment confirm → book a live Trial Assessment seat (not SheetPro) ---
    if state.get("stage") == "confirm_assessment":
        prog = state.get("programme_label") or course_label(
            state.get("interest_course") or "phonics"
        )
        state["course_key"] = "trial"
        state["hide_register"] = True
        if is_affirmative(text) or when_bits or teacher:
            # Fall through to when-ask / trial seat offer
            if when_bits or (state.get("when") or {}).get("window") is not None or (
                state.get("when") or {}
            ).get("weekday") is not None:
                state["stage"] = "offer_slots"
            else:
                state["stage"] = "need_when_asked"
            steps.append(f"assessment_to_trial_seats·{prog}")
        else:
            save_state(session_id, {**state, "stage": "need_when_asked"})
            return {
                "answer": (
                    f"Great — **{child_name}** can sit a short **Trial Assessment** "
                    f"toward **{prog}**.\n\n"
                    "When works best — **this week**, **next week**, or a weekday "
                    "(e.g. Thursday)?"
                ),
                "slots": None,
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "need_when",
                    "child_name": child_name,
                    "programme_label": prog,
                    "course_key": "trial",
                    "steps": steps,
                },
                "state": {**state, "stage": "need_when_asked"},
            }

    # --- Confirm selected seat ---
    if state.get("stage") == "confirm_slot" and state.get("selected_slot"):
        sel = state["selected_slot"]
        if is_affirmative(text):
            from services import final_check

            summary = (
                f"Book {child_name} into {sel.get('course_label') or course_label(state.get('course_key') or '')} "
                f"on {sel.get('slot_display')} with {sel.get('teacher_name') or 'staff'}."
            )
            # Sticky confirm + clear yes — no second LLM vote.
            gate = final_check.affirmative_proceed(
                kind="book", proposed_summary=summary
            )
            try:
                interest = state.get("programme_label") or (
                    course_label(state["interest_course"])
                    if state.get("interest_course")
                    else ""
                )
                book_key = state.get("course_key") or sel.get("course_key") or "phonics"
                notes = "booked via WhatsApp booking dialogue"
                if book_key == "trial" and interest:
                    notes = f"trial toward {interest}"
                booked = booking_svc.create_booking(
                    course_key=book_key,
                    slot_start=sel["slot_start"],
                    parent_name=state.get("parent_name") or "",
                    child_name=child_name,
                    phone=state.get("phone") or "",
                    notes=notes,
                )
            except Exception as e:
                save_state(session_id, {**state, "stage": "offer_slots"})
                return {
                    "answer": (
                        f"I couldn't lock that seat ({e}). "
                        "Want me to show the open times again?"
                    ),
                    "slots": None,
                    "provider": "booking_dialogue",
                    "trace_booking": {
                        "stage": "book_failed",
                        "error": str(e)[:160],
                        "selected": sel,
                        "final_check": gate,
                        "steps": steps,
                    },
                    "state": state,
                }
            clear_state(session_id)
            kind = (
                "Trial Assessment"
                if book_key == "trial"
                else (booked.get("course_label") or course_label(book_key))
            )
            toward = f" toward **{interest}**" if book_key == "trial" and interest else ""
            return {
                "answer": (
                    f"Booked — **{child_name}** is on the register for "
                    f"**{kind}**{toward} on "
                    f"**{booked.get('slot_display') or sel.get('slot_display')}** "
                    f"({booked.get('teacher_name') or sel.get('teacher_name')} · "
                    f"{booked.get('room') or sel.get('room')}).\n\n"
                    f"Booking ref **{booked.get('id')}**. "
                    "See you then — message us if you need to change it."
                ),
                "slots": None,
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "booked",
                    "booking": booked,
                    "final_check": gate,
                    "steps": steps,
                },
                "state": None,
            }
        # Parent named a different time while confirming — fall through to rematch
        if not _has_slot_pick_signal(text) and not _parse_when(text):
            save_state(session_id, state)
            return {
                "answer": (
                    f"Just to confirm — book **{child_name}** for "
                    f"**{sel.get('slot_display')}** "
                    f"({sel.get('teacher_name')} · {sel.get('room')} · "
                    f"{sel.get('seats_left')} left)?\n\n"
                    "Reply **yes** to confirm, or send another day/time."
                ),
                "slots": [sel],
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "confirm_slot",
                    "selected": sel,
                    "steps": steps,
                },
                "state": state,
            }

    # --- Clarify course ---
    if not state.get("course_key"):
        preferred = state.get("preferred_courses") or []
        save_state(session_id, {**state, "stage": "need_course"})
        pref_txt = ", ".join(course_label(c) for c in preferred) if preferred else "Phonics, Mandarin, Writing, Math, Playgroup, or Trial"
        sched_block = f"{schedule_blurb}\n\n" if schedule_blurb else ""
        return {
            "answer": (
                f"Got it — booking for **{child_name}**"
                f"{' (family: ' + state.get('parent_name') + ')' if state.get('parent_name') else ''}.\n\n"
                f"{sched_block}"
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

    # Named English programme → Trial Assessment seats on the live register
    if (
        state.get("programme_label")
        and state.get("course_key") == "phonics"
        and state.get("stage") in {None, "start", "need_course", "need_child"}
        and not (state.get("when") or {}).get("window")
        and state.get("when", {}).get("weekday") is None
    ):
        prog = state["programme_label"]
        state["interest_course"] = state.get("interest_course") or "phonics"
        state["course_key"] = "trial"
        state["hide_register"] = True
        state["stage"] = "need_when_asked"
        save_state(session_id, state)
        return {
            "answer": (
                f"Wonderful choice — **{prog}** starts with a short **Trial Assessment** "
                f"so we place **{child_name}** at the right level.\n\n"
                "When works best — **this week**, **next week**, or a weekday "
                "(e.g. Thursday)?"
            ),
            "slots": None,
            "provider": "booking_dialogue",
            "trace_booking": {
                "stage": "need_when",
                "child_name": child_name,
                "programme_label": prog,
                "course_key": "trial",
                "phone": state.get("phone"),
                "parent_name": state.get("parent_name"),
                "steps": steps,
            },
            "state": state,
        }

    # --- Default when-window if still empty ---
    if not (state.get("when") or {}).get("window") and state.get("when", {}).get("weekday") is None:
        # Ask once unless message already implied urgency via booking request alone
        if not when_bits and state.get("stage") != "need_when_asked":
            save_state(session_id, {**state, "stage": "need_when_asked"})
            sched_block = f"{schedule_blurb}\n\n" if schedule_blurb else ""
            look = course_label(state["course_key"])
            if state.get("course_key") == "trial" and state.get("programme_label"):
                look = f"Trial Assessment toward {state['programme_label']}"
            return {
                "answer": (
                    f"Looking at **{look}** for **{child_name}**.\n\n"
                    f"{sched_block}"
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

    # --- Offer / pick slots ---
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

    # Rebuild the last offered pool so "Friday 10:00" can commit instead of re-listing
    prior_starts = list(state.get("offered_slot_starts") or [])
    prior_pool: List[Dict[str, Any]] = []
    if prior_starts:
        by_start = {s["slot_start"]: s for s in filtered}
        # Also search raw_slots in case weekday filter narrowed too hard
        for s in raw_slots:
            by_start.setdefault(s["slot_start"], s)
        prior_pool = [by_start[s] for s in prior_starts if s in by_start]
        if not prior_pool:
            prior_pool = list(filtered)

    pick_pool = prior_pool or list(filtered)
    if state.get("stage") in {"offer_slots", "confirm_slot", "no_slots"} or (
        prior_pool and _has_slot_pick_signal(text)
    ):
        matched = _match_offered_slots(
            pick_pool,
            text,
            teacher_id=state.get("teacher_id"),
        )
        # Option index: "1" / "option 2"
        idx_m = re.search(r"\b(?:option|number|choice)\s*#?\s*(\d{1,2})\b", text or "", re.I)
        if not idx_m:
            idx_m = re.fullmatch(r"\s*#?\s*(\d{1,2})\s*[.)]?\s*", (text or "").strip())
        if idx_m and pick_pool:
            i = int(idx_m.group(1)) - 1
            if 0 <= i < len(pick_pool):
                matched = [pick_pool[i]]
        steps.append(f"slot_matches={len(matched)}")
        if len(matched) == 1 and (
            _has_slot_pick_signal(text)
            or state.get("stage") == "confirm_slot"
            or (state.get("stage") == "offer_slots" and len(pick_pool) == 1)
        ):
            sel = matched[0]
            state["selected_slot"] = sel
            state["stage"] = "confirm_slot"
            state["offered_slot_starts"] = prior_starts or [sel["slot_start"]]
            save_state(session_id, state)
            return {
                "answer": (
                    f"Perfect — **{sel.get('slot_display')}** with "
                    f"**{sel.get('teacher_name')}** ({sel.get('room')}, "
                    f"{sel.get('seats_left')} left).\n\n"
                    f"Shall I book **{child_name}** into that "
                    f"{'trial assessment' if state.get('course_key') == 'trial' else 'class'}? "
                    "Reply **yes** to confirm."
                ),
                "slots": [sel],
                "provider": "booking_dialogue",
                "trace_booking": {
                    "stage": "confirm_slot",
                    "child_name": child_name,
                    "selected": sel,
                    "steps": steps,
                },
                "state": state,
            }
        if matched and len(matched) < len(pick_pool) and _has_slot_pick_signal(text):
            # Narrow the offer instead of looping the same full list
            filtered = matched

    if not filtered:
        save_state(session_id, {**state, "stage": "no_slots"})
        teacher_bit = (
            f" with {sheets.teacher_name(state['teacher_id'])}" if state.get("teacher_id") else ""
        )
        checked = course_label(state["course_key"])
        if state.get("course_key") == "trial" and state.get("programme_label"):
            checked = f"Trial Assessment toward {state['programme_label']}"
        return {
            "answer": (
                f"I checked **{checked}**{teacher_bit} for **{child_name}** "
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

    # Single remaining seat after refine → confirm directly
    if len(filtered) == 1 and (
        _has_slot_pick_signal(text) or state.get("stage") in {"offer_slots", "confirm_slot"}
    ):
        sel = filtered[0]
        state["selected_slot"] = sel
        state["stage"] = "confirm_slot"
        state["offered_slot_starts"] = [sel["slot_start"]]
        save_state(session_id, state)
        return {
            "answer": (
                f"I have **{sel.get('slot_display')}** with "
                f"**{sel.get('teacher_name')}** ({sel.get('room')}, "
                f"{sel.get('seats_left')} left).\n\n"
                f"Shall I book **{child_name}** into that "
                f"{'trial assessment' if state.get('course_key') == 'trial' else 'class'}? "
                "Reply **yes** to confirm."
            ),
            "slots": [sel],
            "provider": "booking_dialogue",
            "trace_booking": {
                "stage": "confirm_slot",
                "child_name": child_name,
                "selected": sel,
                "steps": steps,
            },
            "state": state,
        }

    offer = filtered[:6]
    state["stage"] = "offer_slots"
    state["selected_slot"] = None
    state["offered_slot_starts"] = [s["slot_start"] for s in offer]
    save_state(session_id, state)

    offer_label = course_label(state["course_key"])
    if state.get("course_key") == "trial" and state.get("programme_label"):
        offer_label = f"Trial Assessment toward {state['programme_label']}"
    lines = [
        f"Here are open **{offer_label}** seats for **{child_name}** "
        f"({when_label}) that don't clash with their register:",
    ]
    for s in offer:
        lines.append(
            f"• {s['slot_display']} — {s.get('teacher_name')} · {s.get('room')} · {s['seats_left']} left"
        )
    lines.append(
        "\nReply with a time (e.g. **Friday 10:00**), or tell me another day/teacher to refine."
    )

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
