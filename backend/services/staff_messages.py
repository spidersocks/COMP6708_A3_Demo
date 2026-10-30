"""Direct bot → centre-boss messages (staff inbox), separate from admin_todos.

admin_todos.csv = quiet knowledge-gap flags for staff to research.
staff_messages.csv = explicit notices the bot has already promised a human follow-up for
(angry parent, ask-for-human, safety, etc.).
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

_FIELDS = [
    "id",
    "created_at",
    "status",  # open | ack
    "priority",  # normal | high | urgent
    "category",
    "phone",
    "parent_name",
    "child_name",
    "session_id",
    "parent_message",
    "summary_for_boss",
    "bot_reply",
]


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
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()


def add_message(
    *,
    category: str,
    parent_message: str,
    summary_for_boss: str,
    bot_reply: str = "",
    priority: str = "normal",
    phone: str = "",
    parent_name: str = "",
    child_name: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    _ensure()
    with _LOCK:
        path = _path()
        rows: List[Dict[str, str]] = []
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        item = {
            "id": str(uuid.uuid4())[:8],
            "created_at": datetime.now(HK_TZ).isoformat(),
            "status": "open",
            "priority": priority or "normal",
            "category": (category or "general").strip(),
            "phone": phone or "",
            "parent_name": parent_name or "",
            "child_name": child_name or "",
            "session_id": session_id or "",
            "parent_message": (parent_message or "").strip()[:800],
            "summary_for_boss": (summary_for_boss or "").strip()[:500],
            "bot_reply": (bot_reply or "").strip()[:800],
        }
        rows.append(item)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in _FIELDS})
        return item


def list_messages(
    *, status: Optional[str] = "open", limit: int = 50
) -> List[Dict[str, Any]]:
    _ensure()
    with _path().open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[: max(1, min(limit, 200))]


def acknowledge(message_id: str) -> Optional[Dict[str, Any]]:
    _ensure()
    with _LOCK:
        path = _path()
        with path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        found = None
        for r in rows:
            if r.get("id") == message_id:
                r["status"] = "ack"
                found = dict(r)
                break
        if found:
            with path.open("w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
                w.writeheader()
                for row in rows:
                    w.writerow({k: row.get(k, "") for k in _FIELDS})
        return found


def clear_messages() -> int:
    _ensure()
    with _LOCK:
        path = _path()
        n = 0
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                n = sum(1 for _ in csv.DictReader(f))
        with path.open("w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=_FIELDS).writeheader()
        return n
