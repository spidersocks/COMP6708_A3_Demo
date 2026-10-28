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
        # Fall back to free-text name if family sheet miss
        name = (mentioned_name or fallback_child or "").strip()
        if name:
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
            "reason": "unknown_number",
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
    m = re.search(
        r"\b(?:for|about)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\b",
        t,
    )
    if m:
        return m.group(1)
    m = re.search(
        r"\b(?:for|about)\s+([A-Za-z]{2,})\b",
        t,
        re.I,
    )
    if m and m.group(1).lower() not in {
        "a",
        "the",
        "my",
        "our",
        "class",
        "lesson",
        "trial",
        "next",
        "this",
        "him",
        "her",
    }:
        return m.group(1)
    return ""
