"""Multi-turn cancel-session dialogue with clarifying questions."""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from config import SETTINGS
from services import booking_dialogue as book_dlg
from services import families
from services import schedule_sheets as sheets

HK_TZ = ZoneInfo("Asia/Hong_Kong")
_lock = threading.Lock()

_CANCEL_SESSION_RE = re.compile(
    r"("
    r"\b(cancel|cancelling|canceled|cancelled|call\s*off|withdraw)\b.{0,60}\b"
    r"(class|lesson|session|booking|appointment|trial|seat|"
    r"phonic|mandarin|math|writing|play\s*group|putonghua)\w*\b"
    r"|"
    r"\b(class|lesson|session|booking|appointment|trial|"
    r"phonic|mandarin|math|writing|play\s*group)\w*\b.{0,60}\b"
    r"(cancel|cancelling|canceled|cancelled)\b"
    r"|"
    # "cancel Jason's Tuesday" / "cancel for Mia" without naming the course
    r"\b(cancel|cancelling|canceled|cancelled)\b.{0,40}\b"
    r"(jason|mia|for\s+my\s+(kid|child|son|daughter)|"
    r"mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday)\w*\b"
    r"|"
    r"取消.{0,6}(堂|課|课|預約|预约|试听|試堂)"
    r"|"
    r"(堂|課|课|預約|预约).{0,6}取消"
    r")",
    re.I | re.S,
)
_ABORT_RE = re.compile(r"\b(never\s*mind|forget\s+it|stop|算了|唔使)\b", re.I)
_CONFIRM_RE = re.compile(r"\b(yes|yeah|yep|confirm|please\s+cancel|ok|okay|sure|好|係|确定|確認)\b", re.I)
_COURSE_HINTS = [
    (re.compile(r"phonic|拼音", re.I), "phonics"),
    (re.compile(r"mandarin|putonghua|普通話|普通话", re.I), "mandarin"),
    (re.compile(r"writ|寫作|写作", re.I), "writing"),
    (re.compile(r"play\s*group|遊戲班|游戏班", re.I), "playgroup"),
    (re.compile(r"\bmath\b|數學|数学|numeracy", re.I), "math"),
    (re.compile(r"trial|試堂|试听|assessment", re.I), "trial"),
]


def is_cancel_request(text: str) -> bool:
    return bool(_CANCEL_SESSION_RE.search(text or ""))


def _conn() -> sqlite3.Connection:
    SETTINGS.bookings_db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(SETTINGS.bookings_db))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS cancel_dialogue (
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
                "SELECT state_json FROM cancel_dialogue WHERE session_id=?",
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
                INSERT INTO cancel_dialogue (session_id, state_json, updated_at)
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
            c.execute("DELETE FROM cancel_dialogue WHERE session_id=?", (session_id,))
            c.commit()


def is_active(session_id: str) -> bool:
    st = get_state(session_id)
    return bool(st and st.get("stage") and st.get("stage") not in {"done", "cancelled"})


def _detect_course(text: str) -> Optional[str]:
    for pat, key in _COURSE_HINTS:
        if pat.search(text or ""):
            return key
    return None


def _detect_index(text: str, n: int) -> Optional[int]:
    m = re.search(r"\b(?:option\s*)?(\d{1,2})\b", text or "")
    if m:
        i = int(m.group(1)) - 1
        if 0 <= i < n:
            return i
    # "the tuesday one" etc. left to course/weekday filters
    return None


def _teacher_tokens(text: str) -> List[str]:
    """Pull teacher-ish tokens: Ms Chen, Avery, Mr Reyes, …"""
    t = text or ""
    out: List[str] = []
    for m in re.finditer(
        r"\b(?:ms\.?|mr\.?|miss|mrs\.?)\s+([A-Za-z]{2,})\b", t, re.I
    ):
        out.append(m.group(1).lower())
    # First name after "with"
    for m in re.finditer(r"\bwith\s+(?:ms\.?|mr\.?|miss|mrs\.?)?\s*([A-Za-z]{3,})\b", t, re.I):
        tok = m.group(1).lower()
        if tok not in {"the", "our", "her", "his", "teacher"}:
            out.append(tok)
    tid = book_dlg._detect_teacher(t)  # noqa: SLF001
    if tid:
        out.append(tid.lower())
        out.append((sheets.teacher_name(tid) or "").lower())
    # De-dupe
    seen = set()
    uniq = []
    for x in out:
        if x and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def _name_fuzzy(a: str, b: str) -> bool:
    a, b = (a or "").lower(), (b or "").lower()
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    # Common HK romanisation / typo: Chen ↔ Chan, etc.
    if abs(len(a) - len(b)) <= 1 and len(a) >= 3:
        diffs = sum(1 for x, y in zip(a, b) if x != y) + abs(len(a) - len(b))
        if diffs <= 1:
            return True
    return False


def _filter_by_teacher(items: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    tokens = _teacher_tokens(text)
    if not tokens:
        return items
    matched = []
    for x in items:
        blob = f"{x.get('teacher_name') or ''} {x.get('teacher_id') or ''}".lower()
        if any(_name_fuzzy(tok, part) or tok in blob for tok in tokens for part in blob.split()):
            matched.append(x)
    return matched if matched else items


def _filter_targets(
    items: List[Dict[str, Any]],
    *,
    course_key: Optional[str],
    weekday: Optional[int],
    text: str,
) -> List[Dict[str, Any]]:
    out = items
    if course_key:
        out = [x for x in out if x.get("course_key") == course_key]
    if weekday is not None:
        filtered = []
        for x in out:
            try:
                st = datetime.fromisoformat(x["slot_start"])
                if st.weekday() == weekday:
                    filtered.append(x)
            except Exception:
                continue
        out = filtered
    out = _filter_by_teacher(out, text)
    # Match display fragment from message
    low = (text or "").lower()
    for token in re.findall(r"\b(\d{1,2}:\d{2})\b", low):
        out = [x for x in out if token in (x.get("slot_display") or "").lower()]
    return out


def _has_refining_signal(text: str) -> bool:
    """True when the parent named a course, day, time, teacher, or option #."""
    if _detect_course(text):
        return True
    if book_dlg._detect_weekday(text) is not None:  # noqa: SLF001
        return True
    if _teacher_tokens(text):
        return True
    if re.search(r"\b(?:option\s*)?\d{1,2}\b", text or ""):
        return True
    if re.search(r"\b\d{1,2}:\d{2}\b", text or ""):
        return True
    return False


def _list_options_answer(child_name: str, pool: List[Dict[str, Any]], *, preface: str = "") -> str:
    lines = [
        preface.strip()
        or f"I can cancel for **{child_name}**. Which session?",
    ]
    for i, s in enumerate(pool[:8], 1):
        lines.append(
            f"{i}. {s['slot_display']} — {s['course_label']} · {s.get('teacher_name')}"
        )
    lines.append("\nReply with a number, or name the course/day (e.g. “Tuesday phonics”).")
    return "\n".join(lines)


def _append_cancellation(row: Dict[str, Any]) -> None:
    root = sheets.sheets_dir()
    path = root / "cancellations.csv"
    fields = [
        "id",
        "created_at",
        "phone",
        "parent_name",
        "child_name",
        "course_key",
        "slot_start",
        "slot_display",
        "teacher_id",
        "session_id",
        "source_kind",
        "booking_id",
        "notes",
    ]
    with _lock:
        existing = []
        if path.exists():
            import csv

            with path.open("r", newline="", encoding="utf-8") as f:
                existing = list(csv.DictReader(f))
        existing.append({k: row.get(k, "") for k in fields})
        import csv

        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in existing:
                w.writerow({k: r.get(k, "") for k in fields})


def _remove_chat_booking(booking_id: str) -> bool:
    if not booking_id:
        return False
    import csv

    path = sheets.sheets_dir() / "chat_bookings.csv"
    with _lock:
        if not path.exists():
            return False
        with path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else [
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
        new_rows = [r for r in rows if r.get("id") != booking_id]
        if len(new_rows) == len(rows):
            return False
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in new_rows:
                w.writerow(r)
        return True


def handle_turn(
    *,
    session_id: str,
    message: str,
    phone: str = "",
    parent_name: str = "",
    child_name_hint: str = "",
    lang: str = "en",
) -> Dict[str, Any]:
    text = (message or "").strip()
    state = get_state(session_id) or {
        "stage": "start",
        "phone": phone or "",
        "parent_name": parent_name or "",
        "child_name": "",
        "candidates": [],
        "selected": None,
    }
    if phone:
        state["phone"] = phone
    if parent_name:
        state["parent_name"] = parent_name

    if _ABORT_RE.search(text) and not is_cancel_request(text):
        clear_state(session_id)
        return {
            "answer": "Okay — I won't cancel anything. Say if you need help later.",
            "provider": "cancel_dialogue",
            "trace_cancel": {"stage": "aborted"},
            "state": None,
        }

    family = families.list_family_by_phone(state.get("phone") or "")
    known = [c["child_name"] for c in family.get("children") or []]
    mentioned = families.extract_child_mention(text, known)
    if not mentioned and child_name_hint and not state.get("child_name"):
        mentioned = child_name_hint

    # Resolve child
    if not state.get("child_name"):
        resolved = families.resolve_child(
            phone=state.get("phone") or "",
            mentioned_name=mentioned,
            fallback_child=child_name_hint,
        )
        if not resolved["ok"]:
            names = ", ".join(c["child_name"] for c in resolved.get("candidates") or [])
            save_state(session_id, {**state, "stage": "need_child"})
            return {
                "answer": (
                    "I can cancel a class — which child is this for?"
                    + (f" On this number I have: {names}." if names else "")
                ),
                "provider": "cancel_dialogue",
                "trace_cancel": {"stage": "need_child", "candidates": names},
                "state": state,
            }
        state["child_name"] = resolved["child"]["child_name"]
        state["parent_name"] = state.get("parent_name") or family.get("parent_name") or ""

    child_name = state["child_name"]
    upcoming = book_dlg.child_upcoming_sessions(child_name)
    # Exclude already cancelled occurrences
    cancelled_starts = set()
    cancel_path = sheets.sheets_dir() / "cancellations.csv"
    if cancel_path.exists():
        import csv

        with cancel_path.open("r", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (r.get("child_name") or "").lower() == child_name.lower():
                    cancelled_starts.add(r.get("slot_start") or "")
    upcoming = [u for u in upcoming if u.get("slot_start") not in cancelled_starts]

    if not upcoming:
        clear_state(session_id)
        return {
            "answer": (
                f"**{child_name}** has no upcoming classes on the register or chat bookings to cancel. "
                "If you meant a different child, tell me their name."
            ),
            "provider": "cancel_dialogue",
            "trace_cancel": {"stage": "none_found", "child_name": child_name},
            "state": None,
        }

    course = _detect_course(text)
    weekday = book_dlg._detect_weekday(text)  # noqa: SLF001

    # If we're mid-clarify and the parent only said "yes"/vague, re-show the same list
    if (
        state.get("stage") == "need_which"
        and state.get("candidates")
        and not _has_refining_signal(text)
        and not is_cancel_request(text)
    ):
        pool = state["candidates"]
        preface = (
            f"Still need a pick for **{child_name}** — which of these?"
            if _CONFIRM_RE.search(text)
            else f"I can cancel for **{child_name}**. Which session?"
        )
        save_state(session_id, state)
        return {
            "answer": _list_options_answer(child_name, pool, preface=preface),
            "provider": "cancel_dialogue",
            "trace_cancel": {
                "stage": "need_which",
                "child_name": child_name,
                "options": len(pool),
                "reprompt": True,
            },
            "state": state,
        }

    candidates = _filter_targets(upcoming, course_key=course, weekday=weekday, text=text)
    idx = _detect_index(
        text, len(state.get("candidates") or candidates or upcoming)
    )

    # Confirm path
    if state.get("stage") == "confirm" and state.get("selected"):
        if _CONFIRM_RE.search(text) or is_cancel_request(text):
            sel = state["selected"]
            cid = f"cx-{datetime.now(HK_TZ).strftime('%H%M%S')}"
            _append_cancellation(
                {
                    "id": cid,
                    "created_at": datetime.now(HK_TZ).isoformat(),
                    "phone": state.get("phone"),
                    "parent_name": state.get("parent_name"),
                    "child_name": child_name,
                    "course_key": sel.get("course_key"),
                    "slot_start": sel.get("slot_start"),
                    "slot_display": sel.get("slot_display"),
                    "teacher_id": sel.get("teacher_id"),
                    "session_id": sel.get("session_id") or "",
                    "source_kind": sel.get("kind"),
                    "booking_id": sel.get("booking_id") or "",
                    "notes": "Cancelled via parent chat",
                }
            )
            if sel.get("kind") == "chat_booking" and sel.get("booking_id"):
                _remove_chat_booking(sel["booking_id"])
            clear_state(session_id)
            return {
                "answer": (
                    f"Cancelled for **{child_name}**:\n"
                    f"• {sel.get('slot_display')} — {sel.get('course_label')} "
                    f"with {sel.get('teacher_name')}\n\n"
                    "I've logged it on the cancellations sheet for admin. "
                    "Say if you'd like to rebook another time."
                ),
                "provider": "cancel_dialogue",
                "trace_cancel": {
                    "stage": "cancelled",
                    "child_name": child_name,
                    "cancelled": sel,
                    "cancellation_id": cid,
                },
                "state": None,
            }
        if _ABORT_RE.search(text):
            clear_state(session_id)
            return {
                "answer": "Kept as booked — nothing was cancelled.",
                "provider": "cancel_dialogue",
                "trace_cancel": {"stage": "aborted_confirm"},
                "state": None,
            }
        # Unclear confirm reply — ask again
        sel = state["selected"]
        save_state(session_id, state)
        return {
            "answer": (
                f"Just to confirm — cancel **{child_name}**'s "
                f"**{sel.get('course_label')}** on **{sel.get('slot_display')}** "
                f"with {sel.get('teacher_name')}?\n\n"
                "Reply **yes** to confirm, or tell me a different class."
            ),
            "provider": "cancel_dialogue",
            "trace_cancel": {
                "stage": "confirm",
                "child_name": child_name,
                "selected": sel,
                "reprompt": True,
            },
            "state": state,
        }

    # Select by index from previous candidate list
    if state.get("candidates") and idx is not None:
        pool = state["candidates"]
        if 0 <= idx < len(pool):
            candidates = [pool[idx]]

    if len(candidates) == 1 or (state.get("candidates") and idx is not None and candidates):
        sel = candidates[0]
        state["selected"] = sel
        state["stage"] = "confirm"
        state["candidates"] = candidates
        save_state(session_id, state)
        return {
            "answer": (
                f"Just to confirm — cancel **{child_name}**'s "
                f"**{sel.get('course_label')}** on **{sel.get('slot_display')}** "
                f"with {sel.get('teacher_name')}?\n\n"
                "Reply **yes** to confirm, or tell me a different class."
            ),
            "provider": "cancel_dialogue",
            "trace_cancel": {
                "stage": "confirm",
                "child_name": child_name,
                "selected": sel,
            },
            "state": state,
        }

    # Need clarification — list options
    pool = candidates if candidates else upcoming
    state["candidates"] = pool[:8]
    state["stage"] = "need_which"
    save_state(session_id, state)
    return {
        "answer": _list_options_answer(child_name, pool),
        "provider": "cancel_dialogue",
        "trace_cancel": {
            "stage": "need_which",
            "child_name": child_name,
            "options": len(pool),
        },
        "state": state,
    }
