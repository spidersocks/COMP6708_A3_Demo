"""Cheap-LLM action policy for turns the detectors leave on RAG.

Detectors stay the fast path. This module is the long-tail authority:
it may promote a RAG default into a tool route, and it may reject a draft
that claims a booking or cancellation no tool actually wrote.

It does not add phrase lists. Fail-open: empty / low confidence stays RAG
and the draft is kept.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from services import cheap_llm

_ACT_ROUTE = {
    "answer": "rag",
    "book": "booking_dialogue",
    "availability": "booking_tool",
    "cancel": "cancel_dialogue",
    "hours": "opening_hours",
    "weather": "severe_weather",
    "escalate": "staff_escalation",
}

# Promote only when the model is clearly asking for a tool.
PROMOTE_AT = 0.62
# Discard a RAG draft only when it clearly claims a finished commit.
COMMIT_AT = 0.75


def enabled() -> bool:
    flag = os.environ.get("BRIGHTNEST_ACTION_POLICY", "1").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return cheap_llm.enabled()


def _history_block(history_preview: str) -> str:
    hist = (history_preview or "").strip()
    if not hist:
        return ""
    return f"Recent chat:\n{hist[-700:]}\n"


def classify_turn(
    message: str,
    *,
    history_preview: str = "",
) -> Dict[str, Any]:
    """
    Returns act/route/confidence/reason/called.
    act: answer | book | availability | cancel | hours | weather | escalate
    """
    out: Dict[str, Any] = {
        "called": False,
        "act": "answer",
        "route": "rag",
        "confidence": 0.0,
        "reason": "skipped",
    }
    if not enabled():
        out["reason"] = "disabled"
        return out
    text = (message or "").strip()
    if not text:
        out["reason"] = "empty"
        return out

    out["called"] = True
    out["model"] = cheap_llm.arbiter_model()
    prompt = (
        "You route a parents' WhatsApp for a kids' tuition centre.\n"
        "Pick ONE act for the parent's latest message, using the chat if it changes the meaning.\n"
        "answer — question about programmes, fees, policy, teachers, ages, or anything we should explain from notes.\n"
        "book — they want to start, try, join, enrol, reserve, or lock a class, trial, or placement "
        "(even with no word 'book').\n"
        "availability — they only want the open seat times, not a policy answer. "
        "Waiting lists, fees, and \"is a trial required\" are answer.\n"
        "cancel — they want to drop, move off, or undo a class already held.\n"
        "hours — open/closed times or a specific day's opening, not a class seat.\n"
        "weather — typhoon, rainstorm, or signal and whether class runs.\n"
        "escalate — they want a human, manager, or urgent safety follow-up.\n"
        "A policy question about whether a trial is required is answer, not book.\n"
        'JSON: {"act":"answer|book|availability|cancel|hours|weather|escalate",'
        '"confidence":0.0,"reason":"short"}\n\n'
        f"{_history_block(history_preview)}"
        f"Parent: {text}\n"
    )
    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="JSON only. One act.",
            max_tokens=80,
            role="action_policy",
        )
    except Exception as e:
        out["reason"] = f"error:{type(e).__name__}"
        return out
    if not parsed:
        out["reason"] = "llm_empty"
        return out
    act = str(parsed.get("act") or "answer").strip().lower()
    if act not in _ACT_ROUTE:
        act = "answer"
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    reason = str(parsed.get("reason") or "").strip()[:160]
    out.update(
        {
            "act": act,
            "route": _ACT_ROUTE[act],
            "confidence": conf,
            "reason": reason or act,
            "raw": parsed,
        }
    )
    return out


def should_promote(verdict: Optional[Dict[str, Any]]) -> bool:
    if not verdict or not verdict.get("called"):
        return False
    route = str(verdict.get("route") or "rag")
    if route == "rag" or route not in _ACT_ROUTE.values():
        return False
    try:
        conf = float(verdict.get("confidence") or 0)
    except (TypeError, ValueError):
        return False
    return conf >= PROMOTE_AT


def audit_draft(answer: str) -> Dict[str, Any]:
    """
    Does this parent-facing draft claim a booking or cancellation already happened?
    commit: none | booked | cancelled
    """
    out: Dict[str, Any] = {
        "called": False,
        "commit": "none",
        "confidence": 0.0,
        "reason": "skipped",
    }
    if not enabled():
        out["reason"] = "disabled"
        return out
    text = (answer or "").strip()
    if len(text) < 12:
        out["reason"] = "short"
        return out
    out["called"] = True
    out["model"] = cheap_llm.arbiter_model()
    prompt = (
        "A centre bot drafted this WhatsApp reply. Did it claim a finished action?\n"
        "booked — a specific seat, trial, or assessment is already reserved / on the register.\n"
        "cancelled — a class was already removed.\n"
        "none — explanation, an offer to book, a question, or a list of options still needing a yes.\n"
        "Describing that booking is possible, or asking the parent to confirm, is none.\n"
        'JSON: {"commit":"none|booked|cancelled","confidence":0.0,"reason":"short"}\n\n'
        f"Draft:\n{text[:900]}\n"
    )
    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="JSON only. Judge the draft, not a new reply.",
            max_tokens=70,
            role="commit_audit",
        )
    except Exception as e:
        out["reason"] = f"error:{type(e).__name__}"
        return out
    if not parsed:
        out["reason"] = "llm_empty"
        return out
    commit = str(parsed.get("commit") or "none").strip().lower()
    if commit not in {"none", "booked", "cancelled"}:
        commit = "none"
    try:
        conf = float(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))
    out.update(
        {
            "commit": commit,
            "confidence": conf,
            "reason": str(parsed.get("reason") or "").strip()[:160] or commit,
            "raw": parsed,
        }
    )
    return out


def draft_is_fake_commit(verdict: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return 'booked' or 'cancelled' when the draft must not be shown."""
    if not verdict or not verdict.get("called"):
        return None
    commit = str(verdict.get("commit") or "none")
    if commit not in {"booked", "cancelled"}:
        return None
    try:
        conf = float(verdict.get("confidence") or 0)
    except (TypeError, ValueError):
        return None
    if conf < COMMIT_AT:
        return None
    return commit
