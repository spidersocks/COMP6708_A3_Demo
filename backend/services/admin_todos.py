"""Admin follow-up to-do list — flagged when the bot lacks a solid answer.

Stored as a staff-facing CSV under data/sheets/admin_todos.csv (Excel / Sheets).
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

# Internal key → CSV header (human-facing when opened in Excel)
_COLUMNS = [
    ("id", "Ref"),
    ("created_at", "Logged at"),
    ("status", "Status"),
    ("phone", "Phone"),
    ("parent_name", "Parent"),
    ("child_name", "Child"),
    ("question", "Parent question"),
    ("reply", "What we told them"),
    ("info_needed", "What to follow up"),
    ("reason", "Why flagged"),
]

_KEYS = [k for k, _ in _COLUMNS]
_HEADERS = [h for _, h in _COLUMNS]
_HEADER_TO_KEY = {h.lower(): k for k, h in _COLUMNS}
# Legacy snake_case headers from earlier demo runs
_HEADER_TO_KEY.update({k.lower(): k for k in _KEYS})
_HEADER_TO_KEY.update(
    {
        "session_id": "_drop",
        "top_score": "_drop",
        "hit_count": "_drop",
        "parent_name": "parent_name",
        "child_name": "child_name",
        "info_needed": "info_needed",
        "created_at": "created_at",
    }
)

REASON_LABELS = {
    "knowledge_gap": "Missing centre information",
    "unknown_programme": "Asked about a programme we don't offer",
    "no_retrieval": "No matching centre materials found",
    "weak_retrieval": "Only a weak match in centre materials",
    "off_topic_retrieval": "Centre materials didn't match the question",
    "weak_extractive": "Auto-answer looked incomplete — please confirm",
    "model_no_answer": "Assistant couldn't answer from centre materials",
    "model_admin_gap": "Needs a staff decision or private record",
    "generic_fallback": "Only a generic reply was available",
    "assessment_request": "Arrange diagnostic / placement assessment",
    "callback_request": "Parent expects a staff callback",
}

_STATUS_LABELS = {"open": "Open", "done": "Done"}


def human_reason(reason: str) -> str:
    raw = (reason or "").strip()
    if not raw:
        return "Needs staff follow-up"
    return REASON_LABELS.get(raw, raw.replace("_", " ").capitalize())


def human_status(status: str) -> str:
    raw = (status or "open").strip().lower()
    return _STATUS_LABELS.get(raw, raw.capitalize() or "Open")


def _stamp_safe(dt: Optional[datetime] = None) -> str:
    """Human timestamp for staff sheets (Windows-safe)."""
    d = dt or datetime.now(HK_TZ)
    day = str(d.day)
    hour = d.strftime("%I").lstrip("0") or "0"
    ampm = d.strftime("%p").lower()
    return f"{day} {d.strftime('%b %Y')}, {hour}:{d.strftime('%M')} {ampm}"


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
                csv.DictWriter(f, fieldnames=_HEADERS).writeheader()


def _normalize_row(raw: Dict[str, str]) -> Dict[str, str]:
    out = {k: "" for k in _KEYS}
    for hk, val in (raw or {}).items():
        key = _HEADER_TO_KEY.get((hk or "").strip().lower())
        if not key or key == "_drop":
            continue
        out[key] = val if val is not None else ""
    out["reason"] = human_reason(out.get("reason") or "")
    st = (out.get("status") or "open").strip().lower()
    if st in ("open", "done"):
        out["status"] = st
    else:
        # Already human label from CSV
        low = st.lower()
        out["status"] = "done" if low.startswith("done") else "open"

    # Soften leftover internal jargon in older rows
    note = out.get("info_needed") or ""
    replacements = [
        ("Quiet flag only (parent was not told about the to-do sheet).", ""),
        ("Quiet flag only", ""),
        ("No KB chunks retrieved — may need a new FAQ/policy doc.",
         "We have nothing on file for this — please confirm the answer or add it to centre FAQs."),
        ("Retrieval scores were weak — confirm whether this topic belongs in the KB.",
         "Our materials only loosely match — please confirm the correct answer for this parent."),
        ("Top KB hit looks unrelated to the question — need a real answer or new FAQ.",
         "Our published materials don't cover this — please advise what to tell the parent."),
        ("Only a weak extractive FAQ dump was available — staff should confirm the answer.",
         "The auto-reply looked incomplete — please confirm the correct details."),
        ("Model signalled it could not answer from excerpts.",
         "The assistant couldn't answer from centre materials — staff clarification needed."),
        ("Model flagged an explicit admin gap.",
         "This needs a staff decision or a note from the child's private record."),
        (" (nearest KB:", " (nearest material:"),
    ]
    for old, new in replacements:
        note = note.replace(old, new)
    out["info_needed"] = " ".join(note.split())
    return out


def _read() -> List[Dict[str, str]]:
    _ensure()
    path = _path()
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [_normalize_row(r) for r in reader]
    # Rewrite legacy snake_case sheets into human-facing headers
    if fieldnames and fieldnames != _HEADERS:
        _write(rows)
    return rows


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
                        "Phone": row.get("phone", ""),
                        "Parent": row.get("parent_name", ""),
                        "Child": row.get("child_name", ""),
                        "Parent question": row.get("question", ""),
                        "What we told them": row.get("reply", ""),
                        "What to follow up": row.get("info_needed", ""),
                        "Why flagged": human_reason(str(row.get("reason") or "")),
                    }
                )


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
    """Append one open to-do (dedupe same phone+question while still open)."""
    del session_id, top_score, hit_count  # kept for call-site compat; not staff-facing
    q_norm = (question or "").strip().lower()
    phone_norm = (phone or "").strip()
    with _LOCK:
        rows = _read()
        for r in rows:
            if (
                r.get("status") == "open"
                and (r.get("phone") or "") == phone_norm
                and (r.get("question") or "").strip().lower() == q_norm
            ):
                return dict(r)
        item = {
            "id": str(uuid.uuid4())[:8],
            "created_at": _stamp_safe(),
            "status": "open",
            "phone": phone or "",
            "parent_name": parent_name or "",
            "child_name": child_name or "",
            "question": (question or "").strip(),
            "reply": (reply or "").strip()[:800],
            "info_needed": (info_needed or "").strip()[:400],
            "reason": human_reason(reason),
        }
        rows.append(item)
        _write(rows)
        return item


def list_todos(*, status: Optional[str] = "open", limit: int = 50) -> List[Dict[str, Any]]:
    rows = _read()
    if status:
        want = status.strip().lower()
        rows = [r for r in rows if (r.get("status") or "").lower() == want]
    # File order is chronological; newest were appended last
    rows = list(reversed(rows))
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


_EDITABLE = ("info_needed", "parent_name", "child_name")


def update_todo(todo_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """Patch editable fields on an open to-do (demo Staff desk sheet)."""
    rows = _read()
    found = None
    for r in rows:
        if r.get("id") != todo_id:
            continue
        if r.get("status") != "open":
            return None
        for key in _EDITABLE:
            if key not in fields or fields[key] is None:
                continue
            val = str(fields[key]).strip()
            if key == "info_needed":
                val = val[:400]
            else:
                val = val[:120]
            r[key] = val
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
            csv.DictWriter(f, fieldnames=_HEADERS).writeheader()
        return n


def infer_info_needed(question: str, hits: List[Dict[str, Any]], reason: str) -> str:
    """Plain-language note for staff — no internal RAG jargon."""
    q = (question or "").lower()
    hints = []
    if reason == "no_retrieval":
        hints.append("We have nothing on file for this — please confirm the answer or add it to centre FAQs.")
    elif reason == "weak_retrieval":
        hints.append("Our materials only loosely match — please confirm the correct answer for this parent.")
    elif reason == "off_topic_retrieval":
        hints.append("Our published materials don't cover this — please advise what to tell the parent.")
    elif reason == "weak_extractive":
        hints.append("The auto-reply looked incomplete — please confirm the correct details.")
    elif reason == "model_no_answer":
        hints.append("The assistant couldn't answer from centre materials — staff clarification needed.")
    elif reason == "model_admin_gap":
        hints.append("This needs a staff decision or a note from the child's private record.")
    elif reason == "generic_fallback":
        hints.append("Only a generic reply was sent — please follow up with a clear answer.")
    elif reason == "unknown_programme":
        hints.append("Parent asked about a programme we don't offer — confirm referral options if any.")

    if any(w in q for w in ("fee", "price", "cost", "tuition", "學費", "学费", "幾錢", "多少钱")):
        hints.append("Please confirm exact fees / package pricing for the programme they asked about.")
    if any(w in q for w in ("allergy", "medical", "asthma", "過敏", "过敏")):
        hints.append("Check the child's medical / allergy notes on the register.")
    if any(w in q for w in ("homework", "feedback", "progress", "功課", "进度")):
        hints.append("Ask the teacher for a short progress note for this child.")
    if any(w in q for w in ("discount", "scholarship", "sponsor")):
        hints.append("Confirm whether any discount or scholarship can apply.")
    if any(w in q for w in ("private", "1:1", "one-to-one", "一對一", "一对一")):
        hints.append("Confirm private tuition rates and teacher availability.")
    if not hints:
        hints.append(
            f"Please clarify how we should answer: “{(question or '')[:120]}”"
        )
    return " ".join(hints)[:400]
