"""Admin follow-up to-do list — flagged when the bot lacks a solid answer.

Stored as CSV under data/sheets/admin_todos.csv so staff can open it like a sheet.
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
    "status",  # open | done
    "phone",
    "parent_name",
    "child_name",
    "session_id",
    "question",
    "reply",
    "info_needed",
    "reason",
    "top_score",
    "hit_count",
]


def _path() -> Path:
    root = sheets.sheets_dir()
    return root / "admin_todos.csv"


def _ensure() -> None:
    path = _path()
    if path.exists():
        return
    with _LOCK:
        sheets.seed_sheets_if_needed()
        if not path.exists():
            with path.open("w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=_FIELDS).writeheader()


def _read() -> List[Dict[str, str]]:
    _ensure()
    with _path().open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write(rows: List[Dict[str, Any]]) -> None:
    _ensure()
    with _LOCK:
        with _path().open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in _FIELDS})


def add_todo(
    *,
    question: str,
    reply: str,
    info_needed: str = "",
    reason: str = "knowledge_gap",
    phone: str = "",
    parent_name: str = "",
    child_name: str = "",
    session_id: str = "",
    top_score: Optional[float] = None,
    hit_count: int = 0,
) -> Dict[str, Any]:
    """Append one open to-do (dedupe same session+question while still open)."""
    _ensure()
    q_norm = (question or "").strip().lower()
    with _LOCK:
        rows = []
        path = _path()
        if path.exists():
            with path.open("r", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        for r in rows:
            if (
                r.get("status") == "open"
                and (r.get("session_id") or "") == (session_id or "")
                and (r.get("question") or "").strip().lower() == q_norm
            ):
                return dict(r)
        item = {
            "id": str(uuid.uuid4())[:8],
            "created_at": datetime.now(HK_TZ).isoformat(),
            "status": "open",
            "phone": phone or "",
            "parent_name": parent_name or "",
            "child_name": child_name or "",
            "session_id": session_id or "",
            "question": (question or "").strip(),
            "reply": (reply or "").strip()[:800],
            "info_needed": (info_needed or "").strip()[:400],
            "reason": reason,
            "top_score": "" if top_score is None else f"{top_score:.4f}",
            "hit_count": str(hit_count),
        }
        rows.append(item)
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_FIELDS, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow({k: row.get(k, "") for k in _FIELDS})
        return item


def list_todos(*, status: Optional[str] = "open", limit: int = 50) -> List[Dict[str, Any]]:
    rows = _read()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows[: max(1, min(limit, 200))]


def complete_todo(todo_id: str) -> Optional[Dict[str, Any]]:
    rows = _read()
    found = None
    for r in rows:
        if r.get("id") == todo_id:
            r["status"] = "done"
            found = dict(r)
            break
    if found:
        _write(rows)
    return found


def clear_todos() -> int:
    """Wipe the admin to-do sheet (demo Reset). Returns previous row count."""
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


def infer_info_needed(question: str, hits: List[Dict[str, Any]], reason: str) -> str:
    q = (question or "").lower()
    hints = []
    if reason == "no_retrieval":
        hints.append("No KB chunks retrieved — may need a new FAQ/policy doc.")
    elif reason == "weak_retrieval":
        hints.append("Retrieval scores were weak — confirm whether this topic belongs in the KB.")
    elif reason == "off_topic_retrieval":
        hints.append("Top KB hit looks unrelated to the question — need a real answer or new FAQ.")
    elif reason == "weak_extractive":
        hints.append("Only a weak extractive FAQ dump was available — staff should confirm the answer.")
    elif reason == "model_no_answer":
        hints.append("Model signalled it could not answer from excerpts.")
    elif reason == "model_admin_gap":
        hints.append("Model flagged an explicit admin gap.")
    elif reason == "generic_fallback":
        hints.append("Only a generic fallback reply was available.")

    if any(w in q for w in ("fee", "price", "cost", "tuition", "學費", "学费", "幾錢", "多少钱")):
        hints.append("Exact fee / package pricing for the named programme.")
    if any(w in q for w in ("allergy", "medical", "asthma", "過敏", "过敏")):
        hints.append("Child-specific medical / allergy note on the register.")
    if any(w in q for w in ("homework", "feedback", "progress", "功課", "进度")):
        hints.append("Teacher progress note for this child (not in public FAQ).")
    if any(w in q for w in ("discount", "scholarship", "sponsor")):
        hints.append("Whether a discretionary discount / scholarship applies.")
    if any(w in q for w in ("private", "1:1", "one-to-one", "一對一", "一对一")):
        hints.append("Private tuition rate card / teacher availability.")
    if not hints:
        top = (hits[0].get("title") or hits[0].get("source") or "") if hits else ""
        hints.append(
            f"Staff clarification on: “{(question or '')[:120]}”"
            + (f" (nearest KB: {top})" if top else "")
        )
    return " ".join(hints)[:400]
