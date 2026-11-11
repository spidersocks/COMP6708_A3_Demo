"""Cheap DashScope second-look on ambiguous intent picks (demo-oriented).

Hard detectors propose a route; only close races ask a small LLM to KEEP or
send to RAG. Clear detector wins skip this module entirely.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from config import SETTINGS
from services import cheap_llm

# Tool routes worth reviewing (keyword misfires look awkward live).
_REVIEWABLE: Set[str] = {
    "opening_hours",
    "severe_weather",
    "booking_tool",
    "booking_dialogue",
    "cancel_dialogue",
}

# Never second-guess these mid-flight / safety paths.
_SKIP: Set[str] = {
    "staff_escalation",
    "rag",
}

_ROUTE_BLURBS: Dict[str, str] = {
    "opening_hours": "Deterministic hours / holiday / weather-hours engine.",
    "severe_weather": "Typhoon / rainstorm closure policy + live HKO.",
    "booking_tool": "Show open seats / availability list.",
    "booking_dialogue": "Multi-turn book-a-class dialogue.",
    "cancel_dialogue": "Multi-turn cancel-a-class dialogue.",
    "rag": "Retrieve centre KB chunks and write a tailored answer.",
    "staff_escalation": "Hand off to a human / boss inbox.",
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
    if route not in _REVIEWABLE:
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
    Returns {action, route, reason, raw, latency_ms?} where action is keep | rag.
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
    allowed = sorted({proposed_route, "rag"})
    out["called"] = True
    out["model"] = cheap_llm.arbiter_model()
    score_block = "\n".join(_top_scores(scores)) or f"- {proposed_route}"
    hist = (history_preview or "").strip()
    hist_block = f"\nRecent chat:\n{hist[-500:]}\n" if hist else ""
    dbg = debug or {}
    hints = (
        f"conflicts={dbg.get('conflicts')}"
        if dbg.get("conflicts")
        else "detector scores"
    )

    prompt = (
        "Ambiguous route pick. KEEP if the tool fits; RAG if the match is incidental "
        "or needs KB narrative. Only keep|rag — no other routes.\n"
        'JSON: {"action":"keep"|"rag","route":"<id>","reason":"<short>"}\n\n'
        f"Proposed: {proposed_route} — {_ROUTE_BLURBS.get(proposed_route, '')}\n"
        f"Notes: {hints}\n"
        f"Scores:\n{score_block}\n"
        f"Allowed: {', '.join(allowed)}\n"
        f"{hist_block}"
        f"Parent: {message}\n"
    )

    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="JSON route keep|rag only.",
            max_tokens=80,
            role="intent_arbiter",
        )
        if not parsed:
            out["reason"] = "llm_empty"
            return out
        out["raw"] = str(parsed)
        action = str(parsed.get("action") or "keep").strip().lower()
        reason = str(parsed.get("reason") or "").strip()[:160]
        if action == "keep":
            out.update({"action": "keep", "route": proposed_route, "reason": reason or "keep"})
            return out
        if action == "rag":
            out.update({"action": "rag", "route": "rag", "reason": reason or "send_to_rag"})
            return out
        out["reason"] = f"invalid_action:{action}"
        return out
    except Exception as e:
        out["reason"] = f"error:{type(e).__name__}"
        print(f"[INTENT_ARBITER] failed: {e}", flush=True)
        return out
