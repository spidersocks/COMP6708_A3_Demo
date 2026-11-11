"""WhatsApp-number → family sheet helpers for conversational booking."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from services import schedule_sheets as sheets


def _norm_phone(phone: str) -> str:
    digits = re.sub(r"\D+", "", phone or "")
    if digits.startswith("852") and len(digits) > 8:
        return digits
    if len(digits) == 8:
        return "852" + digits
    return digits


def _read_families() -> List[Dict[str, str]]:
    sheets.seed_sheets_if_needed()
    return sheets._read_csv(sheets.sheets_dir() / "families.csv")  # noqa: SLF001


def list_family_by_phone(phone: str) -> Dict[str, Any]:
    want = _norm_phone(phone)
    rows = [r for r in _read_families() if _norm_phone(r.get("phone", "")) == want]
    if not rows:
        return {"phone": phone, "parent_name": "", "children": [], "matched": False}
    parent = rows[0].get("parent_name") or ""
    children = []
    for r in rows:
        children.append(
            {
                "child_name": r.get("child_name", "").strip(),
                "preferred_courses": [
                    c for c in (r.get("preferred_courses") or "").split("|") if c
                ],
                "notes": r.get("notes") or "",
            }
        )
    return {
        "phone": rows[0].get("phone") or phone,
        "parent_name": parent,
        "children": children,
        "matched": True,
    }


def ensure_family_on_sheet(
    *,
    phone: str,
    parent_name: str = "",
    child_name: str = "",
    preferred_courses: str = "",
    notes: str = "chat booking",
) -> bool:
    """
    Upsert a WhatsApp family row so Staff → Families shows new-parent bookings.
    Returns True when a row was added.
    """
    phone_s = (phone or "").strip()
    parent_s = (parent_name or "").strip()
    child_s = (child_name or "").strip()
    if not phone_s or not child_s:
        return False
    want = _norm_phone(phone_s)
    sheets.seed_sheets_if_needed()
    path = sheets.sheets_dir() / "families.csv"
    fieldnames = ["phone", "parent_name", "child_name", "preferred_courses", "notes"]
    rows = sheets._read_csv(path)  # noqa: SLF001
    for r in rows:
        if _norm_phone(r.get("phone", "")) != want:
            continue
        if (r.get("child_name") or "").strip().lower() == child_s.lower():
            # Refresh parent label if we learned it
            if parent_s and not (r.get("parent_name") or "").strip():
                r["parent_name"] = parent_s
                sheets._write_csv(path, fieldnames, rows)  # noqa: SLF001
            return False
    rows.append(
        {
            "phone": phone_s,
            "parent_name": parent_s,
            "child_name": child_s,
            "preferred_courses": preferred_courses or "",
            "notes": notes or "chat booking",
        }
    )
    sheets._write_csv(path, fieldnames, rows)  # noqa: SLF001
    return True


def list_all_families(*, days_ahead: int = 21) -> Dict[str, Any]:
    """Staff-desk roster: every WhatsApp family + each child's upcoming classes."""
    from services import booking_dialogue as dialogue_svc  # noqa: PLC0415

    sheets.seed_sheets_if_needed()
    rows = _read_families()
    by_phone: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        phone = (r.get("phone") or "").strip()
        key = _norm_phone(phone)
        if not key:
            continue
        bucket = by_phone.get(key)
        if not bucket:
            bucket = {
                "phone": phone,
                "parent_name": (r.get("parent_name") or "").strip(),
                "children": [],
            }
            by_phone[key] = bucket
        child_name = (r.get("child_name") or "").strip()
        if not child_name:
            continue
        upcoming = dialogue_svc.child_upcoming_sessions(
            child_name, days_ahead=days_ahead
        )
        bucket["children"].append(
            {
                "child_name": child_name,
                "preferred_courses": [
                    c for c in (r.get("preferred_courses") or "").split("|") if c
                ],
                "notes": r.get("notes") or "",
                "upcoming": upcoming,
                "upcoming_count": len(upcoming),
            }
        )

    families = sorted(
        by_phone.values(),
        key=lambda f: ((f.get("parent_name") or "").lower(), f.get("phone") or ""),
    )
    return {
        "families": families,
        "count": len(families),
        "sheet": "families.csv",
        "days_ahead": days_ahead,
    }


_COURSE_NAME_BLOCKLIST = {
    "phonics",
    "mandarin",
    "putonghua",
    "writing",
    "math",
    "maths",
    "playgroup",
    "trial",
    "assessment",
    "class",
    "lesson",
    "course",
    "child",
    "kid",
    "son",
    "daughter",
    "baby",
    "parent",
    # Pronouns / fillers — "book for my child" must never yield child_name="my"
    "my",
    "our",
    "his",
    "her",
    "their",
    "me",
    "we",
    "you",
    "us",
    "him",
    "them",
    "this",
    "that",
    "the",
    "a",
    "an",
    "some",
    "any",
    "someone",
    "somebody",
}


def _is_plausible_person_name(name: str) -> bool:
    raw = (name or "").strip()
    if len(raw) < 2:
        return False
    low = raw.lower()
    if low in _COURSE_NAME_BLOCKLIST:
        return False
    if any(tok in _COURSE_NAME_BLOCKLIST for tok in re.split(r"\s+", low)):
        return False
    # Reject all-caps course-looking tokens that aren't multi-word names
    if " " not in raw and not re.search(r"[A-Za-z]", raw):
        return False
    return True


def resolve_child(
    *,
    phone: str,
    mentioned_name: str = "",
    fallback_child: str = "",
) -> Dict[str, Any]:
    """Match a first/full name against the family on this messaging number."""
    family = list_family_by_phone(phone)
    kids = family.get("children") or []
    if not kids:
        # New / unknown WhatsApp — only accept a real person name, never a course word
        name = (mentioned_name or fallback_child or "").strip()
        if name and _is_plausible_person_name(name):
            return {
                "ok": True,
                "ambiguous": False,
                "child": {
                    "child_name": name,
                    "preferred_courses": [],
                    "notes": "ad-hoc (not on family sheet)",
                },
                "family": family,
                "candidates": [],
            }
        return {
            "ok": False,
            "ambiguous": False,
            "child": None,
            "family": family,
            "candidates": [],
            "reason": "need_identity" if not family.get("matched") else "unknown_number",
        }

    needle = (mentioned_name or fallback_child or "").strip().lower()
    if not needle:
        if len(kids) == 1:
            return {
                "ok": True,
                "ambiguous": False,
                "child": kids[0],
                "family": family,
                "candidates": kids,
            }
        return {
            "ok": False,
            "ambiguous": True,
            "child": None,
            "family": family,
            "candidates": kids,
            "reason": "need_child_name",
        }

    # Exact full name, then first-name / token match
    exact = [k for k in kids if k["child_name"].lower() == needle]
    if exact:
        return {
            "ok": True,
            "ambiguous": False,
            "child": exact[0],
            "family": family,
            "candidates": kids,
        }

    partial = []
    for k in kids:
        tokens = re.split(r"\s+", k["child_name"].lower())
        if needle in tokens or any(t.startswith(needle) for t in tokens if len(needle) >= 2):
            partial.append(k)
        elif needle in k["child_name"].lower():
            partial.append(k)

    if len(partial) == 1:
        return {
            "ok": True,
            "ambiguous": False,
            "child": partial[0],
            "family": family,
            "candidates": kids,
        }
    if len(partial) > 1:
        return {
            "ok": False,
            "ambiguous": True,
            "child": None,
            "family": family,
            "candidates": partial,
            "reason": "ambiguous_child",
        }
    return {
        "ok": False,
        "ambiguous": False,
        "child": None,
        "family": family,
        "candidates": kids,
        "reason": "child_not_in_family",
    }


def extract_child_mention(text: str, known_names: List[str]) -> str:
    """Pull a child name from utterances like 'for Jason next week'."""
    t = text or ""
    # Prefer known roster names (longest first)
    for name in sorted(known_names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", t, re.I):
            return name
        first = name.split()[0]
        if len(first) >= 3 and re.search(rf"\b{re.escape(first)}\b", t, re.I):
            return first
    # "for my child" / "for our kid" — kinship, not a name
    if re.search(
        r"\b(?:for|about)\s+(?:my|our|his|her|their)\s+"
        r"(?:child|kid|son|daughter|girl|boy)\b",
        t,
        re.I,
    ):
        return ""
    m = re.search(
        r"\b(?:for|about)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        t,
    )
    if m and _is_plausible_person_name(m.group(1)):
        return m.group(1)
    m = re.search(
        r"\b(?:for|about)\s+([A-Za-z]{2,})\b",
        t,
        re.I,
    )
    if m and _is_plausible_person_name(m.group(1)):
        return m.group(1)
    return ""
