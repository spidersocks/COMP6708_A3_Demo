"""Direct bot → centre-boss messages (staff inbox), separate from admin_todos.

admin_todos.csv = quiet follow-up sheet when the bot lacked a solid answer.
staff_messages.csv = human-facing inbox when the bot already promised staff will call.
"""
from __future__ import annotations

import csv
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from services import schedule_sheets as sheets

HK_TZ = ZoneInfo("Asia/Hong_Kong")
_LOCK = threading.RLock()

_COLUMNS = [
    ("id", "Ref"),
    ("created_at", "Logged at"),
    ("status", "Status"),
    ("priority", "Priority"),
    ("category", "Topic"),
    ("phone", "Phone"),
    ("parent_name", "Parent"),
    ("child_name", "Child"),
    ("parent_message", "Parent message"),
    ("summary_for_boss", "Subject"),
    ("email_body", "Email body"),
    ("bot_reply", "What we told them"),
]

_KEYS = [k for k, _ in _COLUMNS]
_HEADERS = [h for _, h in _COLUMNS]
_HEADER_TO_KEY = {h.lower(): k for k, h in _COLUMNS}
_HEADER_TO_KEY.update({k.lower(): k for k in _KEYS})
_HEADER_TO_KEY.update(
    {
        "session_id": "_drop",
        "summary_for_boss": "summary_for_boss",
        "parent_message": "parent_message",
        "bot_reply": "bot_reply",
        "topic": "category",
        "subject": "summary_for_boss",
        "what we told them": "bot_reply",
        "parent message": "parent_message",
        "email body": "email_body",
        "email_body": "email_body",
        "logged at": "created_at",
    }
)

CATEGORY_LABELS = {
    "safety": "Child safety / medical concern",
    "angry": "Upset parent / complaint",
    "hard_conflict": "Refund or withdrawal request",
    "ask_human": "Asked to speak with staff",
    "general": "General follow-up",
}

PRIORITY_LABELS = {
    "urgent": "Urgent",
    "high": "High",
    "normal": "Normal",
}

_STATUS_LABELS = {"open": "Open", "ack": "Acknowledged"}


def human_category(category: str) -> str:
    raw = (category or "").strip()
    if not raw:
        return CATEGORY_LABELS["general"]
    return CATEGORY_LABELS.get(raw, raw.replace("_", " ").capitalize())


def human_priority(priority: str) -> str:
    raw = (priority or "normal").strip().lower()
    return PRIORITY_LABELS.get(raw, raw.capitalize() or "Normal")


def human_status(status: str) -> str:
    raw = (status or "open").strip().lower()
    return _STATUS_LABELS.get(raw, raw.capitalize() or "Open")


def _stamp_safe(dt: Optional[datetime] = None) -> str:
    d = dt or datetime.now(HK_TZ)
    day = str(d.day)
    hour = d.strftime("%I").lstrip("0") or "0"
    ampm = d.strftime("%p").lower()
    return f"{day} {d.strftime('%b %Y')}, {hour}:{d.strftime('%M')} {ampm}"


def _path() -> Path:
    return sheets.sheets_dir() / "staff_messages.csv"


def _ensure() -> None:
    path = _path()
    if path.exists():
        return
    with _LOCK:
        sheets.seed_sheets_if_needed()
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=_HEADERS).writeheader()


def _normalize_row(raw: Dict[str, str]) -> Dict[str, str]:
    out = {k: "" for k in _KEYS}
    for hk, val in (raw or {}).items():
        key = _HEADER_TO_KEY.get((hk or "").strip().lower())
        if not key or key == "_drop":
            continue
        out[key] = val if val is not None else ""

    # Status: accept open/ack or human labels
    st = (out.get("status") or "open").strip().lower()
    if st in ("open", "ack"):
        out["status"] = st
    elif st.startswith("ack"):
        out["status"] = "ack"
    else:
        out["status"] = "open"

    # Keep machine keys for priority filters in UI, but expose labels via same fields
    pr = (out.get("priority") or "normal").strip().lower()
    if pr not in PRIORITY_LABELS:
        # Already a label like "High"
        for code, label in PRIORITY_LABELS.items():
            if label.lower() == pr:
                pr = code
                break
        else:
            pr = "normal"
    out["priority"] = pr
    out["priority_label"] = human_priority(pr)

    cat = (out.get("category") or "general").strip()
    cat_l = cat.lower().replace(" ", "_")
    if cat_l in CATEGORY_LABELS:
        out["category"] = cat_l
    else:
        # Match human label back to code when possible
        matched = None
        for code, label in CATEGORY_LABELS.items():
            if label.lower() == cat.lower():
                matched = code
                break
        out["category"] = matched or cat_l or "general"
    out["category_label"] = human_category(out["category"])

    # Refresh legacy machine-ish subjects into staff-facing lines
    subj = (out.get("summary_for_boss") or "").strip()
    legacy_subjects = {
        "Parent asked to speak with a real staff member / manager.",
        "Parent sounds very upset or is escalating a complaint — boss/staff should follow up.",
        "Possible child safety / medical urgency — staff must contact the parent ASAP.",
        "Refund / withdrawal / public-complaint pressure — needs a human decision.",
    }
    if not subj or subj in legacy_subjects:
        defaults = {
            "safety": "Urgent: possible child safety or medical concern — please call the parent now",
            "angry": "Follow up: parent is upset and may escalate a complaint",
            "hard_conflict": "Follow up: refund, withdrawal, or public-complaint request needs a staff decision",
            "ask_human": "Follow up: parent asked to speak with a staff member",
        }
        out["summary_for_boss"] = defaults.get(out["category"], subj or "Follow up: parent needs staff attention")
    return out


def _public_row(row: Dict[str, str]) -> Dict[str, Any]:
    """API payload with human-facing labels for the mailbox UI."""
    item = dict(row)
    item["priority_label"] = human_priority(item.get("priority") or "normal")
    item["category_label"] = human_category(item.get("category") or "general")
    item["status_label"] = human_status(item.get("status") or "open")
    return item


def _write(rows: List[Dict[str, Any]]) -> None:
    _ensure()
    with _LOCK:
        with _path().open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_HEADERS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(
                    {
                        "Ref": row.get("id", ""),
                        "Logged at": row.get("created_at", ""),
                        "Status": human_status(str(row.get("status") or "open")),
                        "Priority": human_priority(str(row.get("priority") or "normal")),
                        "Topic": human_category(str(row.get("category") or "general")),
                        "Phone": row.get("phone", ""),
                        "Parent": row.get("parent_name", ""),
                        "Child": row.get("child_name", ""),
                        "Parent message": row.get("parent_message", ""),
                        "Subject": row.get("summary_for_boss", ""),
                        "Email body": row.get("email_body", ""),
                        "What we told them": row.get("bot_reply", ""),
                    }
                )


def add_message(
    *,
    category: str,
    parent_message: str,
    summary_for_boss: str,
    bot_reply: str = "",
    email_body: str = "",
    priority: str = "normal",
    phone: str = "",
    parent_name: str = "",
    child_name: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    del session_id  # not staff-facing
    _ensure()
    with _LOCK:
        rows = _read()
        subject = (summary_for_boss or "").strip()
        body = (email_body or "").strip()
        item = {
            "id": str(uuid.uuid4())[:8],
            "created_at": _stamp_safe(),
            "status": "open",
            "priority": (priority or "normal").strip().lower(),
            "category": (category or "general").strip(),
            "phone": phone or "",
            "parent_name": parent_name or "",
            "child_name": child_name or "",
            "parent_message": (parent_message or "").strip()[:800],
            "summary_for_boss": subject[:500],
            "email_body": body[:4000],
            "bot_reply": (bot_reply or "").strip()[:800],
        }
        rows.append(item)
        _write(rows)
        return _public_row(item)


def _read() -> List[Dict[str, str]]:
    _ensure()
    path = _path()
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [_normalize_row(r) for r in reader]
    if fieldnames and fieldnames != _HEADERS:
        _write(rows)
    return rows


def list_messages(
    *, status: Optional[str] = "open", limit: int = 50
) -> List[Dict[str, Any]]:
    rows = _read()
    if status:
        want = status.strip().lower()
        rows = [r for r in rows if (r.get("status") or "") == want]
    rows = list(reversed(rows))
    return [_public_row(r) for r in rows[: max(1, min(limit, 200))]]


def acknowledge(message_id: str) -> Optional[Dict[str, Any]]:
    rows = _read()
    found = None
    for r in rows:
        if r.get("id") == message_id:
            r["status"] = "ack"
            found = dict(r)
            break
    if found:
        _write(rows)
        return _public_row(found)
    return None


def clear_messages() -> int:
    _ensure()
    with _LOCK:
        path = _path()
        n = 0
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                n = sum(1 for _ in csv.DictReader(f))
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_HEADERS).writeheader()
        return n
