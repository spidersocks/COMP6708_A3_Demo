"""Hybrid intent router: hard vetoes + regex rules + prototype classifier.

Priority (high → low) when scores tie:
  escalation > off_domain > weather > illness > allergy > makeup > teachers >
  pickup > location > payment > fees > pets/boarding/unknown > cancel >
  trial_info > booking_dialogue > availability > hours > greeting/thanks >
  confused > rag
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
from services.intent_classifier import blend_scores, score_prototypes

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
_BOOK_VERB_RE = re.compile(
    r"("
    r"\b(book|booking|reserve|schedule\s+a|sign\s*up\s+for|enrol(?:l)?\s+in)\b|"
    r"我想(預約|预约|報|报)|幫我(訂|订|約|约)|预约|預約|報名|报名"
    r")",
    re.I,
)
_TRIAL_INFO_RE = re.compile(
    r"("
    r"\b(do\s+you\s+offer|is\s+there|have\s+you\s+got|what\s+is|tell\s+me\s+about)\b.*\btrials?\b|"
    r"\boffer\s+trials?\b|"
    r"\btrials?\s+(class|assessment|lesson)?\b.*\b(offer|for|my|age|year)\b|"
    r"\b(offer|have)\s+a\s+trials?\b|"
    r"\bhow\s+(much\s+time|long)\b.*\btrials?\b|"
    r"\btrials?\b.*\bhow\s+(much\s+time|long)\b|"
    r"有冇試堂|有没有试听|試堂係咩|试听是什么|什麼是試堂|什么是试听|"
    r"想了解.{0,6}試堂|了解一下试听|想先了解|試堂幾耐|试听多久"
    r")",
    re.I,
)
# Policy / process questions — NOT a request to start booking seats
_TRIAL_POLICY_RE = re.compile(
    r"("
    r"\b(do\s+i|do\s+we|does\s+(he|she|my\s+(kid|child|son|daughter))|"
    r"must\s+i|have\s+to|need\s+to|required|necessary|should\s+i)\b"
    r".{0,50}\b(trial|assessment)\b|"
    r"\b(trial|assessment)\b.{0,50}\b("
    r"first|before\s+(class|enrol|enroll|joining|starting|the\s+course)|"
    r"required|necessary|mandatory)\b|"
    r"\b(not\s+attended|never\s+attended|new\s+(family|student|child)|first\s+time|"
    r"hasn'?t\s+attended|have\s+not\s+attended)\b.{0,60}\b(trial|assessment)\b|"
    r"\bbefore\s+(class|the\s+class|enrol|enrolling|joining)\b.{0,40}\b(trial|assessment)\b|"
    r"要不要先(試堂|试听)|需(不|唔)?需要(先)?(試堂|试听)|要先(試堂|试听)|"
    r"未上過.?要不要|没上过.?要不要"
    r")",
    re.I,
)
_PETS_RE = re.compile(
    r"\b(dogs?|cats?|pets?|pupp(?:y|ies)|kittens?)\b|寵物|宠物|(?<![A-Za-z])狗(?![A-Za-z])|(?<![A-Za-z])貓(?![A-Za-z])|(?<![A-Za-z])猫(?![A-Za-z])",
    re.I,
)
_BOARDING_RE = re.compile(
    r"\b(overnight|boarding|sleepover|hostel|dorm)\b|寄宿|留宿|過夜|过夜",
    re.I,
)
_CONFUSED_RE = re.compile(
    r"^(what|huh|sorry|come again|eh|\?\?\?+|嗯|吓|咩話|什么)\??[!!.。]*$",
    re.I,
)
_THANKS_RE = re.compile(
    r"^(thanks|thank\s*you|thx|ty|cheers|多謝|谢谢|謝謝|唔該)[!!.。 ]*$",
    re.I,
)
_OFF_DOMAIN_RE = re.compile(
    r"("
    r"\b(politics|political|election|scrap(?:e|ing)|malware|hack(?:ing)?|"
    r"system\s+prompt|admin\s+password|api\s*keys?|"
    r"admin\s+to-?do|show\s+(me\s+)?(the\s+)?admin)\b|"
    # Clear non-centre chit-chat — refuse without spamming the follow-up sheet
    r"\b(weather|temperature|forecast|climate)\b.{0,50}\b("
    r"tokyo|london|paris|beijing|shanghai|seoul|sydney|new\s+york|nyc|"
    r"los\s+angeles"
    r")\b|"
    r"\b(bitcoin|crypto(?:currency)?|stock\s+price|nba|premier\s+league|world\s+cup)\b|"
    r"香港政治|選舉|选举"
    r")",
    re.I,
)

# Tie-break order when scores equal
_PRIORITY: List[str] = [
    "staff_escalation",
    "off_domain",
    "severe_weather",
    "illness",
    "allergy",
    "makeup",
    "teachers",
    "pickup",
    "waiting",
    "sibling",
    "guest_policy",
    "location",
    "payment",
    "fees",
    "pets_policy",
    "unsupported_facility",
    "unknown_programme",
    "cancel_dialogue",
    "trial_info",
    "booking_dialogue",
    "booking_tool",
    "opening_hours",
    "greeting",
    "thanks",
    "confused",
    "rag",
]

# Routes that hard-veto the soft classifier blend (always win if regex fired)
_HARD_VETO = {
    "staff_escalation",
    "off_domain",
    "severe_weather",
    "confused",
    "thanks",
}


@dataclass
class IntentDecision:
    route: str
    scores: Dict[str, float] = field(default_factory=dict)
    debug: Dict[str, Any] = field(default_factory=dict)


def is_trial_policy_question(message: str) -> bool:
    """True when parent asks whether a trial is required / comes first."""
    return bool(_TRIAL_POLICY_RE.search(message or ""))


def is_trial_info(message: str) -> bool:
    m = message or ""
    if re.search(
        r"\b(show|list|see)\b.*\b(availab|slots?|seats?|openings?)\b|"
        r"open\s+slots?|open\s+seats?|seats?\s+left|有冇位|空位|有冇試堂位",
        m,
        re.I,
    ):
        return False
    if _BOOK_VERB_RE.search(m) and _AVAIL_STRONG_RE.search(m):
        return False
    # Explicit seat-booking intent (not "need to book … first?" policy)
    if (
        _BOOK_VERB_RE.search(m)
        and re.search(r"\b(trial|assessment)\b", m, re.I)
        and not is_trial_policy_question(m)
    ):
        return False
    if is_trial_policy_question(m):
        return True
    return bool(_TRIAL_INFO_RE.search(m))


def is_availability_browse(message: str) -> bool:
    m = message or ""
    strong = bool(_AVAIL_STRONG_RE.search(m))
    # Strong seat/slot wording wins even if the parent also asks about fees.
    # Weak browse still yields to structured fact intents.
    if not strong:
        if centre_facts.is_fees_intent(m) or centre_facts.is_severe_weather_intent(m):
            return False
        if centre_facts.is_location_intent(m) or centre_facts.is_allergy_intent(m):
            return False
        if centre_facts.is_illness_intent(m) or centre_facts.is_payment_intent(m):
            return False
        if centre_facts.is_teachers_intent(m) or centre_facts.is_pickup_intent(m):
            return False
        if centre_facts.is_makeup_intent(m):
            return False
        if centre_facts.is_sibling_intent(m) or centre_facts.is_waiting_intent(m):
            return False
        if _PETS_RE.search(m) or _BOARDING_RE.search(m):
            return False
        if dialogue_svc.unknown_programme(m):
            return False
        if is_trial_info(m):
            return False
    elif centre_facts.is_waiting_intent(m):
        # Explicit waitlist still beats seat browse
        return False
    if strong:
        return True
    if _BOOK_VERB_RE.search(m) and re.search(r"\b(slot|seat|time|availability)\b", m, re.I):
        return True
    return False


def is_pets_question(message: str) -> bool:
    return bool(_PETS_RE.search(message or ""))


def is_boarding_question(message: str) -> bool:
    return bool(_BOARDING_RE.search(message or ""))


def is_confused(message: str) -> bool:
    return bool(_CONFUSED_RE.match((message or "").strip()))


def _pick_route(scores: Dict[str, float]) -> str:
    if not scores:
        return "rag"
    best = max(scores.values())
    candidates = [r for r, s in scores.items() if s >= best - 1e-9]
    for r in _PRIORITY:
        if r in candidates:
            return r
    return candidates[0]


def score_intents(
    message: str,
    *,
    lang: str,
    session_id: str,
    history: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Dict[str, float], Dict[str, Any]]:
    """Return raw scores for audit / tests (does not short-circuit)."""
    history = history or []
    scores: Dict[str, float] = {}
    debug: Dict[str, Any] = {"conflicts": []}

    esc = escalation_svc.detect_escalation(message)
    if esc:
        scores["staff_escalation"] = 1.0
        debug["escalation"] = esc

    if is_confused(message):
        scores["confused"] = 1.0
    if _THANKS_RE.match((message or "").strip()):
        scores["thanks"] = 1.0

    if _OFF_DOMAIN_RE.search(message or ""):
        scores["off_domain"] = 0.98

    if centre_facts.is_severe_weather_intent(message):
        scores["severe_weather"] = 1.0
    if centre_facts.is_illness_intent(message):
        scores["illness"] = 0.95
    if centre_facts.is_allergy_intent(message):
        scores["allergy"] = 0.95
    if centre_facts.is_location_intent(message):
        scores["location"] = 0.95
    if centre_facts.is_payment_intent(message):
        scores["payment"] = 0.95
    if centre_facts.is_fees_intent(message):
        scores["fees"] = 0.9
    if centre_facts.is_teachers_intent(message):
        scores["teachers"] = 0.95
    if centre_facts.is_pickup_intent(message):
        scores["pickup"] = 0.95
    if centre_facts.is_makeup_intent(message):
        scores["makeup"] = 0.95
    if centre_facts.is_waiting_intent(message):
        scores["waiting"] = 0.96
    if centre_facts.is_sibling_intent(message):
        scores["sibling"] = 0.96
    if centre_facts.is_guest_intent(message):
        scores["guest_policy"] = 0.96

    if is_pets_question(message):
        scores["pets_policy"] = 0.95
    if is_boarding_question(message):
        scores["unsupported_facility"] = 0.95
        debug["label"] = "overnight boarding"

    unk = dialogue_svc.unknown_programme(message)
    if unk:
        scores["unknown_programme"] = 0.95
        debug["label"] = unk

    cancel_active = cancel_svc.is_active(session_id)
    if cancel_active or cancel_svc.is_cancel_request(message):
        scores["cancel_dialogue"] = 0.95
        debug["cancel_active"] = cancel_active

    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    dialogue_active = dialogue_svc.is_active(session_id)
    if leave_booking and dialogue_active:
        debug["left_booking"] = True

    if is_trial_info(message):
        scores["trial_info"] = 0.9

    dialogue_hit = (
        (not leave_booking)
        and (dialogue_active or dialogue_svc.is_booking_request(message))
    )
    if dialogue_hit:
        scores["booking_dialogue"] = 0.85
        debug["dialogue_active"] = dialogue_active

    if is_availability_browse(message):
        # Strong seat/slot wording should beat the cheap arbiter → RAG override
        avail_score = 0.95 if _AVAIL_STRONG_RE.search(message or "") else 0.85
        scores["availability"] = avail_score
        # route name used by chat()
        scores["booking_tool"] = avail_score

    hours_intent, hours_debug = detect_opening_hours_intent(message, lang)
    if hours_intent:
        scores["opening_hours"] = 0.8
        debug["hours_debug"] = hours_debug

    # Weather always dominates hours when both fire
    if scores.get("severe_weather") and scores.get("opening_hours"):
        debug["conflicts"].append("weather>hours")
        scores.pop("opening_hours", None)

    # Illness / allergy beat generic fees if somehow both matched
    if scores.get("illness") and scores.get("fees"):
        debug["conflicts"].append("illness>fees")
        scores.pop("fees", None)
    if scores.get("allergy") and scores.get("fees"):
        debug["conflicts"].append("allergy>fees")
        scores.pop("fees", None)

    # Payment vs fees already separated in centre_facts; if both, prefer payment for pay verbs
    if scores.get("payment") and scores.get("fees"):
        debug["conflicts"].append("payment>fees")
        scores.pop("fees", None)

    # Sibling / waiting beat generic fees + RAG
    if scores.get("sibling") and scores.get("fees"):
        debug["conflicts"].append("sibling>fees")
        scores.pop("fees", None)
    if scores.get("waiting"):
        scores.pop("pickup", None)
        scores.pop("booking_tool", None)
        scores.pop("availability", None)

    # Strong live-seat browse beats fee-guide when both appear in one turn
    if (
        scores.get("booking_tool")
        and scores.get("fees")
        and _AVAIL_STRONG_RE.search(message or "")
    ):
        debug["conflicts"].append("availability>fees")
        scores.pop("fees", None)

    # Trial info beats availability when both (seat words stripped in is_trial_info)
    if scores.get("trial_info") and scores.get("booking_tool"):
        debug["conflicts"].append("trial_info>availability")
        scores.pop("booking_tool", None)
        scores.pop("availability", None)

    # Explicit book/enrol verbs beat trial explainer — except policy "need to book first?"
    if (
        scores.get("booking_dialogue")
        and scores.get("trial_info")
        and _BOOK_VERB_RE.search(message or "")
        and not is_trial_policy_question(message)
    ):
        debug["conflicts"].append("booking>trial_info")
        scores.pop("trial_info", None)
    if scores.get("trial_info") and scores.get("booking_dialogue") and is_trial_policy_question(
        message
    ):
        debug["conflicts"].append("trial_policy>booking")
        scores.pop("booking_dialogue", None)
        scores.pop("booking_tool", None)
        scores.pop("availability", None)

    # Pets beat booking/availability
    if scores.get("pets_policy"):
        scores.pop("booking_tool", None)
        scores.pop("availability", None)
        scores.pop("booking_dialogue", None)

    from rag.pipeline import is_greeting

    if is_greeting(message):
        scores["greeting"] = 0.7

    # Long multi-topic parent dumps: don't answer only "parking/location"
    fact_keys = (
        "severe_weather",
        "illness",
        "allergy",
        "location",
        "payment",
        "fees",
        "pets_policy",
        "trial_info",
        "teachers",
        "pickup",
        "makeup",
        "sibling",
        "waiting",
    )
    soft = 0
    low = (message or "").lower()
    for kw in (
        "phonics",
        "sibling",
        "budget",
        "pickup",
        "eczema",
        "discount",
        "parking",
        "shy",
        "allergy",
        "mandarin",
        "grandparents",
    ):
        if kw in low:
            soft += 1
    n_facts = sum(1 for k in fact_keys if scores.get(k, 0) >= 0.9)
    # Many parent constraints in one message → don't answer with a single fact card
    if soft >= 3 or (n_facts >= 1 and soft >= 2 and len(message or "") > 80):
        for k in fact_keys:
            scores.pop(k, None)
        scores["rag"] = 0.65
        debug["conflicts"].append("multi_topic>single_fact")

    # Soft prototype classifier blend (unless a hard veto already owns the turn)
    hard_hit = any(scores.get(k, 0) >= 0.98 for k in _HARD_VETO)
    proto = score_prototypes(message)
    debug["prototype_top"] = sorted(proto.items(), key=lambda kv: -kv[1])[:3]
    if not hard_hit and proto:
        blended = blend_scores(scores, proto)
        # Keep absolute hard regex hits (≥0.95) from being diluted below threshold
        for k, v in scores.items():
            if v >= 0.95:
                blended[k] = max(blended.get(k, 0), v)
        scores = blended
        debug["conflicts"].append("blended_prototypes")

    if not scores:
        scores["rag"] = 0.5

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
    """Explicit 'what in the query matched' payload for the inspector."""
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
    """One-glance explanation of why this query took the final route."""
    winner = next((r for r in rules if r.get("id") == route), None)
    if winner is None and route == "booking_tool":
        winner = next((r for r in rules if r.get("id") == "booking_tool"), None)
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
    """Per-rule fired / not-fired report with nested why / why-not evidence."""
    msg = message or ""
    esc = debug.get("escalation") or escalation_svc.detect_escalation(message)
    hours_hit = "opening_hours" in scores or bool(debug.get("hours_debug"))
    hours_detail = ""
    if debug.get("hours_debug"):
        hours_detail = str(
            (debug["hours_debug"] or {}).get("matched_via")
            or (debug["hours_debug"] or {}).get("reason")
            or "hours_intent"
        )
    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    dialogue_active = dialogue_svc.is_active(session_id)
    cancel_active = cancel_svc.is_active(session_id)
    cancel_request = cancel_svc.is_cancel_request(message)
    unk = dialogue_svc.unknown_programme(message)
    fee_duration_block = bool(centre_facts._FEE_DURATION_RE.search(msg))  # noqa: SLF001
    structured_owner = [
        k
        for k in _PRIORITY
        if k not in {"rag", "greeting", "thanks", "confused"}
        and float(scores.get(k) or 0) >= 0.8
    ]

    # Escalation: ordered category checks
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

    rows = [
        _rule_row(
            "staff_escalation",
            "Ask-human / anger / safety",
            matched=bool(scores.get("staff_escalation")),
            score=scores.get("staff_escalation"),
            mechanism=(
                "Ordered detectors in services/escalation.py: safety → angry → "
                "hard_conflict → ask_human. First hit wins; score 1.0."
            ),
            pattern_summary="safety / anger / refund-withdraw / speak-to-human cues",
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
            "off_domain",
            "Off-domain refuse",
            message=msg,
            pattern=_OFF_DOMAIN_RE,
            pattern_summary="politics / malware / non-HK city weather / crypto / sports…",
            matched=bool(scores.get("off_domain")),
            score=scores.get("off_domain"),
            mechanism="Regex short-circuit before centre tools. Score 0.98. No admin todo.",
            why_template="Triggered by off-domain cue «{span}».",
            why_not="Message stays inside centre / parenting scope.",
            downstream="Polite refuse · no follow-up sheet row",
        ),
        _regex_rule(
            "severe_weather",
            "Typhoon / Black Rain / severe weather policy",
            message=msg,
            pattern=centre_facts._WEATHER_POLICY_RE,  # noqa: SLF001
            pattern_summary="typhoon, T1/T3/T8/T10, black/red/amber rain, 風球, 黑雨…",
            matched=centre_facts.is_severe_weather_intent(message),
            score=scores.get("severe_weather"),
            mechanism=(
                "centre_facts.is_severe_weather_intent → load facts/severe_weather.json → "
                "optional main-LLM adapt_structured_fact. Hard veto (≥0.98)."
            ),
            why_template="Weather-policy keywords matched «{span}».",
            why_not="No typhoon / rainstorm / signal keywords present.",
            downstream="Structured policy answer · adapt · RAG skipped",
        ),
        _regex_rule(
            "illness",
            "Illness / sick-child policy",
            message=msg,
            pattern=centre_facts._ILLNESS_RE,  # noqa: SLF001
            pattern_summary="fever, flu, vomit, stay home, 發燒…",
            matched=centre_facts.is_illness_intent(message),
            score=scores.get("illness"),
            mechanism="Structured ops fact (beats generic fees on conflict).",
            why_template="Illness cue «{span}».",
            why_not="No illness / keep-home language.",
            downstream="centre_ops illness policy · adapt",
        ),
        _regex_rule(
            "allergy",
            "Allergy policy (non-urgent)",
            message=msg,
            pattern=centre_facts._ALLERGY_RE,  # noqa: SLF001
            pattern_summary="allergy, peanut, epipen, 過敏…",
            matched=centre_facts.is_allergy_intent(message),
            score=scores.get("allergy"),
            mechanism=(
                "Non-urgent allergy FAQ. Safety+reaction wording is handled earlier "
                "by staff_escalation."
            ),
            why_template="Allergy cue «{span}».",
            why_not="No allergy keywords (or handled as urgent escalation instead).",
            downstream="Allergy ops fact · adapt",
        ),
        _regex_rule(
            "fees",
            "Published fee guide",
            message=msg,
            pattern=centre_facts._FEE_RE,  # noqa: SLF001
            pattern_summary="fee, tuition, price, how much, 學費/月費/費用/班費/幾錢…",
            matched=centre_facts.is_fees_intent(message),
            score=scores.get("fees"),
            mechanism=(
                "is_fees_intent: _FEE_RE must match AND _FEE_DURATION_RE must not "
                "(so “how much time” ≠ fees)."
            ),
            why_template="Fee-guide cue «{span}».",
            why_not=(
                "Blocked as duration question (how much time / how long)."
                if fee_duration_block
                else "No fee / price / tuition cue."
            ),
            downstream="facts/fees.json (+ course hint) · adapt · not RAG",
            extra_checks=[
                _check(
                    "Duration exclusion (_FEE_DURATION_RE)",
                    not fee_duration_block,
                    "blocked" if fee_duration_block else "not a duration ask",
                )
            ],
        ),
        _regex_rule(
            "location",
            "Centre location / parking",
            message=msg,
            pattern=centre_facts._LOCATION_RE,  # noqa: SLF001
            pattern_summary="where are you, address, parking, MTR, 地址…",
            matched=centre_facts.is_location_intent(message),
            score=scores.get("location"),
            mechanism="Structured location / access fact.",
            why_template="Location cue «{span}».",
            why_not="No address / directions / parking cue.",
            downstream="Location fact · adapt",
        ),
        _regex_rule(
            "payment",
            "Payment methods",
            message=msg,
            pattern=centre_facts._PAYMENT_RE,  # noqa: SLF001
            pattern_summary="how to pay, FPS, bank transfer, 轉數快…",
            matched=centre_facts.is_payment_intent(message),
            score=scores.get("payment"),
            mechanism="Payment verbs; wins over generic fees when both fire.",
            why_template="Payment cue «{span}».",
            why_not="No payment-method language.",
            downstream="Payment fact · adapt",
        ),
        _regex_rule(
            "teachers",
            "Who teaches…",
            message=msg,
            pattern=centre_facts._TEACHERS_RE,  # noqa: SLF001
            pattern_summary="who teaches, teacher qualifications, 老師…",
            matched=centre_facts.is_teachers_intent(message),
            score=scores.get("teachers"),
            mechanism="Teacher roster / qualifications fact.",
            why_template="Teacher cue «{span}».",
            why_not="No teacher / tutor wording.",
            downstream="Teachers fact · adapt",
        ),
        _regex_rule(
            "pickup",
            "Pickup / drop-off",
            message=msg,
            pattern=centre_facts._PICKUP_RE,  # noqa: SLF001
            pattern_summary="pickup, drop-off, authorised collector, 接送…",
            matched=centre_facts.is_pickup_intent(message),
            score=scores.get("pickup"),
            mechanism="Pickup policy fact (cleared if waiting-list wins).",
            why_template="Pickup cue «{span}».",
            why_not="No pickup / drop-off cue.",
            downstream="Pickup fact · adapt",
        ),
        _regex_rule(
            "makeup",
            "Absence / make-up",
            message=msg,
            pattern=centre_facts._MAKEUP_RE,  # noqa: SLF001
            pattern_summary="make-up, reschedule, missed class, 補課…",
            matched=centre_facts.is_makeup_intent(message),
            score=scores.get("makeup"),
            mechanism="Absence / make-up quota policy.",
            why_template="Make-up cue «{span}».",
            why_not="No make-up / absence cue.",
            downstream="Make-up fact · adapt",
        ),
        _regex_rule(
            "sibling",
            "Sibling discount",
            message=msg,
            pattern=centre_facts._SIBLING_RE,  # noqa: SLF001
            pattern_summary="sibling discount, second child, 兄弟姊妹…",
            matched=centre_facts.is_sibling_intent(message),
            score=scores.get("sibling"),
            mechanism="Sibling pricing fact; beats generic fees.",
            why_template="Sibling cue «{span}».",
            why_not="No sibling / second-child discount language.",
            downstream="Sibling fact · adapt",
        ),
        _regex_rule(
            "waiting",
            "Waiting list",
            message=msg,
            pattern=centre_facts._WAITING_RE,  # noqa: SLF001
            pattern_summary="waitlist, class full, 候補, 滿班…",
            matched=centre_facts.is_waiting_intent(message),
            score=scores.get("waiting"),
            mechanism="Waiting-list policy; clears pickup / booking_tool scores.",
            why_template="Waiting-list cue «{span}».",
            why_not="No waitlist / full-class language.",
            downstream="Waiting-list fact · adapt",
        ),
        _regex_rule(
            "guest_policy",
            "Friend / guest at trial",
            message=msg,
            pattern=centre_facts._GUEST_RE,  # noqa: SLF001
            pattern_summary="bring a friend, guest child, 帶朋友…",
            matched=centre_facts.is_guest_intent(message),
            score=scores.get("guest_policy"),
            mechanism="Guest-at-trial policy (separate from sibling discount).",
            why_template="Guest cue «{span}».",
            why_not="No friend / guest-at-class language.",
            downstream="Guest policy fact · adapt",
        ),
        _regex_rule(
            "pets_policy",
            "Pets on site",
            message=msg,
            pattern=_PETS_RE,
            pattern_summary="dog, cat, pet, 寵物…",
            matched=is_pets_question(message),
            score=scores.get("pets_policy"),
            mechanism="Pets policy; clears booking / availability scores.",
            why_template="Pet cue «{span}».",
            why_not="No pet keywords.",
            downstream="Pets policy · adapt",
        ),
        _regex_rule(
            "unsupported_facility",
            "Overnight / boarding (unsupported)",
            message=msg,
            pattern=_BOARDING_RE,
            pattern_summary="overnight, boarding, 寄宿…",
            matched=is_boarding_question(message),
            score=scores.get("unsupported_facility"),
            mechanism="Explicit unsupported facility refuse.",
            why_template="Boarding cue «{span}».",
            why_not="No overnight / boarding language.",
            downstream="Unsupported-facility refuse",
        ),
        _rule_row(
            "unknown_programme",
            "Unknown programme → staff follow-up",
            matched=bool(unk),
            score=scores.get("unknown_programme"),
            mechanism="dialogue_svc.unknown_programme scans for programmes not in catalogue.",
            pattern_summary="known catalogue miss (e.g. scuba, boarding school…)",
            why=f"Unknown programme label «{unk}»." if unk else "",
            why_not="No unknown-programme token detected against the catalogue.",
            checks=[
                _check(
                    "unknown_programme(message)",
                    bool(unk),
                    str(unk) if unk else "catalogue miss not detected",
                )
            ],
            downstream="Honest refuse + admin_todos follow-up row",
        ),
        _rule_row(
            "cancel_dialogue",
            "Cancel class dialogue",
            matched=cancel_active or cancel_request,
            score=scores.get("cancel_dialogue"),
            mechanism="Sticky cancel state OR fresh cancel request detector.",
            pattern_summary="cancel / 取消 + sticky session flag",
            why=(
                "Sticky cancel dialogue already active for this session."
                if cancel_active
                else "Fresh cancel request detected."
                if cancel_request
                else ""
            ),
            why_not="No cancel request and no sticky cancel state.",
            checks=[
                _check("Sticky cancel active", cancel_active),
                _check("is_cancel_request(message)", cancel_request),
            ],
            downstream="Multi-turn cancel_dialogue tool",
        ),
        _rule_row(
            "trial_info",
            "Trial explainer (not seat browse)",
            matched=is_trial_info(message),
            score=scores.get("trial_info"),
            mechanism=(
                "is_trial_info: trial explainer regex, with exclusions for seat browse "
                "and explicit book+trial booking verbs (unless trial-policy question)."
            ),
            pattern_summary="do you offer trials / what is a trial / 試堂…",
            why="Classified as trial explainer (not a live seat browse).",
            why_not="Not a trial explainer (or overridden by seats / book verbs).",
            checks=[
                _check("is_trial_info(message)", is_trial_info(message)),
                _check(
                    "is_trial_policy_question",
                    is_trial_policy_question(message),
                    "policy wording keeps trial_info over booking",
                ),
            ],
            downstream="Trial explainer fact · adapt",
        ),
        _rule_row(
            "booking_dialogue",
            "Book / enrol dialogue",
            matched=bool(scores.get("booking_dialogue")),
            score=scores.get("booking_dialogue"),
            mechanism=(
                "Sticky booking state or is_booking_request, unless should_yield_to_rag."
            ),
            pattern_summary="book / enrol / 預約 + sticky session",
            why=(
                "Sticky booking dialogue active."
                if dialogue_active and not leave_booking
                else "Booking request verbs matched."
                if scores.get("booking_dialogue")
                else ""
            ),
            why_not=(
                "Yielded sticky booking to RAG/other intent."
                if leave_booking and dialogue_active
                else "No booking request and no sticky booking state."
            ),
            checks=[
                _check("dialogue_active", dialogue_active),
                _check("should_yield_to_rag", leave_booking),
                _check(
                    "score booking_dialogue",
                    bool(scores.get("booking_dialogue")),
                    f"{scores.get('booking_dialogue')}",
                ),
            ],
            downstream="Multi-turn booking_dialogue",
        ),
        _rule_row(
            "booking_tool",
            "Live seats / availability browse",
            matched=is_availability_browse(message),
            score=scores.get("booking_tool") or scores.get("availability"),
            mechanism=(
                "is_availability_browse: strong seat/slot wording, after excluding "
                "fees/weather/facts/trial-info/pets/unknown."
            ),
            pattern_summary="seats left, availability, open slots, 有冇位…",
            matched_span=(avail_span := _match_span(_AVAIL_STRONG_RE, msg)),
            match_evidence=_match_evidence(
                msg,
                span=avail_span,
                kind="substring" if avail_span else "predicate",
                explanation=(
                    f"Seat/availability cue «{avail_span['text']}» at "
                    f"[{avail_span['start']}:{avail_span['end']}] — "
                    "live register browse, not a waitlist ask."
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
            why_not="No seat/slot browse wording (or excluded by a higher fact intent).",
            checks=[
                _check(
                    "Strong avail regex",
                    bool(avail_span),
                    f"«{avail_span['text']}»" if avail_span else "no match",
                ),
                _check("is_availability_browse", is_availability_browse(message)),
            ],
            downstream="CSV register → seats_left cards /trial slots",
        ),
        _rule_row(
            "opening_hours",
            "Opening hours / date / holiday",
            matched=hours_hit,
            score=scores.get("opening_hours"),
            mechanism=(
                "llm.hours_intent.detect_opening_hours_intent → deterministic calendar "
                "(holidays, Sunday closed, optional HKO)."
            ),
            pattern_summary="hours / open / Christmas / tomorrow? follow-ups",
            why=(
                f"Hours intent fired via {hours_detail or 'hours detector'}."
                if hours_hit
                else ""
            ),
            why_not="No date/hours intent (or cleared by weather>hours conflict).",
            checks=[
                _check("hours intent", hours_hit, hours_detail or "not detected"),
                _check(
                    "weather>hours conflict",
                    "weather>hours" in (debug.get("conflicts") or []),
                    "opening_hours score cleared when severe_weather also fires",
                ),
            ],
            downstream="Deterministic opening-hours engine · adapt",
        ),
        _rule_row(
            "greeting",
            "Greeting / thanks / confused",
            matched=bool(
                scores.get("greeting") or scores.get("thanks") or scores.get("confused")
            ),
            score=scores.get("greeting")
            or scores.get("thanks")
            or scores.get("confused"),
            mechanism="Short-form social turns (hi / thanks / huh).",
            pattern_summary="hello / thanks / confused one-liners",
            why="Social / clarification short turn.",
            why_not="Not a bare greeting/thanks/confused utterance.",
            checks=[
                _check("greeting score", bool(scores.get("greeting"))),
                _check("thanks score", bool(scores.get("thanks"))),
                _check("confused score", bool(scores.get("confused"))),
            ],
            downstream="Short social reply",
        ),
        _rule_row(
            "rag",
            "RAG knowledge-base path",
            matched=bool(scores.get("rag")) or not structured_owner,
            score=scores.get("rag"),
            mechanism=(
                "Fallback when no structured rule ≥ 0.8 owns the turn, or when "
                "multi_topic>single_fact forces RAG. Runs Chroma retrieve → Qwen generate."
            ),
            pattern_summary="default / multi-topic / narrative KB questions",
            why=(
                "No structured owner ≥ 0.8 — RAG default score applied."
                if not structured_owner
                else f"Explicit RAG score {scores.get('rag')} (e.g. multi-topic conflict)."
                if scores.get("rag")
                else ""
            ),
            why_not=(
                "Idle because structured owner(s) already won: "
                + ", ".join(f"{k}={scores.get(k)}" for k in structured_owner)
                if structured_owner
                else "RAG not selected."
            ),
            checks=[
                _check(
                    "Structured owner ≥ 0.8",
                    bool(structured_owner),
                    ", ".join(structured_owner) if structured_owner else "none",
                ),
                _check(
                    "scores['rag']",
                    bool(scores.get("rag")),
                    str(scores.get("rag")),
                ),
                _check(
                    "multi_topic>single_fact",
                    "multi_topic>single_fact" in (debug.get("conflicts") or []),
                ),
            ],
            downstream="rag/pipeline.py retrieve → generate · citations with rank/score/text",
        ),
    ]
    # Annotate conflicts that suppressed a matched rule
    suppressed = set()
    for c in debug.get("conflicts") or []:
        if ">" in str(c) and c != "blended_prototypes":
            loser = str(c).split(">")[-1].strip()
            # normalize aliases
            if loser == "hours":
                loser = "opening_hours"
            if loser == "availability":
                loser = "booking_tool"
            suppressed.add(loser)
    for row in rows:
        rid = row["id"]
        if rid in suppressed or (
            rid == "opening_hours" and "weather>hours" in (debug.get("conflicts") or [])
        ):
            row["suppressed_by_conflict"] = True
            conflict_note = next(
                (
                    c
                    for c in (debug.get("conflicts") or [])
                    if rid in str(c) or (rid == "opening_hours" and "hours" in str(c))
                ),
                "conflict",
            )
            row["why_not"] = (
                f"Matched initially, then cleared by conflict resolution «{conflict_note}»."
            )
            row["status_reason"] = row["why_not"]
            row["detail"] = row["why_not"]
            row["checks"] = list(row.get("checks") or []) + [
                _check("Survived conflict resolution", False, str(conflict_note))
            ]
    # Guarantee every row carries the full query + match_evidence for the UI.
    for row in rows:
        row["query"] = msg
        if not row.get("match_evidence"):
            row["match_evidence"] = _match_evidence(
                msg,
                span=row.get("matched_span"),
                kind="substring" if row.get("matched_span") else "predicate",
                explanation=row.get("why") or row.get("why_not") or "",
            )
    return rows


def _build_decision_path(
    *,
    route: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
    pre_arbiter_route: str,
) -> List[Dict[str, str]]:
    """Ordered, human-readable path the router took this turn."""
    path: List[Dict[str, str]] = []
    matched = [
        (k, float(v))
        for k, v in sorted(scores.items(), key=lambda kv: -float(kv[1] or 0))
        if float(v or 0) >= 0.28 and k != "availability"
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
                "summary": "No structured rule ≥ 0.28 → default RAG",
                "detail": "",
            }
        )

    for c in debug.get("conflicts") or []:
        if c == "blended_prototypes":
            path.append(
                {
                    "step": "prototype_blend",
                    "summary": "Soft prototype classifier blended into scores",
                    "detail": str(debug.get("prototype_top") or "")[:160],
                }
            )
        else:
            path.append(
                {
                    "step": "conflict",
                    "summary": f"Conflict resolved: {c}",
                    "detail": "lower-priority score cleared",
                }
            )

    hard = any(float(scores.get(k) or 0) >= 0.98 for k in _HARD_VETO)
    if hard:
        path.append(
            {
                "step": "hard_veto",
                "summary": "Hard veto present — prototype blend did not override",
                "detail": ", ".join(
                    k for k in _HARD_VETO if float(scores.get(k) or 0) >= 0.98
                ),
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
                "detail": str(
                    arb.get("reason")
                    or "high_confidence_skip / disabled"
                ),
            }
        )

    path.append(
        {
            "step": "final",
            "summary": f"Final route = {route}",
            "detail": (
                "RAG path runs retrieve→generate"
                if route == "rag"
                else "Structured / tool path — RAG retrieve skipped"
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
    scores, debug = score_intents(
        message, lang=lang, session_id=session_id, history=history
    )
    # Normalize booking_tool key (remove alias)
    if "availability" in scores and "booking_tool" not in scores:
        scores["booking_tool"] = scores["availability"]
    # Drop weak prototype noise below floor unless it's the only signal
    cleaned = {k: v for k, v in scores.items() if k == "availability" or v >= 0.28}
    if not cleaned:
        cleaned = {"rag": 0.5}
    route = _pick_route({k: v for k, v in cleaned.items() if k != "availability"})
    pre_arbiter_route = route

    # Cheap DashScope second look — demo fluency over rigid keyword locks
    try:
        from services import intent_arbiter

        top_score = max(cleaned.values()) if cleaned else 0.0
        conflicts = list(debug.get("conflicts") or [])
        # Prototype blending is noise, not a real conflict — still skip the arbiter
        material_conflicts = [c for c in conflicts if c != "blended_prototypes"]
        # High-confidence structured picks: skip the extra LLM round-trip
        if (
            top_score >= 0.9
            and not material_conflicts
            and route not in {"confused", "off_domain", "rag"}
        ):
            debug["arbiter"] = {
                "action": "keep",
                "route": route,
                "reason": "high_confidence_skip",
                "called": False,
                "skip_detail": (
                    f"top_score={top_score:.2f} ≥ 0.9, "
                    f"no material conflicts, route={route} is structured"
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
            if verdict.get("action") in {"rag", "reroute"} and verdict.get("route"):
                new_route = str(verdict["route"])
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
