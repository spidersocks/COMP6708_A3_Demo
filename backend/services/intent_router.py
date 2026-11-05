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
    if _AVAIL_STRONG_RE.search(m):
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


def _rule_row(
    rule_id: str,
    label: str,
    *,
    matched: bool,
    score: Optional[float] = None,
    detail: str = "",
) -> Dict[str, Any]:
    return {
        "id": rule_id,
        "label": label,
        "matched": bool(matched),
        "score": score,
        "detail": detail,
    }


def _build_rules_evaluated(
    message: str,
    *,
    lang: str,
    session_id: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Per-rule fired / not-fired report for the demo inspector."""
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
    unk = dialogue_svc.unknown_programme(message)

    rows = [
        _rule_row(
            "staff_escalation",
            "Ask-human / anger / safety",
            matched=bool(scores.get("staff_escalation")),
            score=scores.get("staff_escalation"),
            detail=(esc or {}).get("reason") if isinstance(esc, dict) else "",
        ),
        _rule_row(
            "off_domain",
            "Off-domain refuse",
            matched=bool(scores.get("off_domain")),
            score=scores.get("off_domain"),
            detail="non-centre chit-chat / unsafe ask" if scores.get("off_domain") else "",
        ),
        _rule_row(
            "severe_weather",
            "Typhoon / Black Rain / severe weather policy",
            matched=centre_facts.is_severe_weather_intent(message),
            score=scores.get("severe_weather"),
            detail="regex centre_facts.is_severe_weather_intent",
        ),
        _rule_row(
            "illness",
            "Illness / sick-child policy",
            matched=centre_facts.is_illness_intent(message),
            score=scores.get("illness"),
        ),
        _rule_row(
            "allergy",
            "Allergy policy (non-urgent)",
            matched=centre_facts.is_allergy_intent(message),
            score=scores.get("allergy"),
        ),
        _rule_row(
            "fees",
            "Published fee guide",
            matched=centre_facts.is_fees_intent(message),
            score=scores.get("fees"),
        ),
        _rule_row(
            "location",
            "Centre location / parking",
            matched=centre_facts.is_location_intent(message),
            score=scores.get("location"),
        ),
        _rule_row(
            "payment",
            "Payment methods",
            matched=centre_facts.is_payment_intent(message),
            score=scores.get("payment"),
        ),
        _rule_row(
            "teachers",
            "Who teaches…",
            matched=centre_facts.is_teachers_intent(message),
            score=scores.get("teachers"),
        ),
        _rule_row(
            "pickup",
            "Pickup / drop-off",
            matched=centre_facts.is_pickup_intent(message),
            score=scores.get("pickup"),
        ),
        _rule_row(
            "makeup",
            "Absence / make-up",
            matched=centre_facts.is_makeup_intent(message),
            score=scores.get("makeup"),
        ),
        _rule_row(
            "sibling",
            "Sibling discount",
            matched=centre_facts.is_sibling_intent(message),
            score=scores.get("sibling"),
        ),
        _rule_row(
            "waiting",
            "Waiting list",
            matched=centre_facts.is_waiting_intent(message),
            score=scores.get("waiting"),
        ),
        _rule_row(
            "guest_policy",
            "Friend / guest at trial",
            matched=centre_facts.is_guest_intent(message),
            score=scores.get("guest_policy"),
        ),
        _rule_row(
            "pets_policy",
            "Pets on site",
            matched=is_pets_question(message),
            score=scores.get("pets_policy"),
        ),
        _rule_row(
            "unsupported_facility",
            "Overnight / boarding (unsupported)",
            matched=is_boarding_question(message),
            score=scores.get("unsupported_facility"),
        ),
        _rule_row(
            "unknown_programme",
            "Unknown programme → staff follow-up",
            matched=bool(unk),
            score=scores.get("unknown_programme"),
            detail=str(unk or ""),
        ),
        _rule_row(
            "cancel_dialogue",
            "Cancel class dialogue",
            matched=cancel_active or cancel_svc.is_cancel_request(message),
            score=scores.get("cancel_dialogue"),
            detail="sticky" if cancel_active else "",
        ),
        _rule_row(
            "trial_info",
            "Trial explainer (not seat browse)",
            matched=is_trial_info(message),
            score=scores.get("trial_info"),
        ),
        _rule_row(
            "booking_dialogue",
            "Book / enrol dialogue",
            matched=bool(scores.get("booking_dialogue")),
            score=scores.get("booking_dialogue"),
            detail=(
                "sticky dialogue"
                if dialogue_active and not leave_booking
                else ("yield_to_rag" if leave_booking else "")
            ),
        ),
        _rule_row(
            "booking_tool",
            "Live seats / availability browse",
            matched=is_availability_browse(message),
            score=scores.get("booking_tool") or scores.get("availability"),
        ),
        _rule_row(
            "opening_hours",
            "Opening hours / date / holiday",
            matched=hours_hit,
            score=scores.get("opening_hours"),
            detail=hours_detail,
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
        ),
        _rule_row(
            "rag",
            "RAG knowledge-base path",
            matched=bool(scores.get("rag"))
            or (
                not any(
                    (scores.get(k) or 0) >= 0.8
                    for k in _PRIORITY
                    if k not in {"rag", "greeting", "thanks", "confused"}
                )
            ),
            score=scores.get("rag"),
            detail="default when no structured rule wins",
        ),
    ]
    # Annotate conflicts that suppressed a matched rule
    suppressed = set()
    for c in debug.get("conflicts") or []:
        if ">" in str(c):
            loser = str(c).split(">")[-1].strip()
            suppressed.add(loser)
    for row in rows:
        rid = row["id"]
        if rid in suppressed or (
            rid == "opening_hours" and "weather>hours" in (debug.get("conflicts") or [])
        ):
            row["suppressed_by_conflict"] = True
            row["detail"] = (
                (row.get("detail") + " · " if row.get("detail") else "")
                + "cleared by conflict resolution"
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

    return IntentDecision(route, cleaned, debug)
