"""Demo-only cheap LLM polishers: gap refuses, cancel picks, booking parses."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from config import SETTINGS
from services import cheap_llm


def polish_gap_reply(
    question: str,
    draft: str,
    *,
    gap_reason: str,
    lang: str = "en",
) -> Dict[str, Any]:
    """Turn awkward soft-refuses into a fluent parent reply; keep substance when present."""
    out: Dict[str, Any] = {
        "answer": draft,
        "polished": False,
        "reason": "skipped",
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
    }
    if not SETTINGS.arbiter_gap_rewrite or not cheap_llm.enabled():
        return out
    out["called"] = True
    prompt = (
        "BrightNest COMP6708 live demo — rewrite the draft parent reply so it sounds "
        "like a sharp centre admin, not a broken bot.\n"
        "Rules:\n"
        "- Keep any concrete centre facts already in the draft.\n"
        "- Do not invent fees, schedules, or teachers.\n"
        "- If the draft is an empty soft-refuse, briefly say we don't have that yet "
        "and invite them to ask for a staff callback — warm, short.\n"
        "- Do not mention admin to-dos, spreadsheets, RAG, or internal tools.\n"
        "- No [[ADMIN_GAP]] tags.\n\n"
        f"Language hint: {lang}\n"
        f"Gap reason: {gap_reason}\n"
        f"Parent question: {question}\n"
        f"Draft reply:\n---\n{draft}\n---\n"
        "Polished reply:"
    )
    text = cheap_llm.complete_text(
        prompt,
        system="Parent-facing BrightNest reply only. No JSON.",
        max_tokens=180,
        temperature=0.3,
    )
    if not text or len(text.strip()) < 20:
        out["reason"] = "empty"
        return out
    out.update({"answer": text.strip(), "polished": True, "reason": "ok"})
    return out


def pick_cancel_candidate(
    message: str,
    candidates: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    Choose which listed cancel option the parent meant.
    Returns {action: pick|none|ambiguous, index: int|None, reason}.
    index is 0-based into candidates.
    """
    out: Dict[str, Any] = {
        "action": "none",
        "index": None,
        "reason": "skipped",
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
    }
    if not SETTINGS.arbiter_cancel_pick or not cheap_llm.enabled():
        return out
    if not candidates:
        return out
    out["called"] = True
    lines = []
    for i, c in enumerate(candidates[:8], 1):
        lines.append(
            f"{i}. {c.get('slot_display')} — {c.get('course_label')} · {c.get('teacher_name')}"
        )
    prompt = (
        "Parent is cancelling a BrightNest class. Options were listed numbered.\n"
        "Pick which option they mean, or none/ambiguous.\n"
        'JSON only: {"action":"pick"|"none"|"ambiguous","index":1-based-or-null,"reason":"short"}\n\n'
        f"Options:\n" + "\n".join(lines) + "\n\n"
        f"Parent message: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=60)
    if not parsed:
        out["reason"] = "parse_fail"
        return out
    action = str(parsed.get("action") or "none").lower()
    reason = str(parsed.get("reason") or "")[:120]
    idx_raw = parsed.get("index")
    try:
        idx1 = int(idx_raw) if idx_raw is not None else None
    except (TypeError, ValueError):
        idx1 = None
    base = {"called": True, "model": out["model"]}
    if action == "pick" and idx1 is not None and 1 <= idx1 <= len(candidates):
        return {**base, "action": "pick", "index": idx1 - 1, "reason": reason or "pick"}
    if action == "ambiguous":
        return {**base, "action": "ambiguous", "index": None, "reason": reason or "ambiguous"}
    return {**base, "action": "none", "index": None, "reason": reason or "none"}


def parse_booking_hints(message: str) -> Dict[str, Any]:
    """
    Soft extract course / weekday / window from a booking clarify utterance.
    Returns keys among: course_key, weekday (0=Mon), window, teacher_hint, confidence.
    """
    empty: Dict[str, Any] = {
        "ok": False,
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
    }
    if not SETTINGS.arbiter_booking_parse or not cheap_llm.enabled():
        return empty
    prompt = (
        "Extract booking constraints for BrightNest kids centre.\n"
        "Courses: phonics, mandarin, writing, playgroup, math, trial (or null).\n"
        "weekday: 0=Mon .. 6=Sun or null.\n"
        "window: tomorrow|this_week|next_week|null.\n"
        "teacher_hint: short name token or null.\n"
        'JSON only: {"course_key":..,"weekday":..,"window":..,"teacher_hint":..,"confidence":0-1}\n\n'
        f"Parent message: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=80)
    if not parsed:
        return {**empty, "called": True, "reason": "parse_fail"}
    allowed_courses = {"phonics", "mandarin", "writing", "playgroup", "math", "trial"}
    course = parsed.get("course_key")
    if course not in allowed_courses:
        course = None
    weekday = parsed.get("weekday")
    try:
        weekday_i = int(weekday) if weekday is not None else None
        if weekday_i is not None and not (0 <= weekday_i <= 6):
            weekday_i = None
    except (TypeError, ValueError):
        weekday_i = None
    window = parsed.get("window")
    if window not in {"tomorrow", "this_week", "next_week"}:
        window = None
    teacher = parsed.get("teacher_hint")
    if teacher is not None:
        teacher = str(teacher).strip() or None
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    if not any([course, weekday_i is not None, window, teacher]) or conf < 0.45:
        return {
            "ok": False,
            "called": True,
            "confidence": conf,
            "model": empty["model"],
            "reason": "low_confidence",
        }
    return {
        "ok": True,
        "called": True,
        "course_key": course,
        "weekday": weekday_i,
        "window": window,
        "teacher_hint": teacher,
        "confidence": conf,
        "model": empty["model"],
    }
