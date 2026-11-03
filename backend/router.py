"""HTTP routes for BrightNest chatbot demo."""
from __future__ import annotations

import re
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from config import SETTINGS
from llm.chat_history import (
    build_context_string,
    clear_history,
    get_recent_history,
    prune_history,
    save_message,
)
from llm.hours_intent import (
    effective_hours_message,
    is_general_hours_query,
    is_hours_followup,
    recent_was_hours,
)
from llm.lang import get_language_code
from llm.opening_hours import _get_opening_facts, compute_opening_answer
from rag.generator import adapt_structured_fact
from rag.pipeline import chat_with_rag, is_greeting
from services import admin_todos
from services import answer_cache
from services import booking as booking_svc
from services import booking_dialogue as dialogue_svc
from services import cancel_dialogue as cancel_svc
from services import centre_facts
from services import demo_polish
from services import escalation as escalation_svc
from services import families
from services import intent_router
from services import staff_messages

# Extra canned providers that get a main-LLM fact rewrite (centre_facts always does).
# Keep this short — soft refuses / greetings stay template-clean for the demo.
_ADAPT_PROVIDERS = {
    "deterministic_opening_hours",
    "unsupported_facility",
    "booking_tool",
}

router = APIRouter()

_CONFUSED_REPLY = (
    "Happy to help — could you tell me a bit more? "
    "You can ask about opening hours, programmes, fees, trial assessments, "
    "availability, or ask to speak with our team."
)
_OFFERED_PROGRAMMES = (
    "Phonics, Mandarin, Writing, Early Math, Playgroup, and Trial assessments"
)
_GREETING_REPLY = (
    "Hello! Welcome to **BrightNest Learning Centre**.\n\n"
    "How can I help you today? You can ask about opening hours, our programmes "
    "(Phonics, Mandarin, Writing, Early Math, Playgroup, Trial), booking or cancelling "
    "a class, or fees and policies."
)

# Avoid matching "opening hours" — use open slots / availability / book / trial.
_AVAIL_RE = re.compile(
    r"\b(availab(?:le|ility)?|time\s*slots?|open\s+slots?|open\s+seats?|bookings?\b|book\s+a\b|"
    r"schedule|timetable|trial\s*class|"
    r"有冇位|有没有位|空位|預約|预约|試堂|试听|档期|檔期)\b",
    re.IGNORECASE,
)
_COURSE_HINTS = [
    (re.compile(r"phonic|拼音", re.I), "phonics"),
    (re.compile(r"mandarin|putonghua|普通話|普通话", re.I), "mandarin"),
    (re.compile(r"writ|寫作|写作", re.I), "writing"),
    (re.compile(r"play\s*group|遊戲班|游戏班", re.I), "playgroup"),
    (re.compile(r"\bmath\b|數學|数学|numeracy", re.I), "math"),
    (re.compile(r"trial|試堂|试听|assessment|評估|评估", re.I), "trial"),
]
_TEACHER_HINTS = [
    (re.compile(r"\b(avery|ms\.?\s*chan)\b", re.I), "avery"),
    (re.compile(r"\b(wei|mr\.?\s*lin)\b", re.I), "wei"),
    (re.compile(r"\b(priya|ms\.?\s*das)\b", re.I), "priya"),
    (re.compile(r"\b(maya|ms\.?\s*cheung)\b", re.I), "maya"),
    (re.compile(r"\b(jordan|mr\.?\s*blake)\b", re.I), "jordan"),
    (re.compile(r"\b(hana|ms\.?\s*zhou)\b", re.I), "hana"),
    (re.compile(r"\b(tom|mr\.?\s*reyes)\b", re.I), "tom"),
    (re.compile(r"\b(sofia|ms\.?\s*almeida)\b", re.I), "sofia"),
]


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    language: Optional[str] = None
    debug: bool = True  # dashboard always wants trace
    # WhatsApp-number identity for conversational booking
    phone: Optional[str] = None
    parent_name: Optional[str] = None
    child_name: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    citations: List[Dict[str, Any]] = Field(default_factory=list)
    provider: Optional[str] = None
    slots: Optional[List[Dict[str, Any]]] = None
    booking: Optional[Dict[str, Any]] = None
    session_id: Optional[str] = None
    debug: Optional[Dict[str, Any]] = None
    trace: Optional[Dict[str, Any]] = None


class BookingRequest(BaseModel):
    course_key: str = "trial"
    slot_start: str
    parent_name: str = ""
    child_name: str = ""
    phone: str = ""
    notes: str = ""


def _detect_course(text: str) -> str:
    for pat, key in _COURSE_HINTS:
        if pat.search(text or ""):
            return key
    return "trial"


def _detect_teacher(text: str) -> Optional[str]:
    for pat, key in _TEACHER_HINTS:
        if pat.search(text or ""):
            return key
    return None


def _session_id(req: ChatRequest) -> str:
    sid = (req.session_id or "").strip()
    return sid or f"web:{uuid.uuid4().hex[:12]}"


def _serialize_hours_facts(facts: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe opening-hours facts for the demo dashboard."""
    dt = facts.get("datetime")
    open_t = facts.get("open_time")
    close_t = facts.get("close_time")
    return {
        "datetime": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
        "lang": facts.get("lang"),
        "open_time": open_t.strftime("%H:%M") if open_t else None,
        "close_time": close_t.strftime("%H:%M") if close_t else None,
        "is_sunday": bool(facts.get("is_sunday")),
        "is_holiday": bool(facts.get("is_holiday")),
        "holiday_name": facts.get("holiday_name"),
        "weather_hint": facts.get("weather_hint"),
        "asked_specific_time": bool(facts.get("asked_specific_time")),
        "is_general_query": bool(facts.get("is_general_query")),
        "parse_debug": facts.get("parse_debug") or {},
    }


def _step(step_id: str, label: str, status: str, detail: str = "") -> Dict[str, str]:
    return {"id": step_id, "label": label, "status": status, "detail": detail}


def _llm_trace_summary(
    routing_debug: Dict[str, Any],
    steps: List[Dict[str, str]],
    answer_provider: str,
) -> Dict[str, Any]:
    """Compact inspector payload: what the cheap vs main model did this turn."""
    arb = (routing_debug or {}).get("arbiter") or {}
    uses: List[str] = []
    for s in steps:
        if s.get("status") != "done":
            continue
        sid = s.get("id") or ""
        if sid == "intent_arbiter":
            uses.append("intent_review")
        elif sid == "gap_polish":
            uses.append("gap_polish")
        elif sid == "cancel_pick":
            uses.append("cancel_pick")
        elif sid == "booking_parse":
            uses.append("booking_parse")
        elif sid == "adapt":
            uses.append("fact_rewrite")
    cheap_called = bool(arb.get("called")) or any(
        s.get("id") in {"gap_polish", "cancel_pick", "booking_parse"}
        and s.get("status") in {"done", "error"}
        for s in steps
    )
    cheap_model = arb.get("model") or (
        SETTINGS.intent_arbiter_model if cheap_called else None
    )
    return {
        "answer_provider": answer_provider,
        "main_model": SETTINGS.llm_model,
        "cheap_model": cheap_model,
        "cheap_called": cheap_called,
        "uses": uses,
        "same_workspace_model": bool(
            SETTINGS.llm_model and cheap_model and cheap_model == SETTINGS.llm_model
        ),
    }


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    t0 = time.perf_counter()
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message is required")

    lang = req.language or get_language_code(message) or "en"
    session_id = _session_id(req)
    use_history = SETTINGS.chat_history_enabled and bool(session_id)

    steps: List[Dict[str, str]] = [
        _step("lang", "Language detect", "done", lang),
    ]

    history: List[Dict[str, Any]] = []
    if use_history:
        try:
            history = get_recent_history(
                session_id,
                limit=SETTINGS.chat_history_limit,
                oldest_first=True,
            )
            steps.append(
                _step(
                    "history",
                    "Session memory",
                    "done",
                    f"{len(history)}/{SETTINGS.chat_history_limit} msgs",
                )
            )
        except Exception as e:
            print(f"[CHAT] history load failed: {e}", flush=True)
            history = []
            steps.append(_step("history", "Session memory", "error", str(e)[:80]))
    else:
        steps.append(_step("history", "Session memory", "skipped", "disabled"))

    history_context = build_context_string(
        history,
        new_message=message,
        include_new=True,
    )

    phone = (req.phone or "").strip() or "+852 9000 0000"
    parent_name = (req.parent_name or "").strip()
    child_name = (req.child_name or "").strip()

    # Hours follow-up promotion (tomorrow?) before scored router
    hours_msg = message
    followup_promoted = False
    decision = intent_router.decide_intent(
        message, lang=lang, session_id=session_id, history=history
    )
    if (
        SETTINGS.opening_hours_enabled
        and decision.route == "rag"
        and is_hours_followup(message)
        and recent_was_hours(history)
    ):
        followup_promoted = True
        hours_msg = effective_hours_message(message, history)
        decision = intent_router.IntentDecision(
            "opening_hours",
            {"hours": 1.0, "followup": 1.0},
            {"followup_promoted": True},
        )

    # Leaving sticky booking for off-topic / fees / etc.
    dialogue_active = dialogue_svc.is_active(session_id)
    cancel_active = cancel_svc.is_active(session_id)
    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    if leave_booking and dialogue_active and decision.route not in {
        "booking_dialogue",
        "booking_tool",
        "cancel_dialogue",
    }:
        dialogue_svc.clear_state(session_id)
        dialogue_active = False

    route = decision.route
    steps.append(
        _step(
            "intent",
            "Intent router",
            "done",
            f"{route} · scores={decision.scores}",
        )
    )
    arb = (decision.debug or {}).get("arbiter") or {}
    if arb:
        override = (decision.debug or {}).get("arbiter_override")
        called = bool(arb.get("called"))
        reason = str(arb.get("reason") or "")
        if not called or reason == "skipped":
            arb_status = "skipped"
        elif reason.startswith("error") or reason in {"llm_empty", "parse_fail"}:
            arb_status = "error"
        else:
            arb_status = "done"
        if override:
            detail = (
                f"{override.get('from')} → {override.get('to')} · "
                f"{arb.get('reason') or arb.get('action')}"
            )
        else:
            detail = f"{arb.get('action') or 'keep'} · {arb.get('reason') or 'n/a'}"
            if arb.get("model"):
                detail = f"{detail} · {arb['model']}"
        steps.append(
            _step(
                "intent_arbiter",
                "Cheap LLM intent review",
                arb_status,
                detail,
            )
        )

    trace: Dict[str, Any] = {
        "language": lang,
        "session_id": session_id,
        "history": {
            "enabled": use_history,
            "turns_loaded": len(history),
            "limit": SETTINGS.chat_history_limit,
            "preview": (
                build_context_string(history, include_new=False)[-600:] if history else ""
            ),
        },
        "routing": {
            "route": route,
            "scores": decision.scores,
            "debug": decision.debug,
            "hours_followup_promoted": followup_promoted,
            "dialogue_active": dialogue_active,
            "cancel_active": cancel_active,
            "left_booking": leave_booking and not dialogue_active,
        },
        "pipeline_steps": steps,
    }

    def _family_names() -> tuple[str, str]:
        fam: Dict[str, Any] = {}
        try:
            fam = families.list_family_by_phone(phone) or {}
        except Exception:
            fam = {}
        p = parent_name or fam.get("parent_name") or ""
        c = child_name
        if not c:
            kids = fam.get("children") or []
            if len(kids) == 1:
                c = kids[0].get("child_name") or ""
        return p, c

    def _fact_response(
        *,
        route_name: str,
        answer: str,
        citations: List[Dict[str, Any]],
        provider: str,
        extra_step: Optional[Dict[str, str]] = None,
        slots: Optional[List[Dict[str, Any]]] = None,
        debug: Optional[Dict[str, Any]] = None,
        adapt: bool = True,
    ) -> ChatResponse:
        if extra_step:
            steps.append(extra_step)
        final = answer
        final_provider = provider
        # Demo fluency: always polish centre_facts; optionally other stiff templates
        should_adapt = adapt and (
            provider == "centre_facts"
            or (SETTINGS.arbiter_adapt_extra and provider in _ADAPT_PROVIDERS)
        )
        if should_adapt:
            polished = adapt_structured_fact(
                message,
                answer,
                lang=lang,
                history_context=history_context,
                route=route_name,
            )
            if polished.get("adapted"):
                final = polished["answer"]
                final_provider = f"{provider}+llm"
                steps.append(
                    _step(
                        "adapt",
                        "Main LLM fact rewrite",
                        "done",
                        f"{SETTINGS.llm_model} · {polished.get('provider') or 'dashscope'}",
                    )
                )
            else:
                steps.append(
                    _step(
                        "adapt",
                        "Main LLM fact rewrite",
                        "skipped",
                        polished.get("provider") or "passthrough",
                    )
                )
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", route_name))
        trace["route"] = route_name
        trace["provider"] = final_provider
        trace["answer_provider"] = final_provider
        trace["llm"] = _llm_trace_summary(decision.debug or {}, steps, final_provider)
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        # Surface dialogue traces the inspector reads from `trace`, not only `debug`
        dbg = debug or {}
        if dbg.get("cancel") and "cancel" not in trace:
            trace["cancel"] = dbg["cancel"]
        if dbg.get("dialogue") and "booking" not in trace:
            trace["booking"] = dbg["dialogue"]
        resp = ChatResponse(
            answer=final,
            citations=citations,
            provider=final_provider,
            slots=slots,
            session_id=session_id,
            debug=debug or {},
            trace=trace,
        )
        _persist_turn(session_id, message, final, lang, use_history)
        return resp

    # --- Deterministic / tool routes (scored intent) ---
    if route == "staff_escalation":
        from services import staff_email

        esc = decision.debug.get("escalation") or escalation_svc.detect_escalation(message)
        todo_parent, todo_child = _family_names()
        topic = escalation_svc.topic_hint_from_message(message, esc["category"])
        answer = escalation_svc.parent_reply(
            category=esc["category"], topic_hint=topic, lang=lang
        )
        drafted = staff_email.draft_boss_email(
            category=esc["category"],
            priority=esc.get("priority") or "normal",
            parent_message=message,
            bot_reply=answer,
            summary=esc.get("summary") or topic,
            phone=phone,
            parent_name=todo_parent,
            child_name=todo_child,
        )
        staff_msg = staff_messages.add_message(
            category=esc["category"],
            priority=esc.get("priority") or "normal",
            parent_message=message,
            summary_for_boss=drafted.get("subject") or esc.get("summary") or topic,
            email_body=drafted.get("body") or "",
            bot_reply=answer,
            phone=phone,
            parent_name=todo_parent,
            child_name=todo_child,
            session_id=session_id,
        )
        staff_msg["email_provider"] = drafted.get("provider")
        if dialogue_active:
            dialogue_svc.clear_state(session_id)
        if cancel_active:
            cancel_svc.clear_state(session_id)
        steps.append(
            _step(
                "staff_email",
                "Draft staff email",
                "done",
                drafted.get("provider") or "template",
            )
        )
        # Inspector reads staff_message from trace
        trace["staff_message"] = staff_msg
        return _fact_response(
            route_name="staff_escalation",
            answer=answer,
            citations=[],
            provider="staff_escalation",
            extra_step=_step(
                "staff_escalate",
                "Message centre boss",
                "done",
                f"{esc['category']} · {staff_msg.get('id')}",
            ),
            debug={"escalation": esc, "staff_message": staff_msg},
            adapt=False,
        )

    if route == "confused":
        return _fact_response(
            route_name="confused",
            answer=_CONFUSED_REPLY,
            citations=[],
            provider="confused",
            extra_step=_step("confused", "Clarify short-circuit", "done", "short ask"),
        )

    if route == "off_domain":
        low_msg = (message or "").lower()
        weatherish = bool(
            re.search(r"\b(weather|temperature|forecast|climate)\b", low_msg)
        )
        off = {
            "en": (
                (
                    "I’m BrightNest’s learning-centre assistant, so I don’t have weather "
                    "forecasts for other cities. Ask about our Hong Kong severe-weather "
                    "class arrangements, programmes, fees, hours, or booking — "
                    "or ask to speak with our team."
                )
                if weatherish
                else (
                    "That’s outside what we can help with as BrightNest’s learning-centre assistant. "
                    "Ask about programmes, fees, hours, location, trials, or booking — "
                    "or ask to speak with our team."
                )
            ),
            "zh-HK": (
                "呢啲唔屬於亮巢學習中心助理可以處理嘅範圍。"
                "你可以問課程、學費、時間、地址、試堂或預約，或者要求同團隊傾。"
            ),
            "zh-CN": (
                "这不属于亮巢学习中心助理可以处理的范围。"
                "你可以问课程、学费、时间、地址、试听或预约，或要求与团队沟通。"
            ),
        }
        L = (lang or "en").lower()
        ans = off["zh-HK"] if L.startswith("zh-hk") else (
            off["zh-CN"] if L.startswith("zh") else off["en"]
        )
        return _fact_response(
            route_name="off_domain",
            answer=ans,
            citations=[],
            provider="off_domain",
            extra_step=_step("off_domain", "Out-of-scope short-circuit", "done", "refuse"),
        )

    if route == "thanks":
        thanks = {
            "en": "You’re welcome! Anything else I can help with — hours, fees, trials, or booking?",
            "zh-HK": "唔該晒！仲有冇想問——營業時間、學費、試堂定預約？",
            "zh-CN": "不客气！还有想问的吗——营业时间、学费、试听或预约？",
        }
        L = (lang or "en").lower()
        ans = thanks["zh-HK"] if L.startswith("zh-hk") else (
            thanks["zh-CN"] if L.startswith("zh") else thanks["en"]
        )
        return _fact_response(
            route_name="thanks",
            answer=ans,
            citations=[],
            provider="thanks",
            extra_step=_step("thanks", "Thanks short-circuit", "done", "ack"),
        )

    if route == "severe_weather":
        cached = answer_cache.get("severe_weather", message, lang)
        if cached:
            answer, cites = cached["answer"], cached["citations"]
        else:
            answer, cites = centre_facts.format_severe_weather_answer(message, lang)
            answer_cache.put(
                "severe_weather", message, lang, {"answer": answer, "citations": cites}
            )
        return _fact_response(
            route_name="severe_weather",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Severe weather policy", "done", "structured"),
        )

    if route == "fees":
        cached = answer_cache.get("fees", message, lang)
        if cached:
            answer, cites = cached["answer"], cached["citations"]
        else:
            answer, cites = centre_facts.format_fees_answer(message, lang)
            answer_cache.put("fees", message, lang, {"answer": answer, "citations": cites})
        return _fact_response(
            route_name="fees",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Published fee guide", "done", "structured"),
        )

    if route == "location":
        answer, cites = centre_facts.format_location_answer(lang)
        return _fact_response(
            route_name="location",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Centre location", "done", "structured"),
        )

    if route == "allergy":
        answer, cites = centre_facts.format_allergy_answer(lang)
        return _fact_response(
            route_name="allergy",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Allergy policy", "done", "structured"),
        )

    if route == "illness":
        answer, cites = centre_facts.format_illness_answer(lang)
        return _fact_response(
            route_name="illness",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Illness / fever policy", "done", "structured"),
        )

    if route == "payment":
        answer, cites = centre_facts.format_payment_answer(lang)
        return _fact_response(
            route_name="payment",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "How to pay", "done", "structured"),
        )

    if route == "teachers":
        answer, cites = centre_facts.format_teachers_answer(lang, message=message)
        return _fact_response(
            route_name="teachers",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Teachers", "done", "structured"),
        )

    if route == "pickup":
        answer, cites = centre_facts.format_pickup_answer(lang)
        return _fact_response(
            route_name="pickup",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Drop-off & pickup", "done", "structured"),
        )

    if route == "makeup":
        answer, cites = centre_facts.format_makeup_answer(lang)
        return _fact_response(
            route_name="makeup",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Absence & make-up", "done", "structured"),
        )

    if route == "sibling":
        answer, cites = centre_facts.format_sibling_answer(lang)
        return _fact_response(
            route_name="sibling",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Sibling discount", "done", "structured"),
        )

    if route == "guest_policy":
        answer, cites = centre_facts.format_guest_answer(lang)
        # Fluent card already — skip main-model rewrite for latency
        return _fact_response(
            route_name="guest_policy",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Guest / friend policy", "done", "structured"),
            adapt=False,
        )

    if route == "waiting":
        answer, cites = centre_facts.format_waiting_answer(lang)
        return _fact_response(
            route_name="waiting",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Waiting list", "done", "structured"),
        )

    if route == "pets_policy":
        answer, cites = centre_facts.format_pets_policy(lang)
        return _fact_response(
            route_name="pets_policy",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Pets policy", "done", "no pets in class"),
        )

    if route == "unsupported_facility":
        label = (decision.debug.get("label") or "that facility").lower()
        answer = (
            f"We don't offer **{label}** at BrightNest — we're a day learning centre "
            f"for programmes like {_OFFERED_PROGRAMMES}. "
            "If you tell me your child's age, I can suggest a better fit — "
            "or ask to speak with our team and someone will reach out during working hours."
        )
        return _fact_response(
            route_name="unsupported_facility",
            answer=answer,
            citations=[],
            provider="unsupported_facility",
            extra_step=_step("unsupported", "Unsupported facility", "done", label),
        )

    if route == "trial_info":
        # Don't leave a half-open booking loop after a policy answer
        try:
            dialogue_svc.clear_state(session_id)
        except Exception:
            pass
        answer, cites = centre_facts.format_trial_info_answer(lang)
        return _fact_response(
            route_name="trial_info",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Trial assessment info", "done", "policy not slots"),
        )

    if route == "unknown_programme":
        unk = decision.debug.get("label") or dialogue_svc.unknown_programme(message)
        # fall through to existing unknown handler below via reassign
        route = "unknown_programme"
        unknown_programme = unk
    else:
        unknown_programme = dialogue_svc.unknown_programme(message)

    if route == "opening_hours" and SETTINGS.opening_hours_enabled:
        general = is_general_hours_query(hours_msg, lang)
        facts = _get_opening_facts(hours_msg, lang=lang, is_general=general)
        answer = compute_opening_answer(hours_msg, lang=lang, is_general=general)
        steps.append(
            _step(
                "hours_parse",
                "Date / holiday / weather parse",
                "done",
                (facts.get("parse_debug") or {}).get("matched_via") or "now",
            )
        )
        steps.append(
            _step(
                "hours_answer",
                "Deterministic hours answer",
                "done",
                "weather"
                if facts.get("weather_hint")
                else (
                    "holiday"
                    if facts.get("is_holiday")
                    else ("sunday" if facts.get("is_sunday") else "open_window")
                ),
            )
        )
        trace["opening_hours"] = {
            "effective_message": hours_msg,
            "is_general": general,
            "facts": _serialize_hours_facts(facts),
        }
        return _fact_response(
            route_name="deterministic_opening_hours",
            answer=answer,
            citations=[{"source": "institution/opening_hours", "title": "Opening Hours"}],
            provider="deterministic_opening_hours",
            debug={
                "hours": (decision.debug or {}).get("hours_debug"),
                "facts": trace["opening_hours"]["facts"],
            },
        )

    if route == "cancel_dialogue":
        result = cancel_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
        )
        answer = result["answer"]
        tc = dict(result.get("trace_cancel") or {})
        st = result.get("state") or {}
        if st.get("arbiter_pick") and "arbiter_pick" not in tc:
            tc["arbiter_pick"] = st["arbiter_pick"]
        pick = tc.get("arbiter_pick")
        extra_steps = [
            _step(
                "cancel_dialogue",
                "Cancel session dialogue",
                "done",
                tc.get("stage") or "turn",
            )
        ]
        if pick and pick.get("called"):
            steps.append(
                _step(
                    "cancel_pick",
                    "Cheap LLM cancel pick",
                    "done" if pick.get("action") == "pick" else "skipped",
                    f"{pick.get('action')} · {pick.get('reason') or 'n/a'}"
                    + (f" · {pick.get('model')}" if pick.get("model") else ""),
                )
            )
        return _fact_response(
            route_name="cancel_dialogue",
            answer=answer,
            citations=[
                {"source": "services/families", "title": "Family WhatsApp sheet"},
                {"source": "services/cancellations", "title": "Cancellations sheet"},
            ],
            provider="cancel_dialogue",
            extra_step=extra_steps[0],
            debug={"cancel": tc},
            adapt=False,
        )

    if route == "booking_dialogue":
        result = dialogue_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
        )
        tb = result.get("trace_booking") or {}
        if result.get("yield_to_rag"):
            steps.append(
                _step(
                    "booking_dialogue",
                    "Left booking chat",
                    "done",
                    tb.get("reason") or "yield_to_rag",
                )
            )
            trace["booking"] = tb
            route = "rag"
        else:
            tb = dict(tb)
            st = result.get("state") or {}
            if st.get("arbiter_booking") and "arbiter_booking" not in tb:
                tb["arbiter_booking"] = st["arbiter_booking"]
            hints = tb.get("arbiter_booking")
            if hints and hints.get("called"):
                if hints.get("ok"):
                    bp_status = "done"
                    bp_detail = f"ok · conf={hints.get('confidence')}"
                elif hints.get("reason") == "parse_fail":
                    bp_status = "error"
                    bp_detail = "parse_fail"
                else:
                    bp_status = "skipped"
                    bp_detail = f"no_fill · conf={hints.get('confidence')}"
                if hints.get("model"):
                    bp_detail = f"{bp_detail} · {hints['model']}"
                steps.append(
                    _step(
                        "booking_parse",
                        "Cheap LLM booking parse",
                        bp_status,
                        bp_detail,
                    )
                )
            return _fact_response(
                route_name="booking_dialogue",
                answer=result["answer"],
                citations=[
                    {"source": "services/families", "title": "Family WhatsApp sheet"},
                    {"source": "services/timetable", "title": "Centre class register"},
                ],
                provider="booking_dialogue",
                extra_step=_step(
                    "booking_dialogue",
                    "Conversational booking",
                    "done",
                    tb.get("stage") or "turn",
                ),
                slots=result.get("slots"),
                debug={"dialogue": tb},
                adapt=False,
            )

    if route == "booking_tool":
        course = _detect_course(message)
        teacher_id = _detect_teacher(message)
        slots = booking_svc.get_availability(
            course, lang=lang, teacher_id=teacher_id
        )
        answer = booking_svc.format_availability_message(slots, lang=lang)
        return _fact_response(
            route_name="booking_tool",
            answer=answer,
            citations=[{"source": "services/timetable", "title": "Centre class register"}],
            provider="booking_tool",
            extra_step=_step(
                "booking",
                "Timetable / availability tool",
                "done",
                f"course={course}, teacher={teacher_id or 'any'}, slots={len(slots)}",
            ),
            slots=slots[:8],
            debug={"course_key": course, "teacher_id": teacher_id},
        )

    if route == "greeting" or is_greeting(message):
        return _fact_response(
            route_name="greeting",
            answer=_GREETING_REPLY,
            citations=[{"source": "en/faq/greeting.md", "title": "Welcome!"}],
            provider="greeting",
            extra_step=_step("greeting", "Greeting short-circuit", "done", "welcome"),
            debug={"greeting": True},
        )

    # Deterministic: we do not teach scuba/piano/etc. — never dump unrelated FAQs
    unk = unknown_programme if route == "unknown_programme" else None
    if unk:
        todo_parent, todo_child = _family_names()
        label = unk.lower()
        # Parent-facing: no mention of internal admin sheets
        answer = (
            f"We don't offer **{label}** at BrightNest — sorry about the confusion.\n\n"
            f"Our programmes are {_OFFERED_PROGRAMMES}. "
            "If you tell me your child's age, I can suggest a better fit — "
            "or ask to speak with our team and someone will reach out during working hours."
        )
        admin_todo = admin_todos.add_todo(
            question=message if unknown_programme else f"{message} (re: {label})",
            reply=answer,
            info_needed=(
                f"Parent asked about {label}. Please confirm we do not offer it, "
                "and whether we can suggest a partner or referral."
            ),
            reason="unknown_programme",
            phone=phone,
            parent_name=todo_parent,
            child_name=todo_child,
            session_id=session_id,
            top_score=None,
            hit_count=0,
        )
        steps.append(
            _step("unknown_programme", "Unsupported programme", "done", label)
        )
        steps.append(
            _step("admin_todo", "Flag for admin to-do", "done", admin_todo.get("id"))
        )
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "unknown programme"))
        trace["route"] = "unknown_programme"
        trace["provider"] = "unknown_programme"
        trace["answer_provider"] = "unknown_programme"
        trace["admin_todo"] = admin_todo
        trace["llm"] = _llm_trace_summary(decision.debug or {}, steps, "unknown_programme")
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=answer,
            citations=[],
            provider="unknown_programme",
            session_id=session_id,
            debug={"unknown_programme": label, "admin_todo": admin_todo},
            trace=trace,
        )
        _persist_turn(session_id, message, answer, lang, use_history)
        return resp

    result = chat_with_rag(
        message,
        language=lang,
        debug=True,
        history_context=history_context if use_history else None,
    )
    answer = result.get("answer") or ""
    rag_debug = result.get("debug") or {}
    steps.append(
        _step(
            "rag",
            "RAG retrieve → generate",
            "done",
            f"{rag_debug.get('hit_count', 0)} hits · {result.get('provider')}",
        )
    )
    # Still surface hours parse when the query looked date-like but lost to RAG
    try:
        soft_facts = _get_opening_facts(message, lang=lang, is_general=False)
        dbg = soft_facts.get("parse_debug") or {}
        if not dbg.get("is_fallback_to_now") or dbg.get("holiday_keyword_detected"):
            trace["opening_hours"] = {
                "effective_message": message,
                "is_general": False,
                "facts": _serialize_hours_facts(soft_facts),
                "note": "Parsed for dashboard; RAG route was selected",
            }
            steps.append(_step("hours_parse", "Date parse (informational)", "done",
                               dbg.get("matched_via") or "fallback_now"))
    except Exception:
        pass

    admin_todo = None
    # Greetings / small-talk must never spam the admin to-do list
    if rag_debug.get("knowledge_gap") and is_greeting(message):
        rag_debug = {**rag_debug, "knowledge_gap": False, "gap_reason": None}
        steps.append(
            _step("admin_todo", "Flag for admin to-do", "skipped", "greeting")
        )
    # Off-topic chit-chat already answered with a refuse — don't spam the staff sheet
    if rag_debug.get("knowledge_gap") and (
        rag_debug.get("gap_reason") or ""
    ) == "off_topic_retrieval":
        rag_debug = {**rag_debug, "knowledge_gap": False}
        steps.append(
            _step("admin_todo", "Flag for admin to-do", "skipped", "off_topic")
        )
    if rag_debug.get("knowledge_gap"):
        info_needed = rag_debug.get("admin_gap_note") or admin_todos.infer_info_needed(
            message,
            result.get("citations") or [],
            rag_debug.get("gap_reason") or "knowledge_gap",
        )
        # Enrich identity from the family sheet when the request omitted names
        fam = {}
        try:
            fam = families.list_family_by_phone(phone) or {}
        except Exception:
            fam = {}
        todo_parent = parent_name or fam.get("parent_name") or ""
        todo_child = child_name
        if not todo_child:
            kids = fam.get("children") or []
            if len(kids) == 1:
                todo_child = kids[0].get("child_name") or ""
        # Demo bias: keep a useful model answer whenever we have one.
        # Only soft-refuse when retrieval was empty — never overwrite a fluent reply
        # just because lexical overlap looked weak.
        gap_reason = rag_debug.get("gap_reason") or ""
        low = answer.lower()
        if "admin to-do" in low or "admin team" in low or "to-do list" in low:
            answer = re.sub(
                r"(?is)\n*.{0,40}(admin to-?do( list)?|admin team|to-?do list).*$",
                "",
                answer,
            ).rstrip()
        if gap_reason == "no_retrieval" and (
            not answer.strip()
            or "happy to help. based on what we usually share" in low
        ):
            answer = (
                "I’m not finding that in our centre materials yet. "
                "If you’d like, ask to speak with our team and somebody will reach out "
                "during working hours."
            )
        # Demo: polish awkward gap copy (don't stack a long trailer onto a good answer)
        polished = demo_polish.polish_gap_reply(
            message, answer, gap_reason=gap_reason, lang=lang
        )
        if polished.get("polished"):
            gap_status = "done"
            answer = polished["answer"]
        elif polished.get("called") and polished.get("reason") in {"empty", "parse_fail"}:
            gap_status = "error"
        else:
            gap_status = "skipped"
        steps.append(
            _step(
                "gap_polish",
                "Cheap LLM gap polish",
                gap_status,
                f"{polished.get('reason') or 'skipped'}"
                + (f" · {polished.get('model')}" if polished.get("model") else ""),
            )
        )
        if not polished.get("polished"):
            if gap_reason and "reach out" not in answer.lower() and "working hours" not in answer.lower():
                if len(answer) < 280:
                    answer = (
                        answer.rstrip()
                        + "\n\nIf anything is still unclear, ask to speak with our team and "
                        "somebody will reach out during working hours."
                    )
        admin_todo = admin_todos.add_todo(
            question=message,
            reply=answer,
            info_needed=info_needed,
            reason=rag_debug.get("gap_reason") or "knowledge_gap",
            phone=phone,
            parent_name=todo_parent,
            child_name=todo_child,
            session_id=session_id,
            top_score=rag_debug.get("top_score"),
            hit_count=int(rag_debug.get("hit_count") or 0),
        )
        steps.append(
            _step(
                "admin_todo",
                "Flag for admin to-do",
                "done",
                f"{admin_todo.get('id')} · {rag_debug.get('gap_reason')}",
            )
        )
        trace["admin_todo"] = admin_todo

    rag_provider = result.get("provider") or "rag"
    if any(s.get("id") == "gap_polish" and s.get("status") == "done" for s in steps):
        rag_provider = f"{rag_provider}+llm"
    trace["route"] = "rag"
    trace["rag"] = rag_debug
    trace["provider"] = rag_provider
    trace["answer_provider"] = rag_provider
    trace["llm"] = _llm_trace_summary(decision.debug or {}, steps, rag_provider)
    trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    resp = ChatResponse(
        answer=answer,
        citations=result.get("citations") or [],
        provider=rag_provider,
        session_id=session_id,
        debug=rag_debug,
        trace=trace,
    )
    _persist_turn(session_id, message, answer, lang, use_history)
    return resp


def _persist_turn(
    session_id: str,
    user_message: str,
    bot_answer: str,
    lang: str,
    use_history: bool,
) -> None:
    if not use_history:
        return
    try:
        now = time.time()
        save_message(session_id, "user", user_message, lang, timestamp=now)
        save_message(session_id, "bot", bot_answer or "", lang, timestamp=now + 0.001)
        prune_history(session_id, keep=SETTINGS.chat_history_limit)
    except Exception as e:
        print(f"[CHAT] history save failed: {e}", flush=True)


@router.delete("/chat/history/{session_id}")
def reset_history(session_id: str):
    clear_history(session_id)
    dialogue_svc.clear_state(session_id)
    cancel_svc.clear_state(session_id)
    # Demo Reset also clears admin sheets so each run starts clean
    cleared = admin_todos.clear_todos()
    msgs_cleared = staff_messages.clear_messages()
    return {
        "ok": True,
        "session_id": session_id,
        "admin_todos_cleared": cleared,
        "staff_messages_cleared": msgs_cleared,
    }


@router.get("/admin/todos")
def get_admin_todos(status: str = "open", limit: int = 50):
    return {
        "todos": admin_todos.list_todos(status=status or None, limit=limit),
        "sheet": "admin_todos.csv",
    }


@router.post("/admin/todos/{todo_id}/done")
def complete_admin_todo(todo_id: str):
    row = admin_todos.complete_todo(todo_id)
    if not row:
        raise HTTPException(status_code=404, detail="To-do not found")
    return row


class AdminTodoPatch(BaseModel):
    info_needed: Optional[str] = None
    parent_name: Optional[str] = None
    child_name: Optional[str] = None


@router.patch("/admin/todos/{todo_id}")
def patch_admin_todo(todo_id: str, body: AdminTodoPatch):
    """Update editable cells on an open admin to-do (Staff desk sheet)."""
    payload = body.model_dump(exclude_unset=True)
    if not payload:
        raise HTTPException(status_code=400, detail="No fields to update")
    row = admin_todos.update_todo(todo_id, **payload)
    if not row:
        raise HTTPException(status_code=404, detail="Open to-do not found")
    return row


@router.get("/admin/messages")
def get_staff_messages(status: str = "open", limit: int = 50):
    """Inbox of direct bot → centre-boss notices (human hand-off)."""
    return {
        "messages": staff_messages.list_messages(status=status or None, limit=limit),
        "sheet": "staff_messages.csv",
    }


@router.post("/admin/messages/{message_id}/ack")
def ack_staff_message(message_id: str):
    row = staff_messages.acknowledge(message_id)
    if not row:
        raise HTTPException(status_code=404, detail="Staff message not found")
    return row


@router.get("/courses")
def courses(language: str = "en"):
    return {"courses": booking_svc.list_courses(language)}


@router.get("/availability")
def availability(
    course_key: str = "trial",
    language: str = "en",
    days_ahead: int = 14,
    teacher_id: Optional[str] = None,
):
    slots = booking_svc.get_availability(
        course_key, days_ahead=days_ahead, lang=language, teacher_id=teacher_id
    )
    return {"course_key": course_key, "teacher_id": teacher_id, "slots": slots}


@router.post("/bookings")
def create_booking(req: BookingRequest):
    try:
        booking = booking_svc.create_booking(
            course_key=req.course_key,
            slot_start=req.slot_start,
            parent_name=req.parent_name,
            child_name=req.child_name,
            phone=req.phone,
            notes=req.notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return booking


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: str):
    row = booking_svc.get_booking(booking_id)
    if not row:
        raise HTTPException(status_code=404, detail="Booking not found")
    return row


@router.get("/teachers")
def teachers():
    return {"teachers": booking_svc.list_teachers()}


@router.get("/teachers/{teacher_id}/availability")
def teacher_availability(teacher_id: str, language: str = "en", days_ahead: int = 14):
    try:
        return booking_svc.get_teacher_availability_map(
            teacher_id, days_ahead=days_ahead, lang=language
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/schedule")
def schedule(days: int = 7, language: str = "en"):
    """Admin register from local CSV sheets (calendar / spreadsheet stand-in)."""
    days = max(1, min(days, 14))
    return booking_svc.get_week_schedule(days=days, lang=language)


@router.post("/admin/sheets/reseed")
def reseed_sheets():
    """Reset CSV admin sheets to the synthetic demo pack (keeps format; wipes chat_bookings)."""
    from services import schedule_sheets as sheets

    return sheets.reseed(force=True)


@router.post("/admin/reindex")
def reindex():
    from rag.ingest import ingest

    return ingest(reset=True)
