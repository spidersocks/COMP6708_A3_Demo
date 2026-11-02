"""Cheap DashScope second-look on structured intent picks (demo-oriented).

Regex/prototypes propose a route; a small LLM may KEEP it, send to RAG, or
reroute to another allowed label when the keyword match looks silly for the
actual parent question.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from config import SETTINGS
from services import cheap_llm

# Structured / tool routes worth reviewing (keyword misfires look awkward live).
_REVIEWABLE: Set[str] = {
    "teachers",
    "fees",
    "location",
    "payment",
    "pickup",
    "makeup",
    "sibling",
    "guest_policy",
    "waiting",
    "allergy",
    "illness",
    "trial_info",
    "pets_policy",
    "unsupported_facility",
    "unknown_programme",
    "opening_hours",
    "severe_weather",
    "booking_tool",
    "booking_dialogue",
    "cancel_dialogue",
}

# Never second-guess these mid-flight / safety paths.
_SKIP: Set[str] = {
    "staff_escalation",
    "greeting",
    "thanks",
    "rag",
}
# Soft short-circuits: arbiter may only KEEP or send to RAG (no random reroute).
_RAG_OR_KEEP: Set[str] = {
    "confused",
    "off_domain",
}

_ROUTE_BLURBS: Dict[str, str] = {
    "teachers": "Fixed teacher roster card (optionally filtered by programme).",
    "fees": "Published tuition / trial fee guide card.",
    "location": "Centre address / MTR / parking card.",
    "payment": "How to pay (bank / FPS) card.",
    "pickup": "Drop-off & pickup policy card.",
    "makeup": "Absence / make-up class policy card.",
    "sibling": "Sibling discount card.",
    "guest_policy": "Bring a friend / guest / visitor policy card.",
    "waiting": "Waiting-list / full-class card.",
    "allergy": "Allergy / peanut policy card.",
    "illness": "Fever / illness stay-home card.",
    "trial_info": "What a trial assessment is / whether it's required.",
    "pets_policy": "Pets-on-site policy (we don't board animals).",
    "unsupported_facility": "Politely decline something we don't offer.",
    "unknown_programme": "Quiet admin flag — programme we don't teach.",
    "opening_hours": "Deterministic hours / holiday / weather-hours engine.",
    "severe_weather": "Typhoon / rainstorm closure policy card.",
    "booking_tool": "Show open seats / availability list.",
    "booking_dialogue": "Multi-turn book-a-class dialogue.",
    "cancel_dialogue": "Multi-turn cancel-a-class dialogue.",
    "rag": "Retrieve centre KB chunks and write a tailored answer.",
    "staff_escalation": "Hand off to a human / boss inbox.",
    "confused": "Short clarify template — bad if the message already has substance.",
    "off_domain": "Refuse off-topic asks — bad on false keyword hits.",
}

def should_review(
    route: str,
    *,
    debug: Optional[Dict[str, Any]] = None,
) -> bool:
    if not SETTINGS.intent_arbiter_enabled or not SETTINGS.has_dashscope:
        return False
    if route in _SKIP:
        return False
    if route not in _REVIEWABLE and route not in _RAG_OR_KEEP:
        return False
    dbg = debug or {}
    # Sticky dialogues already in flight — don't yank the parent out
    if route == "cancel_dialogue" and dbg.get("cancel_active"):
        return False
    if route == "booking_dialogue" and dbg.get("dialogue_active"):
        return False
    return True


def _top_scores(scores: Dict[str, float], n: int = 4) -> List[str]:
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    lines = []
    for route, score in ranked[:n]:
        blurb = _ROUTE_BLURBS.get(route, "")
        lines.append(f"- {route} ({score:.2f}): {blurb}")
    return lines


def review_intent(
    message: str,
    *,
    proposed_route: str,
    scores: Dict[str, float],
    debug: Optional[Dict[str, Any]] = None,
    history_preview: str = "",
) -> Dict[str, Any]:
    """
    Returns {action, route, reason, raw, latency_ms?} where action is
    keep | rag | reroute.
    """
    out: Dict[str, Any] = {
        "action": "keep",
        "route": proposed_route,
        "reason": "skipped",
        "raw": "",
        "called": False,
    }
    if not should_review(proposed_route, debug=debug):
        return out

    # Demo-safe: referee may only KEEP the proposed route or send to RAG
    # (never jump to an unrelated tool like cancel/booking).
    soft_only = proposed_route in _RAG_OR_KEEP
    allowed = sorted({proposed_route, "rag"})
    out["called"] = True
    out["model"] = cheap_llm.arbiter_model()
    score_block = "\n".join(_top_scores(scores)) or f"- {proposed_route}"
    hist = (history_preview or "").strip()
    hist_block = f"\nRecent chat:\n{hist[-500:]}\n" if hist else ""
    rule_hints = []
    dbg = debug or {}
    if dbg.get("conflicts"):
        rule_hints.append(f"conflicts={dbg.get('conflicts')}")
    if dbg.get("prototype_top"):
        rule_hints.append(f"prototypes={dbg.get('prototype_top')[:3]}")
    if dbg.get("label"):
        rule_hints.append(f"label={dbg.get('label')}")
    hints = ("; ".join(rule_hints)) if rule_hints else "regex/prototype scores only"
    soft_note = (
        "This proposed route is a soft short-circuit. Only KEEP or RAG — never invent other routes.\n"
        if soft_only
        else ""
    )

    prompt = (
        "You are a routing referee for BrightNest (COMP6708 live demo chatbot).\n"
        "A cheap keyword/prototype router proposed a route. Decide if that is a good fit "
        "for the parent's question, or if RAG / another route would sound less awkward.\n"
        "Prefer KEEP when the proposed route can answer well (including filtered fact cards).\n"
        "Choose RAG when the question needs narrative KB detail, age×course fit, curriculum, "
        "or the keyword match is incidental (e.g. 'teachers' in a policy question).\n"
        "Do NOT invent other routes — only keep the proposed route or send to rag.\n"
        f"{soft_note}"
        "Reply with ONE JSON object only, no markdown:\n"
        '{"action":"keep"|"rag","route":"<route_id>","reason":"<short>"}\n\n'
        f"Proposed route: {proposed_route} — {_ROUTE_BLURBS.get(proposed_route, '')}\n"
        f"Matcher notes: {hints}\n"
        f"Top scores:\n{score_block}\n"
        f"Allowed routes: {', '.join(allowed)}\n"
        f"{hist_block}"
        f"Parent message: {message}\n"
    )

    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="Return only compact JSON for intent routing. No prose.",
            max_tokens=80,
        )
        if not parsed:
            out["reason"] = "llm_empty"
            return out
        out["raw"] = str(parsed)
        action = str(parsed.get("action") or "keep").strip().lower()
        route = str(parsed.get("route") or proposed_route).strip()
        reason = str(parsed.get("reason") or "").strip()[:160]
        if action == "keep":
            out.update({"action": "keep", "route": proposed_route, "reason": reason or "keep"})
            return out
        if action == "rag" or (action == "reroute" and route == "rag"):
            out.update({"action": "rag", "route": "rag", "reason": reason or "send_to_rag"})
            return out
        # Treat legacy "reroute" to the same proposed route as keep
        if action == "reroute" and route == proposed_route:
            out.update({"action": "keep", "route": proposed_route, "reason": reason or "keep"})
            return out
        out["reason"] = f"invalid_action:{action}"
        return out
    except Exception as e:
        out["reason"] = f"error:{type(e).__name__}"
        print(f"[INTENT_ARBITER] failed: {e}", flush=True)
        return out
