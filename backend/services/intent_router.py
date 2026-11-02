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
    r"seats?\s+left|any\s+slots?|free\s+slots?)\b|"
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
        scores["availability"] = 0.85
        # route name used by chat()
        scores["booking_tool"] = 0.85

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
    return IntentDecision(route, cleaned, debug)
