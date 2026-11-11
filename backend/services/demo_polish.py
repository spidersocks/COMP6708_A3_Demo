"""Demo-only cheap LLM polishers: gap refuses, cancel picks, booking parses, query rewrite."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from config import SETTINGS
from services import cheap_llm

# Deictic / underspecified follow-ups that need *prior* chat to retrieve well.
# Do NOT match standalone "know more about your math programmes" (no deixis).
_CONTEXTUAL_QUERY_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:tell\s+me\s+)?more\s+about\s+(?:that|this|it|them|those|these)\b|"
    r"\btell\s+me\s+more\b|"
    r"\bcan\s+you\s+expand\b|"
    r"\b(?:what|how)\s+about\s+(?:that|this|it|them|those|these)\b|"
    r"\b(?:and|also)\s+(?:that|this|it|them|the\s+other)\b|"
    r"\bthe\s+other\s+(?:one|class|course|programme|program)\b|"
    r"\bthe\s+(?:first|second|third|last)\s+one\b|"
    r"\byou\s+(?:said|mentioned|told)\b|"
    r"\b(?:earlier|above)\b|"
    r"\b(?:that|this|those|these)\s+(?:one|class|course|programme|program|fee|option)\b|"
    r"那個|這個|那個呢|呢個|多啲|詳細啲|再說|上面|剛才"
    r")"
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


_ALLOWED_COURSES = {"phonics", "mandarin", "writing", "playgroup", "math", "trial"}
_KINSHIP_NAME_BLOCK = {
    "my",
    "our",
    "his",
    "her",
    "their",
    "child",
    "kid",
    "son",
    "daughter",
    "boy",
    "girl",
    "parent",
    "mum",
    "mom",
    "dad",
    "father",
    "mother",
}


def _clean_person_slot(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = str(raw).strip(" .,;:!?")
    if not s or s.lower() in {"null", "none", "n/a", "-"}:
        return None
    s = re.sub(r"^(?:named|called)\s+", "", s, flags=re.I).strip()
    low = s.lower()
    if low in _KINSHIP_NAME_BLOCK or low in {"named", "called"}:
        return None
    if any(tok in _KINSHIP_NAME_BLOCK for tok in re.split(r"\s+", low)):
        return None
    # Prefer Title Case for single/multi English names
    if s.islower() or s.isupper():
        s = s.title()
    return s


def _parse_weekday_slot(raw: Any) -> Optional[int]:
    try:
        weekday_i = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
    if weekday_i is not None and 0 <= weekday_i <= 6:
        return weekday_i
    return None


def track_booking_slots(
    message: str,
    *,
    prior: Optional[Dict[str, Any]] = None,
    missing: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Cheap-LLM Dialogue State Tracking for booking.

    Schema-constrained JSON extract over the latest parent turn (+ known slots).
    Fills only what the utterance supports: parent_name, child_name, course_key,
    weekday, window, teacher_hint. Caller merges into FSM state (never wipe
    untouched slots).
    """
    empty: Dict[str, Any] = {
        "ok": False,
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
        "role": "booking_dst",
        "how": [
            "Regex / detectors run first (fast path).",
            "If slots still missing: cheap LLM returns structured JSON for this turn.",
            "Merge only filled fields; reject kinship pronouns as names.",
            "Booking FSM still decides what to ask next and when to book.",
        ],
    }
    if not SETTINGS.arbiter_booking_parse or not cheap_llm.enabled():
        return {**empty, "reason": "disabled"}
    prior = dict(prior or {})
    miss = [m for m in (missing or []) if m]
    prior_compact = {
        k: prior.get(k)
        for k in (
            "parent_name",
            "child_name",
            "course_key",
            "weekday",
            "window",
            "teacher_hint",
            "stage",
        )
        if prior.get(k) not in (None, "", [])
    }
    prompt = (
        "Dialogue state tracking for a kids' learning-centre booking chat.\n"
        "Extract ONLY values clearly stated in the latest parent message. "
        "Do not invent names. Kinship words (my/kid/child/son) are NOT names — use null.\n"
        "course_key: phonics|mandarin|writing|playgroup|math|trial|null; "
        "weekday 0=Mon..6=Sun|null; window tomorrow|this_week|next_week|null.\n"
        "Return null for any slot not supported by this utterance.\n"
        f"Already known (do not clear): {prior_compact or '{}'}\n"
        f"Still missing (prefer filling these): {miss or ['any stated']}\n"
        'JSON: {"parent_name":..,"child_name":..,"course_key":..,"weekday":..,'
        '"window":..,"teacher_hint":..,"confidence":0-1,"notes":"short"}\n'
        f"Parent: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=140, role="booking_dst")
    if not parsed:
        return {**empty, "called": True, "reason": "parse_fail"}
    course = parsed.get("course_key")
    if course not in _ALLOWED_COURSES:
        course = None
    weekday_i = _parse_weekday_slot(parsed.get("weekday"))
    window = parsed.get("window")
    if window not in {"tomorrow", "this_week", "next_week"}:
        window = None
    teacher = parsed.get("teacher_hint")
    if teacher is not None:
        teacher = str(teacher).strip() or None
        if teacher and teacher.lower() in {"null", "none"}:
            teacher = None
    parent = _clean_person_slot(parsed.get("parent_name"))
    child = _clean_person_slot(parsed.get("child_name"))
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    filled = {
        "parent_name": parent,
        "child_name": child,
        "course_key": course,
        "weekday": weekday_i,
        "window": window,
        "teacher_hint": teacher,
    }
    # Drop empties for merge clarity
    filled = {k: v for k, v in filled.items() if v is not None and v != ""}
    if not filled or conf < 0.40:
        return {
            **empty,
            "called": True,
            "ok": False,
            "confidence": conf,
            "filled": {},
            "raw": parsed,
            "reason": "low_confidence",
            "missing_asked": miss,
            "prior": prior_compact,
        }
    return {
        "ok": True,
        "called": True,
        "filled": filled,
        "confidence": conf,
        "notes": str(parsed.get("notes") or "")[:160],
        "model": empty["model"],
        "role": "booking_dst",
        "how": empty["how"],
        "missing_asked": miss,
        "prior": prior_compact,
        # Back-compat keys used by older booking_parse merge
        "course_key": course,
        "weekday": weekday_i,
        "window": window,
        "teacher_hint": teacher,
        "parent_name": parent,
        "child_name": child,
    }


def parse_booking_hints(message: str) -> Dict[str, Any]:
    """Back-compat wrapper — full DST lives in track_booking_slots."""
    return track_booking_slots(message, missing=["course_key", "weekday", "window", "teacher_hint"])


def track_cancel_slots(
    message: str,
    *,
    known_children: Optional[List[str]] = None,
    prior: Optional[Dict[str, Any]] = None,
    missing: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Cheap-LLM DST for cancel dialogue: child_name / course / when cues when
    regex + roster match miss.
    """
    empty: Dict[str, Any] = {
        "ok": False,
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
        "role": "cancel_dst",
        "how": [
            "Roster name match / regex first.",
            "If child or filters still unclear: cheap LLM structured JSON.",
            "Merge into cancel FSM; pick/confirm stays deterministic (or cancel_pick).",
        ],
    }
    # Reuse booking parse flag — same cheap NLU budget
    if not SETTINGS.arbiter_booking_parse or not cheap_llm.enabled():
        return {**empty, "reason": "disabled"}
    kids = [str(k).strip() for k in (known_children or []) if str(k).strip()]
    prior = dict(prior or {})
    miss = [m for m in (missing or []) if m]
    prior_compact = {
        k: prior.get(k)
        for k in ("child_name", "course_key", "weekday", "stage")
        if prior.get(k) not in (None, "", [])
    }
    prompt = (
        "Dialogue state tracking for cancelling a class at a kids' centre.\n"
        "Extract ONLY values clearly stated. Do not invent child names.\n"
        f"Known children on this WhatsApp (prefer exact match): {kids or ['(none)']}\n"
        "course_key: phonics|mandarin|writing|playgroup|math|trial|null; "
        "weekday 0=Mon..6=Sun|null.\n"
        f"Already known: {prior_compact or '{}'}\n"
        f"Still missing: {miss or ['any stated']}\n"
        'JSON: {"child_name":..,"course_key":..,"weekday":..,"confidence":0-1,"notes":"short"}\n'
        f"Parent: {message}\n"
    )
    parsed = cheap_llm.complete_json(prompt, max_tokens=120, role="cancel_dst")
    if not parsed:
        return {**empty, "called": True, "reason": "parse_fail"}
    child = _clean_person_slot(parsed.get("child_name"))
    if child and kids:
        # Prefer canonical roster casing when fuzzy-equal
        for k in kids:
            if k.lower() == child.lower() or k.split()[0].lower() == child.lower():
                child = k
                break
    course = parsed.get("course_key")
    if course not in _ALLOWED_COURSES:
        course = None
    weekday_i = _parse_weekday_slot(parsed.get("weekday"))
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    filled = {
        k: v
        for k, v in {
            "child_name": child,
            "course_key": course,
            "weekday": weekday_i,
        }.items()
        if v is not None and v != ""
    }
    if not filled or conf < 0.40:
        return {
            **empty,
            "called": True,
            "ok": False,
            "confidence": conf,
            "filled": {},
            "reason": "low_confidence",
            "missing_asked": miss,
            "prior": prior_compact,
        }
    return {
        "ok": True,
        "called": True,
        "filled": filled,
        "confidence": conf,
        "notes": str(parsed.get("notes") or "")[:160],
        "model": empty["model"],
        "role": "cancel_dst",
        "how": empty["how"],
        "missing_asked": miss,
        "prior": prior_compact,
        "child_name": child,
        "course_key": course,
        "weekday": weekday_i,
    }


def _prior_history_only(
    history_context: Optional[str],
    message: str,
) -> str:
    """
    Drop the current inbound line if the caller passed include_new=True context.
    Rewrite must only see *prior* turns — never treat turn-1 as its own history.
    """
    hist = (history_context or "").strip()
    if not hist:
        return ""
    msg = (message or "").strip()
    if not msg:
        return hist
    lines = [ln for ln in hist.splitlines() if ln.strip()]
    # Strip trailing "Parent: <this message>"
    if lines:
        last = lines[-1]
        if last.startswith("Parent:") and msg.lower() in last.lower():
            lines = lines[:-1]
    prior = "\n".join(lines).strip()
    if not prior:
        return ""
    if prior.lower() in {msg.lower(), f"parent: {msg.lower()}"}:
        return ""
    return prior


def needs_retrieval_rewrite(message: str, history_context: Optional[str]) -> bool:
    """Heuristic: deictic / very short follow-up given real prior turns."""
    msg = (message or "").strip()
    if not msg:
        return False
    prior = _prior_history_only(history_context, msg)
    if not prior:
        return False
    if _CONTEXTUAL_QUERY_RE.search(msg):
        return True
    tokens = re.findall(r"[A-Za-z\u4e00-\u9fff]+", msg)
    return len(tokens) <= 6


_PROTOCOL_RULE = (
    "CRAG-lite (Yan et al. 2024) + Self-RAG-style retry: "
    "retrieve with the parent text → grade Correct/Ambiguous/Incorrect → "
    "only if not Correct and prior chat exists, cheap-rewrite once and "
    "re-retrieve (max 1 retry). Never rewrite on turn 1 when retrieval is strong."
)


def rewrite_retrieval_query(
    message: str,
    history_context: Optional[str],
    *,
    lang: str = "en",
    grade: str = "incorrect",
    hit_titles: Optional[List[str]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Corrective rewrite after a weak first retrieve (not proactive).

    ``force=True`` when the retrieval grader said Ambiguous/Incorrect.
    Without force, only deictic/short asks with prior are rewritten.
    """
    original = (message or "").strip()
    prior = _prior_history_only(history_context, original)
    out: Dict[str, Any] = {
        "rewritten": False,
        "called": False,
        "needed": False,
        "reason": "skipped",
        "original": original,
        "query": original,
        "prior_chars": len(prior),
        "grade": grade,
        "protocol": "CRAG-lite",
        "rule": _PROTOCOL_RULE,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
    }
    if not SETTINGS.arbiter_query_rewrite:
        out["reason"] = "disabled"
        return out
    if not prior:
        out["reason"] = "no_prior"
        return out
    deictic = needs_retrieval_rewrite(original, prior)
    if not force and not deictic:
        out["reason"] = "retrieval_ok"
        return out
    # force path still needs something to gain from context
    if force and not deictic and len(prior) < 20:
        out["reason"] = "no_useful_prior"
        return out
    out["needed"] = True
    if not cheap_llm.enabled():
        out["reason"] = "no_llm"
        return out
    hist = prior[-1600:] if len(prior) > 1600 else prior
    titles = hit_titles or []
    title_line = "; ".join(t for t in titles[:5] if t) or "(none useful)"
    out["called"] = True
    prompt = (
        "First retrieval looked weak for this parent ask. Rewrite into ONE "
        "standalone centre-KB search query using PRIOR chat to resolve "
        "that/this/it and the topic. Same language as the parent. "
        "Output ONLY the query — no quotes, no preamble.\n"
        f"Lang: {lang}\n"
        f"Grade: {grade}\n"
        f"Weak hits: {title_line}\n"
        f"Prior chat:\n{hist}\n\n"
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
    if len(cleaned) > 280:
        cleaned = cleaned[:280].rsplit(" ", 1)[0] or cleaned[:280]
    if cleaned.lower() == original.lower():
        out.update({"reason": "unchanged", "query": original})
        return out
    out.update({"rewritten": True, "reason": "ok", "query": cleaned})
    return out
