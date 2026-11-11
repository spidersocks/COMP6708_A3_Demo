"""Cheap-LLM action policy for turns the detectors leave on RAG.

Detectors stay the fast path. This module is the long-tail authority:
it may promote a RAG default into a tool route, and it may reject a draft
that claims a booking or cancellation no tool actually wrote.

It does not add phrase lists for routing. Fail-open: empty / low confidence
stays RAG and the draft is kept.

Latency: both LLM calls are gated. Clear info questions skip classify;
drafts without commit-shaped language skip audit. Near-miss detector scores
and tool-ish chat history force a classify call.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

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

# Detector near-miss: still worth a cheap classify even if RAG won.
_NEAR_MISS = 0.12
_TOOL_SCORE_KEYS = (
    "booking_dialogue",
    "booking_tool",
    "cancel_dialogue",
    "opening_hours",
    "severe_weather",
    "staff_escalation",
)

# Structural info-question openers (not topic phrase lists).
_INFO_OPENERS = (
    "what ",
    "when ",
    "where ",
    "which ",
    "who ",
    "why ",
    "how ",
    "do ",
    "does ",
    "did ",
    "is ",
    "are ",
    "can ",
    "could ",
    "will ",
    "would ",
    "may ",
    "should ",
    "多少",
    "什么",
    "什麼",
    "怎",
    "是否",
    "有没有",
    "有沒有",
    "请问",
    "請問",
)

# Cheap prefilter before commit_audit LLM — broad claim shapes only.
_COMMIT_CUE_RE = re.compile(
    r"("
    r"\b(booked|reserved|confirmed|cancelled|canceled|registered)\b|"
    r"\b(i('ve| have)|we('ve| have)|you('re| are)|you('ve| have))\s+"
    r"(booked|reserved|cancelled|canceled|confirmed|removed|registered)\b|"
    r"\bon the (register|roster)\b|"
    r"\bremoved (you|your|the)\b|"
    r"\ball set for\b|"
    r"已(经|經)?(预约|預約|报名|報名|取消|预订|預訂)|"
    r"已经(预约|預約|报名|報名|取消)|"
    r"幫你(預約|取消|報名)|帮你(预约|取消|报名)"
    r")",
    re.I,
)

# History that suggests an ongoing tool dialogue — force classify.
_HISTORY_TOOL_RE = re.compile(
    r"("
    r"\b(book|booking|trial|cancel|seat|slot|confirm|reserve|enrol|enroll|"
    r"assessment|placement|waitlist)\b|"
    r"試堂|取消|預約|报名|報名|評估"
    r")",
    re.I,
)


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


def looks_like_info_question(message: str) -> bool:
    """Structural FAQ shape — ends with ? or interrogative opener."""
    t = (message or "").strip()
    if not t:
        return False
    if t.endswith("?") or t.endswith("？"):
        return True
    low = t.lower()
    return any(low.startswith(p) for p in _INFO_OPENERS)


def _near_miss_tools(scores: Optional[Dict[str, Any]]) -> Optional[str]:
    if not scores:
        return None
    best_k = None
    best_v = 0.0
    for k in _TOOL_SCORE_KEYS:
        try:
            v = float(scores.get(k) or 0)
        except (TypeError, ValueError):
            continue
        if v >= _NEAR_MISS and v > best_v:
            best_v = v
            best_k = k
    return best_k


def should_classify(
    message: str,
    *,
    history_preview: str = "",
    scores: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """
    Gate the action_policy LLM. Fail-open toward calling when unsure.
    Skip only clear informational questions with no tool-ish context.
    """
    text = (message or "").strip()
    if not text:
        return False, "empty"
    hist = (history_preview or "").strip()
    if hist and _HISTORY_TOOL_RE.search(hist):
        return True, "history_cues"
    near = _near_miss_tools(scores)
    if near:
        return True, f"near_miss:{near}"
    if looks_like_info_question(text):
        return False, "info_question"
    return True, "default"


def should_audit(answer: str) -> Tuple[bool, str]:
    """Gate commit_audit LLM. Skip drafts with no commit-shaped language."""
    text = (answer or "").strip()
    if len(text) < 12:
        return False, "short"
    if not _COMMIT_CUE_RE.search(text):
        return False, "no_commit_cues"
    return True, "commit_cues"


def classify_turn(
    message: str,
    *,
    history_preview: str = "",
    scores: Optional[Dict[str, Any]] = None,
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

    run, gate = should_classify(
        text, history_preview=history_preview, scores=scores
    )
    out["gate"] = gate
    if not run:
        out["reason"] = f"skipped:{gate}"
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
    run, gate = should_audit(text)
    out["gate"] = gate
    if not run:
        out["reason"] = f"skipped:{gate}"
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
