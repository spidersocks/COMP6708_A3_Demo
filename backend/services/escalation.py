"""Detect when the bot should message the centre boss (human hand-off).

Parent-facing copy must never mention internal sheets (admin_todos / staff_messages).
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Explicit request for a human
_ASK_HUMAN_RE = re.compile(
    r"("
    r"\b(speak|talk|chat)\s+(to|with)\s+(a\s+)?(real\s+)?(person|human|someone|somebody|staff|teacher|manager|director|boss|owner|admin)\b"
    r"|\b(real\s+person|human\s+agent|live\s+agent|customer\s+service)\b"
    r"|\b(call\s+me(\s+back)?|phone\s+me|ring\s+me)\b"
    r"|\b(get|put)\s+(me\s+)?(a\s+)?(manager|director|boss|supervisor)\b"
    r"|\bi\s+want\s+(a\s+)?(manager|director|boss|human|person)\b"
    r"|\bcan\s+(someone|somebody|a\s+staff|staff)\s+(call|contact|help|reach)\b"
    r"|\b(message|tell|email|notify)\s+(the\s+)?(centre|center|boss|director|manager|owner)\b"
    r"|轉接|找(個)?人|搵(個)?人|真人|人工客服|聯絡(你哋|你們)?(老細|老板|老闆|主任|老師|職員)"
    r"|請(經理|主任|負責人).{0,6}(打|來電|回覆|聯絡)"
    r")",
    re.I,
)

# Anger / severe dissatisfaction
_ANGRY_RE = re.compile(
    r"("
    r"\b(furious|outraged|disgusted|unacceptable|ridiculous|scam|ripoff|rip[\s-]?off)\b"
    r"|\b(worst|terrible|horrible|awful)\s+(service|experience|centre|center|school)\b"
    r"|\bi('m| am)\s+(so\s+)?(angry|furious|upset|disappointed|fed\s+up)\b"
    r"|\b(this\s+is\s+)?(a\s+)?(joke|waste\s+of\s+(my\s+)?money)\b"
    r"|\b(sue|lawyer|legal\s+action|consumer\s+council)\b"
    r"|好嬲|好憤怒|離譜|仆街|垃圾服務|投訴你哋|投訴你们|消費者委員會|消委会|律师|律師"
    r")",
    re.I,
)

# Safety / urgent welfare
_SAFETY_RE = re.compile(
    r"("
    r"\b(emergency|injured|injury|hurt|bleeding|allergic\s+reaction|anaphyla)\b"
    r"|\b(missing|lost)\s+(my\s+)?(child|kid|son|daughter)\b"
    r"|\b(child\s+)?(abuse|unsafe|danger(ous)?)\b"
    r"|受傷|受伤|流血|過敏反應|过敏休克|走失|失踪|危險|危险|緊急|紧急"
    r")",
    re.I,
)

# Hard commercial conflict that should not stay with the bot alone
_HARD_CONFLICT_RE = re.compile(
    r"("
    r"\b(full\s+)?refund\s+(now|immediately|everything|all)\b"
    r"|\bi\s+want\s+(my\s+)?money\s+back\b"
    r"|\b(cancel\s+everything|withdraw\s+(my\s+)?(child|kid)|never\s+coming\s+back)\b"
    r"|\b(report\s+you|leave\s+a\s+bad\s+review|one[\s-]?star)\b"
    r"|立刻退款|全部退款|我要退學|我要退学|永久取消|差評|差评"
    r")",
    re.I,
)


def detect_escalation(text: str) -> Optional[Dict[str, Any]]:
    """Return {category, priority, summary} or None."""
    t = (text or "").strip()
    if not t:
        return None

    if _SAFETY_RE.search(t):
        return {
            "category": "safety",
            "priority": "urgent",
            "summary": "Possible child safety / medical urgency — staff must contact the parent ASAP.",
        }
    if _ASK_HUMAN_RE.search(t):
        return {
            "category": "ask_human",
            "priority": "high",
            "summary": "Parent asked to speak with a real staff member / manager.",
        }
    if _ANGRY_RE.search(t):
        return {
            "category": "angry",
            "priority": "high",
            "summary": "Parent sounds very upset or is escalating a complaint — boss/staff should follow up.",
        }
    if _HARD_CONFLICT_RE.search(t):
        return {
            "category": "hard_conflict",
            "priority": "high",
            "summary": "Refund / withdrawal / public-complaint pressure — needs a human decision.",
        }
    return None


def parent_reply(*, category: str, topic_hint: str = "") -> str:
    """Parent-safe copy — never mentions internal admin sheets."""
    topic = (topic_hint or "your message").strip()
    if category == "safety":
        return (
            f"I've let our team know about {topic} right away. "
            "Somebody from BrightNest will reach out to you as soon as possible — "
            "if this is a medical emergency, please also call local emergency services."
        )
    if category == "ask_human":
        return (
            f"I've let our team know about {topic}. "
            "Somebody will reach out to you during working hours."
        )
    if category == "angry":
        return (
            "I'm sorry this has been frustrating. "
            f"I've let our team know about {topic}. "
            "Somebody will reach out to you during working hours to help sort this out."
        )
    # hard_conflict / default
    return (
        f"I've let our team know about {topic}. "
        "Somebody will reach out to you during working hours."
    )


def topic_hint_from_message(text: str, category: str) -> str:
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if len(raw) > 90:
        raw = raw[:87].rstrip() + "…"
    if category == "ask_human":
        return "your request to speak with our team"
    if category == "safety":
        return raw or "a safety concern"
    if category == "angry":
        return raw or "your concern"
    return raw or "your request"
