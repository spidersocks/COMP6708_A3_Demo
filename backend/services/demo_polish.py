"""Demo-only cheap LLM polishers: gap refuses, cancel picks, booking parses, query rewrite."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from services import cheap_llm

# Deictic / underspecified follow-ups that need chat context to retrieve well.
_CONTEXTUAL_QUERY_RE = re.compile(
    r"(?i)\b("
    r"that|this|it|those|these|them|"
    r"more\s+about|tell\s+me\s+more|can\s+you\s+expand|"
    r"what\s+about|how\s+about|and\s+the|the\s+other|"
    r"same\s+(one|thing)|as\s+well|too\b|also\b|"
    r"you\s+(said|mentioned|told)|earlier|above|"
    r"the\s+(first|second|third|last)\s+one|"
    r"那個|這個|那個呢|呢個|多啲|詳細|再說|上面|剛才"
    r")\b"
)


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
    draft_s = (draft or "").strip()
    # Already-fluent DashScope answers must NOT pay for a second full generation.
    soft_gap = gap_reason in {
        "no_retrieval",
        "model_no_answer",
        "generic_fallback",
        "model_admin_gap",
    }
    if not soft_gap and len(draft_s) >= 140:
        out["reason"] = "skip_fluent"
        return out
    if len(draft_s) >= 320 and "admin to-do" not in draft_s.lower():
        out["reason"] = "skip_fluent"
        return out
    out["called"] = True
    prompt = (
        "Polish the draft parent reply: warm, short, keep existing facts, "
        "no invented fees/teachers, no internal tools/ADMIN_GAP. "
        "Empty refuse → brief 'we don't have that yet' + staff callback invite.\n"
        f"Lang: {lang} · gap: {gap_reason}\n"
        f"Parent: {question}\n"
        f"Draft:\n{draft}\n"
        "Reply:"
    )
    text = cheap_llm.complete_text(
        prompt,
        system="Parent-facing BrightNest reply only.",
        max_tokens=180,
        temperature=0.3,
        role="gap_polish",
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
        "Cancel pick from numbered options.\n"
        'JSON: {"action":"pick"|"none"|"ambiguous","index":1-based-or-null,"reason":"short"}\n'
        f"Options:\n" + "\n".join(lines) + "\n"
        f"Parent: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=60, role="cancel_pick")
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
        "Extract booking hints.\n"
        "course_key: phonics|mandarin|writing|playgroup|math|trial|null; "
        "weekday 0=Mon..6=Sun|null; window tomorrow|this_week|next_week|null; "
        "teacher_hint short|null.\n"
        'JSON: {"course_key":..,"weekday":..,"window":..,"teacher_hint":..,"confidence":0-1}\n'
        f"Parent: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=80, role="booking_parse")
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


def needs_retrieval_rewrite(message: str, history_context: Optional[str]) -> bool:
    """True when the ask likely depends on prior turns to be searchable."""
    if not (history_context or "").strip():
        return False
    msg = (message or "").strip()
    if not msg:
        return False
    if _CONTEXTUAL_QUERY_RE.search(msg):
        return True
    # Very short follow-ups with history ("fees?", "and math?")
    tokens = re.findall(r"[A-Za-z\u4e00-\u9fff]+", msg)
    return len(tokens) <= 6


def rewrite_retrieval_query(
    message: str,
    history_context: Optional[str],
    *,
    lang: str = "en",
) -> Dict[str, Any]:
    """
    Cheap rewrite: turn a context-dependent parent ask into a standalone
    retrieval query. Fail-open → original message.
    """
    original = (message or "").strip()
    out: Dict[str, Any] = {
        "rewritten": False,
        "called": False,
        "needed": False,
        "reason": "skipped",
        "original": original,
        "query": original,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
    }
    if not SETTINGS.arbiter_query_rewrite:
        out["reason"] = "disabled"
        return out
    if not needs_retrieval_rewrite(original, history_context):
        out["reason"] = "standalone"
        return out
    out["needed"] = True
    if not cheap_llm.enabled():
        out["reason"] = "no_llm"
        return out
    hist = (history_context or "").strip()
    if len(hist) > 1600:
        hist = hist[-1600:]
    out["called"] = True
    prompt = (
        "Rewrite the parent's latest message into ONE standalone search query "
        "for a centre knowledge base. Resolve pronouns like that/this/it using "
        "the chat. Keep the same language as the parent. Output ONLY the query "
        "text — no quotes, no preamble.\n"
        f"Lang: {lang}\n"
        f"Chat:\n{hist}\n\n"
        f"Latest: {original}\n"
        "Standalone query:"
    )
    text = cheap_llm.complete_text(
        prompt,
        system="Output a single standalone retrieval query only.",
        max_tokens=80,
        temperature=0.1,
        role="query_rewrite",
    )
    cleaned = (text or "").strip().strip('"').strip("'")
    if not cleaned or len(cleaned) < 4:
        out["reason"] = "empty"
        return out
    # Guard: model must not dump the whole chat back
    if len(cleaned) > 280:
        cleaned = cleaned[:280].rsplit(" ", 1)[0] or cleaned[:280]
    if cleaned.lower() == original.lower():
        out.update({"reason": "unchanged", "query": original})
        return out
    out.update(
        {
            "rewritten": True,
            "reason": "ok",
            "query": cleaned,
        }
    )
    return out
