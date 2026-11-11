"""Lean intent router: tools only + RAG default.

Priority (high → low) when scores tie:
  escalation > weather > cancel > booking_dialogue >
  booking_tool > hours > rag

Static FAQs (fees, address, payment, pickup, teachers, …) are plain RAG.
Off-topic asks fall through to RAG (retrieve-only + refuse), not a keyword refuse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from llm.hours_intent import detect_opening_hours_intent
from services import booking_dialogue as dialogue_svc
from services import cancel_dialogue as cancel_svc
from services import centre_facts
from services import escalation as escalation_svc
# Soft prototype blend removed (Ockham): hard detectors + sticky state + RAG default.

# Strong availability / seat browse — NOT bare "trial class"
_AVAIL_STRONG_RE = re.compile(
    r"("
    r"\b(availab(?:le|ility)?|time\s*slots?|open\s+slots?|open\s+seats?|"
    r"show\s+(me\s+)?(the\s+)?(slots?|availability|openings?)|"
    r"what\s+times?\s+(are\s+)?(open|available)|"
    r"seats?\s+(are\s+)?left|how\s+many\s+seats?|seat\s+count|"
    r"spaces?\s+left|any\s+slots?|free\s+slots?)\b|"
    r"有冇位|有没有位|空位|档期|檔期|"
    r"有冇試堂位|有没有试听名额"
    r")",
    re.I,
)
_PRIORITY: List[str] = [
    "staff_escalation",
    "severe_weather",
    "cancel_dialogue",
    "booking_dialogue",
    "booking_tool",
    "opening_hours",
    "rag",
]

@dataclass
class IntentDecision:
    route: str
    scores: Dict[str, float] = field(default_factory=dict)
    debug: Dict[str, Any] = field(default_factory=dict)


def is_availability_browse(message: str) -> bool:
    """Seat/slot browse for the live register tool."""
    m = message or ""
    if centre_facts.is_severe_weather_intent(m):
        return False
    return bool(_AVAIL_STRONG_RE.search(m))


def _pick_route(scores: Dict[str, float]) -> str:
    if not scores:
        return "rag"
    best = max(scores.values())
    candidates = [r for r, s in scores.items() if s >= best - 1e-9]
    for r in _PRIORITY:
        if r in candidates:
            return r
    return candidates[0]


SCORE_SOURCE_GUIDE = [
    {
        "id": "hard_regex",
        "label": "Detector match",
        "meaning": "Fixed score when a tool regex/predicate matches.",
    },
    {
        "id": "sticky_state",
        "label": "Sticky dialogue",
        "meaning": "Book/cancel FSM already open for this session.",
    },
    {
        "id": "default_floor",
        "label": "RAG default",
        "meaning": "Nothing matched → rag = 0.5.",
    },
]


def score_intents(
    message: str,
    *,
    lang: str,
    session_id: str,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Return raw scores for audit / tests (does not short-circuit)."""
    scores: Dict[str, float] = {}
    ledger: List[Dict[str, Any]] = []
    debug: Dict[str, Any] = {"conflicts": [], "score_ledger": ledger}

    def _award(
        route: str,
        value: float,
        *,
        source: str,
        why: str,
        cue: str = "",
    ) -> None:
        scores[route] = float(value)
        ledger.append(
            {
                "op": "award",
                "route": route,
                "score": float(value),
                "source": source,
                "why": why,
                "cue": cue,
            }
        )

    esc = escalation_svc.detect_escalation(message)
    if esc:
        _award(
            "staff_escalation",
            1.0,
            source="hard_regex",
            why=f"Escalation detector · category «{esc.get('category')}»",
            cue=str(esc.get("summary") or esc.get("category") or ""),
        )
        debug["escalation"] = esc

    if centre_facts.is_severe_weather_intent(message):
        _award(
            "severe_weather",
            1.0,
            source="hard_regex",
            why="centre_facts.is_severe_weather_intent · fixed 1.0 hard veto",
        )

    cancel_active = cancel_svc.is_active(session_id)
    if cancel_active or cancel_svc.is_cancel_request(message):
        _award(
            "cancel_dialogue",
            0.95,
            source="hard_regex" if not cancel_active else "sticky_state",
            why=(
                "Sticky cancel dialogue already open for this session"
                if cancel_active
                else "Fresh cancel-request detector · fixed 0.95"
            ),
        )
        debug["cancel_active"] = cancel_active

    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    dialogue_active = dialogue_svc.is_active(session_id)
    if leave_booking and dialogue_active:
        debug["left_booking"] = True

    dialogue_hit = (
        (not leave_booking)
        and (dialogue_active or dialogue_svc.is_booking_request(message))
    )
    if dialogue_hit:
        _award(
            "booking_dialogue",
            0.85,
            source="sticky_state" if dialogue_active else "hard_regex",
            why=(
                "Sticky booking dialogue active"
                if dialogue_active
                else "Booking-request verbs · fixed 0.85"
            ),
        )
        debug["dialogue_active"] = dialogue_active

    if is_availability_browse(message):
        span = _match_span(_AVAIL_STRONG_RE, message or "")
        _award(
            "booking_tool",
            0.9,
            source="hard_regex",
            why="Seat/slot browse · 0.9",
            cue=(span or {}).get("text") or "",
        )

    hours_intent, hours_debug = detect_opening_hours_intent(message, lang)
    if hours_intent:
        via = (
            "strong"
            if hours_debug.get("strong_hits")
            else "holiday"
            if hours_debug.get("holiday_hits")
            else "weak+open"
        )
        _award(
            "opening_hours",
            0.8,
            source="hard_regex",
            why=f"detect_opening_hours_intent · fixed 0.8 · via {via}",
        )
        debug["hours_debug"] = hours_debug

    if not scores:
        _award(
            "rag",
            0.5,
            source="default_floor",
            why="No structured scores remained — default RAG floor 0.5",
        )

    origin_by_route: Dict[str, Dict[str, Any]] = {}
    for entry in ledger:
        r = entry.get("route")
        if not r:
            continue
        origin_by_route[r] = {
            "source": entry.get("source"),
            "why": entry.get("why"),
            "cue": entry.get("cue") or "",
            "score": entry.get("score"),
            "op": entry.get("op"),
        }
    debug["score_origins"] = origin_by_route
    debug["score_source_guide"] = SCORE_SOURCE_GUIDE
    return scores, debug


def _match_span(pattern: re.Pattern[str], message: str) -> Optional[Dict[str, Any]]:
    m = pattern.search(message or "")
    if not m:
        return None
    return {"text": m.group(0), "start": m.start(), "end": m.end()}


def _match_evidence(
    message: str,
    *,
    span: Optional[Dict[str, Any]] = None,
    cue: str = "",
    kind: str = "substring",
    explanation: str = "",
) -> Dict[str, Any]:
    msg = message or ""
    text = ""
    start: Optional[int] = None
    end: Optional[int] = None
    if span and span.get("text") is not None:
        text = str(span["text"])
        start = int(span["start"])
        end = int(span["end"])
    elif cue:
        text = str(cue)
        idx = msg.find(text)
        if idx >= 0:
            start, end = idx, idx + len(text)

    before = after = ""
    segments: List[Dict[str, Any]] = []
    annotated = msg
    if text and start is not None and end is not None and 0 <= start < end <= len(msg):
        before = msg[max(0, start - 24) : start]
        after = msg[end : min(len(msg), end + 24)]
        annotated = f"{before}«{text}»{after}"
        if start > 0:
            segments.append({"text": msg[:start], "hit": False})
        segments.append({"text": text, "hit": True})
        if end < len(msg):
            segments.append({"text": msg[end:], "hit": False})
    elif msg:
        segments = [{"text": msg, "hit": False}]

    return {
        "kind": kind,
        "cue": text or cue or "",
        "start": start,
        "end": end,
        "before": before,
        "after": after,
        "annotated": annotated,
        "segments": segments,
        "full_query": msg,
        "explanation": explanation
        or (
            f"The substring «{text}» inside the parent message triggered this rule."
            if text
            else "No single substring cue — matched by predicate / session state."
        ),
    }


def _check(label: str, passed: bool, detail: str = "") -> Dict[str, Any]:
    return {"label": label, "passed": bool(passed), "detail": detail}


def _rule_row(
    rule_id: str,
    label: str,
    *,
    matched: bool,
    score: Optional[float] = None,
    detail: str = "",
    mechanism: str = "",
    pattern_summary: str = "",
    matched_span: Optional[Dict[str, Any]] = None,
    match_evidence: Optional[Dict[str, Any]] = None,
    query: str = "",
    why: str = "",
    why_not: str = "",
    checks: Optional[List[Dict[str, Any]]] = None,
    downstream: str = "",
) -> Dict[str, Any]:
    status_reason = why if matched else why_not
    evidence = match_evidence
    if evidence is None and (matched_span or query):
        evidence = _match_evidence(
            query,
            span=matched_span,
            kind="substring" if matched_span else "predicate",
            explanation=(
                why
                if matched and why
                else why_not
                if not matched and why_not
                else ""
            ),
        )
    return {
        "id": rule_id,
        "label": label,
        "matched": bool(matched),
        "score": score,
        "detail": detail or status_reason,
        "mechanism": mechanism,
        "pattern_summary": pattern_summary,
        "matched_span": matched_span,
        "match_evidence": evidence,
        "query": query,
        "why": why,
        "why_not": why_not,
        "status_reason": status_reason,
        "checks": checks or [],
        "downstream": downstream,
    }


def _regex_rule(
    rule_id: str,
    label: str,
    *,
    message: str,
    pattern: re.Pattern[str],
    pattern_summary: str,
    matched: bool,
    score: Optional[float],
    mechanism: str,
    why_template: str,
    why_not: str,
    downstream: str,
    extra_checks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    span = _match_span(pattern, message)
    checks = [
        _check(
            f"Pattern scan · {pattern_summary}",
            bool(span),
            f"matched «{span['text']}» at [{span['start']}:{span['end']}]"
            if span
            else "no substring matched",
        )
    ]
    if extra_checks:
        checks.extend(extra_checks)
    why = ""
    if matched and span:
        why = why_template.format(span=span["text"])
    elif matched:
        why = why_template.format(span="(predicate true without a single span)")
    evidence = _match_evidence(
        message,
        span=span,
        kind="substring" if span else "predicate",
        explanation=(
            f"Detector looked for: {pattern_summary}. "
            + (
                f"Found cue «{span['text']}» at character positions "
                f"[{span['start']}:{span['end']}] in the parent message."
                if span
                else "No cue substring found in this message."
            )
        ),
    )
    return _rule_row(
        rule_id,
        label,
        matched=matched,
        score=score,
        mechanism=mechanism,
        pattern_summary=pattern_summary,
        matched_span=span,
        match_evidence=evidence,
        query=message,
        why=why,
        why_not=why_not,
        checks=checks,
        downstream=downstream,
    )


def _summarize_winning_match(
    message: str,
    *,
    route: str,
    rules: List[Dict[str, Any]],
    scores: Dict[str, float],
) -> Dict[str, Any]:
    winner = next((r for r in rules if r.get("id") == route), None)
    evidence = (winner or {}).get("match_evidence") or {}
    cue = evidence.get("cue") or ((winner or {}).get("matched_span") or {}).get("text") or ""
    why = (winner or {}).get("why") or (winner or {}).get("status_reason") or ""
    headline = (
        f"Matched because the message contains «{cue}» → route `{route}`."
        if cue
        else f"Route `{route}` won"
        + (f": {why}" if why else f" (score {scores.get(route)}).")
    )
    return {
        "route": route,
        "score": scores.get(route),
        "rule_id": (winner or {}).get("id") or route,
        "rule_label": (winner or {}).get("label") or route,
        "cue": cue,
        "headline": headline,
        "why": why,
        "query": message or "",
        "annotated": evidence.get("annotated") or message or "",
        "segments": evidence.get("segments")
        or ([{"text": message, "hit": False}] if message else []),
        "explanation": evidence.get("explanation") or why,
        "mechanism": (winner or {}).get("mechanism") or "",
        "pattern_summary": (winner or {}).get("pattern_summary") or "",
        "downstream": (winner or {}).get("downstream") or "",
    }


def _build_rules_evaluated(
    message: str,
    *,
    lang: str,
    session_id: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
) -> List[Dict[str, Any]]:
    msg = message or ""
    esc = debug.get("escalation") or escalation_svc.detect_escalation(message)
    hours_hit = "opening_hours" in scores
    hours_detail = ""
    if hours_hit and debug.get("hours_debug"):
        hd = debug["hours_debug"] or {}
        hours_detail = (
            "strong"
            if hd.get("strong_hits")
            else "holiday"
            if hd.get("holiday_hits")
            else "weak+open"
        )
    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    dialogue_active = dialogue_svc.is_active(session_id)
    cancel_active = cancel_svc.is_active(session_id)
    cancel_request = cancel_svc.is_cancel_request(message)
    structured_owner = [
        k
        for k in _PRIORITY
        if k != "rag" and float(scores.get(k) or 0) >= 0.8
    ]

    esc_checks = []
    for name, pat in (
        ("safety", escalation_svc._SAFETY_RE),  # noqa: SLF001
        ("angry", escalation_svc._ANGRY_RE),  # noqa: SLF001
        ("hard_conflict", escalation_svc._HARD_CONFLICT_RE),  # noqa: SLF001
        ("ask_human", escalation_svc._ASK_HUMAN_RE),  # noqa: SLF001
    ):
        sp = _match_span(pat, msg)
        esc_checks.append(
            _check(
                f"Escalation category «{name}»",
                bool(sp),
                f"matched «{sp['text']}»" if sp else "no match",
            )
        )
    esc_span = None
    if isinstance(esc, dict):
        cat = esc.get("category") or ""
        for name, pat in (
            ("safety", escalation_svc._SAFETY_RE),  # noqa: SLF001
            ("angry", escalation_svc._ANGRY_RE),  # noqa: SLF001
            ("hard_conflict", escalation_svc._HARD_CONFLICT_RE),  # noqa: SLF001
            ("ask_human", escalation_svc._ASK_HUMAN_RE),  # noqa: SLF001
        ):
            if name == cat:
                esc_span = _match_span(pat, msg)
                break

    avail_span = _match_span(_AVAIL_STRONG_RE, msg)
    rows = [
        _rule_row(
            "staff_escalation",
            "Ask-human / anger / safety",
            matched=bool(scores.get("staff_escalation")),
            score=scores.get("staff_escalation"),
            mechanism="First matching escalation cue → score 1.0.",
            pattern_summary="safety / anger / hard conflict / ask-human",
            matched_span=esc_span,
            match_evidence=_match_evidence(
                msg,
                span=esc_span,
                kind="substring" if esc_span else "predicate",
                explanation=(
                    f"Escalation category «{(esc or {}).get('category')}» fired on cue "
                    f"«{(esc_span or {}).get('text') or (esc or {}).get('summary') or 'n/a'}»."
                    if esc
                    else "No escalation cue in this message."
                ),
            ),
            query=msg,
            why=(
                f"Triggered as category «{(esc or {}).get('category')}» "
                f"({(esc or {}).get('priority')}): {(esc or {}).get('summary')}"
                if esc
                else ""
            ),
            why_not=(
                "No safety, anger, hard-conflict, or ask-human cue matched. "
                "Ordinary centre questions stay on tools/RAG."
            ),
            checks=esc_checks,
            downstream="Parent ack + staff_messages inbox row (+ optional email draft)",
        ),
        _regex_rule(
            "severe_weather",
            "Typhoon / Black Rain / severe weather policy",
            message=msg,
            pattern=centre_facts._WEATHER_POLICY_RE,  # noqa: SLF001
            pattern_summary="typhoon / T8 / black rain / 風球 / 黑雨",
            matched=centre_facts.is_severe_weather_intent(message),
            score=scores.get("severe_weather"),
            mechanism="Weather-policy keywords → 1.0 (beats hours).",
            why_template="Weather cue «{span}».",
            why_not="No weather-policy keywords.",
            downstream="Weather policy + optional live HKO",
        ),
        _rule_row(
            "cancel_dialogue",
            "Cancel class dialogue",
            matched=cancel_active or cancel_request,
            score=scores.get("cancel_dialogue"),
            mechanism="Sticky cancel or fresh cancel request → 0.95.",
            pattern_summary="cancel / 取消",
            why=(
                "Cancel chat already open for this session."
                if cancel_active
                else "Cancel request detected."
                if cancel_request
                else ""
            ),
            why_not="No cancel ask, and cancel chat is not open.",
            checks=[
                _check(
                    "Cancel chat already open?",
                    cancel_active,
                    "yes — continue cancel FSM"
                    if cancel_active
                    else "no — not mid-cancel",
                ),
                _check(
                    "This message asks to cancel?",
                    cancel_request,
                    "yes" if cancel_request else "no",
                ),
            ],
            downstream="Cancel FSM",
        ),
        _rule_row(
            "booking_dialogue",
            "Book / enrol dialogue",
            matched=bool(scores.get("booking_dialogue")),
            score=scores.get("booking_dialogue"),
            mechanism="Sticky book or book/enrol verbs → 0.85.",
            pattern_summary="book / enrol / 預約",
            why=(
                "Booking chat already open for this session."
                if dialogue_active and not leave_booking
                else "Booking verbs matched."
                if scores.get("booking_dialogue")
                else ""
            ),
            why_not=(
                "Booking chat was open but this message leaves booking for RAG."
                if leave_booking and dialogue_active
                else "No booking ask, and booking chat is not open."
            ),
            checks=[
                _check(
                    "Booking chat already open?",
                    dialogue_active,
                    "yes — continue book FSM"
                    if dialogue_active
                    else "no — not mid-booking",
                ),
                _check(
                    "Leave booking for RAG?",
                    leave_booking,
                    "yes — yield sticky booking"
                    if leave_booking
                    else "no",
                ),
            ],
            downstream="Booking FSM",
        ),
        _rule_row(
            "booking_tool",
            "Live seats / availability browse",
            matched=is_availability_browse(message),
            score=scores.get("booking_tool"),
            mechanism="Seat/slot browse wording → live register (0.9).",
            pattern_summary="seats left / availability / 有冇位",
            matched_span=avail_span,
            match_evidence=_match_evidence(
                msg,
                span=avail_span,
                kind="substring" if avail_span else "predicate",
                explanation=(
                    f"Seat/availability cue «{avail_span['text']}» at "
                    f"[{avail_span['start']}:{avail_span['end']}] — "
                    "live register browse."
                    if avail_span
                    else (
                        "Availability browse predicate true without a single span."
                        if is_availability_browse(message)
                        else "No seat/slot browse wording in this message."
                    )
                ),
            ),
            query=msg,
            why=(
                f"Availability browse matched «{avail_span['text']}»."
                if avail_span
                else "Availability browse predicate true."
                if is_availability_browse(message)
                else ""
            ),
            why_not="No seat/slot browse wording.",
            checks=[
                _check(
                    "Strong avail regex",
                    bool(avail_span),
                    f"«{avail_span['text']}»" if avail_span else "no match",
                ),
                _check("is_availability_browse", is_availability_browse(message)),
            ],
            downstream="CSV register → seats_left cards / trial slots",
        ),
        _rule_row(
            "opening_hours",
            "Opening hours / date / holiday",
            matched=hours_hit,
            score=scores.get("opening_hours"),
            mechanism="Hours/date intent → 0.8 calendar tool.",
            pattern_summary="hours / open / holiday / tomorrow?",
            why=(
                f"Hours intent fired via {hours_detail or 'hours detector'}."
                if hours_hit
                else ""
            ),
            why_not="No date/hours intent (weather policy asks go to severe_weather).",
            checks=[
                _check("hours intent", hours_hit, hours_detail or "not detected"),
                _check(
                    "weather policy override",
                    bool((debug.get("hours_debug") or {}).get("overridden_by_weather_policy")),
                    "hours detector skipped — centre_facts severe weather",
                ),
            ],
            downstream="Deterministic opening-hours engine · adapt",
        ),
        _rule_row(
            "rag",
            "RAG knowledge-base path",
            matched=bool(scores.get("rag")) or not structured_owner,
            score=scores.get("rag"),
            mechanism="Default path when no tool ≥ 0.8 — retrieve → generate.",
            pattern_summary="FAQ / programme / policy / everything else",
            why=(
                "No tool ≥ 0.8 → RAG floor 0.5."
                if not structured_owner
                else f"RAG score {scores.get('rag')}."
                if scores.get("rag")
                else ""
            ),
            why_not=(
                "Tool already won: " + ", ".join(structured_owner)
                if structured_owner
                else "RAG not selected."
            ),
            checks=[
                _check(
                    "Tool owner ≥ 0.8",
                    bool(structured_owner),
                    ", ".join(structured_owner) if structured_owner else "none",
                ),
                _check(
                    "scores['rag']",
                    bool(scores.get("rag")),
                    str(scores.get("rag")),
                ),
            ],
            downstream="rag/pipeline.py retrieve → generate · citations",
        ),
    ]

    origins = debug.get("score_origins") or {}
    for row in rows:
        row["query"] = msg
        if not row.get("match_evidence"):
            row["match_evidence"] = _match_evidence(
                msg,
                span=row.get("matched_span"),
                kind="substring" if row.get("matched_span") else "predicate",
                explanation=row.get("why") or row.get("why_not") or "",
            )
        origin = origins.get(row["id"]) or {}
        if origin:
            row["score_origin"] = origin
        elif row.get("matched") and row.get("score") is not None:
            row["score_origin"] = {
                "source": "hard_regex",
                "why": "Detector matched; see score ledger for the exact award step.",
                "score": row.get("score"),
            }
        else:
            row["score_origin"] = {
                "source": "none",
                "why": "No score awarded to this rule on this turn.",
                "score": None,
            }
    return rows


def _build_decision_path(
    *,
    route: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
    pre_arbiter_route: str,
) -> List[Dict[str, str]]:
    path: List[Dict[str, str]] = []
    matched = [
        (k, float(v))
        for k, v in sorted(scores.items(), key=lambda kv: -float(kv[1] or 0))
        if float(v or 0) >= 0.28
    ]
    if matched:
        top_id, top_sc = matched[0]
        path.append(
            {
                "step": "rules",
                "summary": f"Top rule score: {top_id} = {top_sc:.2f}",
                "detail": ", ".join(f"{k}={v:.2f}" for k, v in matched[:6]),
            }
        )
    else:
        path.append(
            {
                "step": "rules",
                "summary": "No tool rule ≥ 0.28 → default RAG",
                "detail": "",
            }
        )

    path.append(
        {
            "step": "pick",
            "summary": f"Priority pick → {pre_arbiter_route}",
            "detail": "tie-break order in intent_router._PRIORITY",
        }
    )

    arb = debug.get("arbiter") or {}
    override = debug.get("arbiter_override")
    if arb.get("called"):
        if override:
            path.append(
                {
                    "step": "arbiter",
                    "summary": (
                        f"Cheap LLM review OVERRIDE: {override.get('from')} → "
                        f"{override.get('to')}"
                    ),
                    "detail": str(arb.get("reason") or arb.get("action") or ""),
                }
            )
        else:
            path.append(
                {
                    "step": "arbiter",
                    "summary": f"Cheap LLM review kept {arb.get('route') or route}",
                    "detail": str(arb.get("reason") or arb.get("action") or ""),
                }
            )
    else:
        path.append(
            {
                "step": "arbiter",
                "summary": "Cheap LLM review skipped",
                "detail": str(arb.get("reason") or "high_confidence_skip / disabled"),
            }
        )

    path.append(
        {
            "step": "final",
            "summary": f"Final route = {route}",
            "detail": (
                "RAG path runs retrieve→generate"
                if route == "rag"
                else "Tool path — live calendar / HKO / seats / FSM"
            ),
        }
    )
    return path


def decide_intent(
    message: str,
    *,
    lang: str,
    session_id: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> IntentDecision:
    scores, debug = score_intents(message, lang=lang, session_id=session_id)
    cleaned = {k: v for k, v in scores.items() if v >= 0.28}
    if not cleaned:
        cleaned = {"rag": 0.5}
    route = _pick_route(cleaned)
    pre_arbiter_route = route

    # Cheap LLM arbiter only when the pick is actually ambiguous.
    # Clear detector wins (and plain RAG default) skip the extra model call.
    try:
        from services import intent_arbiter

        top_score = max(cleaned.values()) if cleaned else 0.0
        tool_hits = sorted(
            ((k, float(v)) for k, v in cleaned.items() if k != "rag"),
            key=lambda kv: -kv[1],
        )
        close_race = (
            len(tool_hits) >= 2 and (tool_hits[0][1] - tool_hits[1][1]) < 0.15
        )
        clear_detector = route != "rag" and top_score >= 0.8 and not close_race
        if route == "rag" or clear_detector:
            debug["arbiter"] = {
                "action": "keep",
                "route": route,
                "reason": "clear_pick_skip",
                "called": False,
                "skip_detail": (
                    "rag default"
                    if route == "rag"
                    else f"top={top_score:.2f} ≥ 0.8, close_race={close_race}"
                ),
            }
        else:
            hist_preview = ""
            if history:
                bits = []
                for turn in history[-4:]:
                    role = turn.get("role") or turn.get("speaker") or ""
                    content = turn.get("content") or turn.get("message") or ""
                    if content:
                        bits.append(f"{role}: {content}")
                hist_preview = "\n".join(bits)
            verdict = intent_arbiter.review_intent(
                message,
                proposed_route=route,
                scores=cleaned,
                debug=debug,
                history_preview=hist_preview,
            )
            debug["arbiter"] = verdict
            if verdict.get("action") == "rag" and verdict.get("route"):
                new_route = str(verdict["route"])
                if new_route not in _PRIORITY:
                    new_route = "rag"
                if new_route != route:
                    debug["arbiter_override"] = {"from": route, "to": new_route}
                    cleaned = dict(cleaned)
                    cleaned[new_route] = max(float(cleaned.get(new_route) or 0), 0.99)
                    route = new_route
    except Exception as e:
        debug["arbiter"] = {"action": "keep", "route": route, "reason": f"error:{e}"}

    debug["rules_evaluated"] = _build_rules_evaluated(
        message, lang=lang, session_id=session_id, scores=cleaned, debug=debug
    )
    debug["decision_path"] = _build_decision_path(
        route=route,
        scores=cleaned,
        debug=debug,
        pre_arbiter_route=pre_arbiter_route,
    )
    debug["winning_rule"] = {
        "route": route,
        "score": cleaned.get(route),
        "pre_arbiter_route": pre_arbiter_route,
        "arbiter_called": bool((debug.get("arbiter") or {}).get("called")),
        "arbiter_reason": (debug.get("arbiter") or {}).get("reason"),
        "arbiter_skip_detail": (debug.get("arbiter") or {}).get("skip_detail"),
        "override": debug.get("arbiter_override"),
    }
    debug["match_summary"] = _summarize_winning_match(
        message,
        route=route,
        rules=debug["rules_evaluated"],
        scores=cleaned,
    )

    return IntentDecision(route, cleaned, debug)
