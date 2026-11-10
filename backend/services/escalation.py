"""Detect when the bot should message the centre boss (human hand-off).

Parent-facing copy must never mention internal sheets (admin_todos / staff_messages).
Templates are multilingual and must NOT paste the parent's raw rant into the reply.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

# Explicit request for a human
_ASK_HUMAN_RE = re.compile(
    r"("
    r"\b(speak|talk|chat)\s+(to|with)\s+(a\s+)?(real\s+)?(person|human|someone|somebody|staff|teacher|manager|director|boss|owner|admin)\b"
    r"|\b(real\s+person|human\s+agent|live\s+agent|customer\s+service)\b"
    r"|\b(call\s+me(\s+back)?|call\s+back|phone\s+me|ring\s+me)\b"
    r"|\b(get|put)\s+(me\s+)?(a\s+)?(manager|director|boss|supervisor)\b"
    r"|\bi\s+want\s+(a\s+)?(manager|director|boss|human|person)\b"
    r"|\bcan\s+(someone|somebody|a\s+staff|staff)\s+(call|contact|help|reach)\b"
    r"|\b(message|tell|email|notify)\s+(the\s+)?(centre|center|boss|director|manager|owner)\b"
    r"|轉接|找(個)?人|搵(個)?人|真人|人工客服|聯絡(你哋|你們)?(老細|老板|老闆|主任|老師|職員)"
    r"|請(經理|主任|負責人).{0,6}(打|來電|回覆|聯絡)"
    r")",
    re.I,
)

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

_SAFETY_RE = re.compile(
    r"("
    r"\b(emergency|injured|injury|hurt|bleeding|allergic\s+reaction|anaphyla)\b"
    # "peanut allergy … had a reaction" / "reaction after class" medical urgency
    r"|\ballerg(?:y|ies|ic)\b.{0,80}\breaction\b"
    r"|\breaction\b.{0,60}\b(allerg(?:y|ies|ic)|peanut|nut|class|yesterday|today)\b"
    r"|\b(medical\s+emergency|ambulance|hospitalis|hospitaliz)\b"
    r"|\b(missing|lost)\s+(my\s+)?(child|kid|son|daughter)\b"
    r"|\b(child\s+)?(abuse|unsafe|danger(ous)?)\b"
    r"|受傷|受伤|流血|過敏反應|过敏休克|走失|失踪|危險|危险|緊急|紧急|过敏.*反应|過敏.*反應"
    r")",
    re.I,
)

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

_TEMPLATES = {
    "en": {
        "safety": (
            "I've let our team know about this safety concern right away. "
            "Somebody from BrightNest will reach out as soon as possible — "
            "if this is a medical emergency, please also call local emergency services."
        ),
        "ask_human": (
            "I've let our team know you'd like to speak with a staff member. "
            "Somebody will reach out to you during working hours."
        ),
        "angry": (
            "I'm sorry this has been frustrating. I've let our team know so they can "
            "follow up with you personally. Somebody will reach out during working hours."
        ),
        "hard_conflict": (
            "I've passed this to our team so they can review it with you directly. "
            "Somebody will reach out during working hours."
        ),
    },
    "zh-HK": {
        "safety": (
            "我已經即時通知團隊有關安全事宜。BrightNest 職員會盡快聯絡你——"
            "如屬醫療緊急情況，請同時致電本地緊急服務。"
        ),
        "ask_human": (
            "我已經通知團隊，話你想同職員傾。職員會喺辦公時間內聯絡你。"
        ),
        "angry": (
            "對唔住令你唔開心。我已經通知團隊親自跟進，職員會喺辦公時間內聯絡你。"
        ),
        "hard_conflict": (
            "我已經交俾團隊直接同你跟進，職員會喺辦公時間內聯絡你。"
        ),
    },
    "zh-CN": {
        "safety": (
            "我已立即通知团队有关安全事宜。BrightNest 职员会尽快联系您——"
            "如属医疗紧急情况，请同时拨打本地紧急服务电话。"
        ),
        "ask_human": (
            "我已通知团队，说明您希望与职员沟通。职员会在办公时间内联系您。"
        ),
        "angry": (
            "很抱歉让您感到不满。我已通知团队亲自跟进，职员会在办公时间内联系您。"
        ),
        "hard_conflict": (
            "我已转交团队直接与您跟进，职员会在办公时间内联系您。"
        ),
    },
}


def detect_escalation(text: str) -> Optional[Dict[str, Any]]:
    """Return {category, priority, summary} or None."""
    t = (text or "").strip()
    if not t:
        return None

    if _SAFETY_RE.search(t):
        return {
            "category": "safety",
            "priority": "urgent",
            "summary": "Urgent: possible child safety or medical concern — please call the parent now",
        }
    # Anger / hard conflict before ask_human so "manager + unacceptable" stays complaint-toned
    if _ANGRY_RE.search(t):
        return {
            "category": "angry",
            "priority": "high",
            "summary": "Follow up: parent is upset and may escalate a complaint",
        }
    if _HARD_CONFLICT_RE.search(t):
        return {
            "category": "hard_conflict",
            "priority": "high",
            "summary": "Follow up: refund, withdrawal, or public-complaint request needs a staff decision",
        }
    if _ASK_HUMAN_RE.search(t):
        return {
            "category": "ask_human",
            "priority": "high",
            "summary": "Follow up: parent asked to speak with a staff member",
        }
    return None


def _lang_bucket(lang: Optional[str]) -> str:
    L = (lang or "en").lower()
    if L.startswith("zh-hk"):
        return "zh-HK"
    if L.startswith("zh"):
        return "zh-CN"
    return "en"


def parent_reply(
    *,
    category: str,
    topic_hint: str = "",
    lang: Optional[str] = None,
) -> str:
    """Parent-safe copy — never mentions internal sheets; never pastes raw rant."""
    del topic_hint  # kept for call-site compat; staff summary uses raw message separately
    bucket = _lang_bucket(lang)
    table = _TEMPLATES.get(bucket) or _TEMPLATES["en"]
    key = category if category in table else "ask_human"
    return table[key]


def topic_hint_from_message(text: str, category: str) -> str:
    """Short label for staff inbox only — not interpolated into parent reply."""
    raw = re.sub(r"\s+", " ", (text or "").strip())
    if len(raw) > 90:
        raw = raw[:87].rstrip() + "…"
    if category == "ask_human":
        return "request to speak with staff"
    if category == "safety":
        return raw or "safety concern"
    if category == "angry":
        return "complaint / upset parent"
    if category == "hard_conflict":
        return "refund / withdrawal pressure"
    return raw or "parent request"
