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
    detect_opening_hours_intent,
    effective_hours_message,
    is_general_hours_query,
    is_hours_followup,
    recent_was_hours,
)
from llm.lang import get_language_code
from llm.opening_hours import _get_opening_facts, compute_opening_answer
from rag.pipeline import chat_with_rag, is_greeting
from services import admin_todos
from services import booking as booking_svc
from services import booking_dialogue as dialogue_svc
from services import cancel_dialogue as cancel_svc
from services import escalation as escalation_svc
from services import families
from services import staff_messages

router = APIRouter()

_CONFUSED_RE = re.compile(
    r"^(what|huh|sorry|come again|eh|嗯|吓|咩話|什么)\??[!!.。]*$",
    re.I,
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

    # Opening-hours path (incl. short follow-ups like "tomorrow?")
    hours_msg = message
    hours_intent, hours_debug = detect_opening_hours_intent(message, lang)
    followup_promoted = False
    if (
        SETTINGS.opening_hours_enabled
        and not hours_intent
        and is_hours_followup(message)
        and recent_was_hours(history)
    ):
        hours_intent = True
        followup_promoted = True
        hours_debug = {**hours_debug, "followup_promoted": True}
        hours_msg = effective_hours_message(message, history)

    avail_hit = bool(_AVAIL_RE.search(message))
    dialogue_active = dialogue_svc.is_active(session_id)
    cancel_active = cancel_svc.is_active(session_id)
    cancel_hit = (not hours_intent) and (
        cancel_active or cancel_svc.is_cancel_request(message)
    )
    # Leave sticky booking when the parent asks fees / unknown programmes / etc.
    leave_booking = dialogue_svc.should_yield_to_rag(session_id, message)
    force_rag = leave_booking
    if leave_booking and dialogue_active:
        dialogue_svc.clear_state(session_id)
        dialogue_active = False
    unknown_programme = dialogue_svc.unknown_programme(message)
    if unknown_programme:
        force_rag = True
    dialogue_hit = (
        (not hours_intent)
        and (not cancel_hit)
        and (not leave_booking)
        and (dialogue_active or dialogue_svc.is_booking_request(message))
    )
    # Pure availability browse (no child booking chat) stays on the simple tool
    simple_avail = (
        avail_hit
        and not dialogue_hit
        and not cancel_hit
        and not leave_booking
        and not dialogue_svc.is_booking_request(message)
    )

    esc_preview = escalation_svc.detect_escalation(message)
    route_guess = (
        "opening_hours"
        if hours_intent
        else (
            "cancel_dialogue"
            if cancel_hit
            else (
                "greeting"
                if is_greeting(message)
                else (
                    "staff_escalation"
                    if esc_preview
                    else (
                        "unknown_programme"
                        if unknown_programme
                        else (
                            "booking_dialogue"
                            if dialogue_hit
                            else ("booking_tool" if avail_hit and not force_rag else "rag")
                        )
                    )
                )
            )
        )
    )
    steps.append(_step("intent", "Intent router", "done", route_guess))

    phone = (req.phone or "").strip() or "+852 9000 0000"
    parent_name = (req.parent_name or "").strip()
    child_name = (req.child_name or "").strip()

    trace: Dict[str, Any] = {
        "language": lang,
        "session_id": session_id,
        "history": {
            "enabled": use_history,
            # Prior messages loaded for this turn (excludes the message just typed).
            "turns_loaded": len(history),
            "limit": SETTINGS.chat_history_limit,
            # Preview must match turns_loaded — do not append the new user turn here
            # (history_context includes it for the LLM prompt, which confused the inspector).
            "preview": (
                build_context_string(history, include_new=False)[-600:] if history else ""
            ),
        },
        "routing": {
            "hours_intent": hours_intent,
            "hours_followup_promoted": followup_promoted,
            "hours_debug": hours_debug,
            "availability_intent": avail_hit and not hours_intent,
            "booking_dialogue": dialogue_hit,
            "dialogue_active": dialogue_active,
            "cancel_dialogue": cancel_hit,
            "cancel_active": cancel_active,
        },
        "pipeline_steps": steps,
    }

    if SETTINGS.opening_hours_enabled and hours_intent:
        general = is_general_hours_query(hours_msg, lang)
        facts = _get_opening_facts(hours_msg, lang=lang, is_general=general)
        answer = compute_opening_answer(hours_msg, lang=lang, is_general=general)
        steps.append(_step("hours_parse", "Date / holiday / weather parse", "done",
                           (facts.get("parse_debug") or {}).get("matched_via") or "now"))
        steps.append(_step("hours_answer", "Deterministic hours answer", "done",
                           "weather" if facts.get("weather_hint")
                           else ("holiday" if facts.get("is_holiday")
                                 else ("sunday" if facts.get("is_sunday") else "open_window"))))
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "hours short-circuit"))
        trace["route"] = "deterministic_opening_hours"
        trace["opening_hours"] = {
            "effective_message": hours_msg,
            "is_general": general,
            "facts": _serialize_hours_facts(facts),
        }
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=answer,
            citations=[{"source": "institution/opening_hours", "title": "Opening Hours"}],
            provider="deterministic_opening_hours",
            session_id=session_id,
            debug={"hours": hours_debug, "facts": trace["opening_hours"]["facts"]},
            trace=trace,
        )
        _persist_turn(session_id, message, answer, lang, use_history)
        return resp

    if cancel_hit:
        result = cancel_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
        )
        answer = result["answer"]
        tc = result.get("trace_cancel") or {}
        steps.append(
            _step("cancel_dialogue", "Cancel session dialogue", "done", tc.get("stage") or "turn")
        )
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "cancel dialogue"))
        trace["route"] = "cancel_dialogue"
        trace["cancel"] = tc
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=answer,
            citations=[
                {"source": "services/families", "title": "Family WhatsApp sheet"},
                {"source": "services/cancellations", "title": "Cancellations sheet"},
            ],
            provider="cancel_dialogue",
            session_id=session_id,
            debug={"cancel": tc},
            trace=trace,
        )
        _persist_turn(session_id, message, answer, lang, use_history)
        return resp

    if leave_booking:
        steps.append(
            _step(
                "booking_dialogue",
                "Left booking chat",
                "done",
                f"yield → rag"
                + (f" · {unknown_programme}" if unknown_programme else ""),
            )
        )
        trace["booking"] = {
            "stage": "yield_to_rag",
            "unknown_programme": unknown_programme,
            "reason": "left_sticky_booking",
        }

    if dialogue_hit:
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
            force_rag = True
            steps.append(
                _step(
                    "booking_dialogue",
                    "Left booking chat",
                    "done",
                    tb.get("reason") or "yield_to_rag",
                )
            )
            trace["booking"] = tb
            # Fall through to RAG (do not re-enter booking_tool)
        else:
            answer = result["answer"]
            slots = result.get("slots")
            steps.append(
                _step(
                    "booking_dialogue",
                    "Conversational booking",
                    "done",
                    tb.get("stage") or "turn",
                )
            )
            steps.append(
                _step("rag", "RAG retrieve → generate", "skipped", "booking dialogue")
            )
            trace["route"] = "booking_dialogue"
            trace["booking"] = tb
            trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            resp = ChatResponse(
                answer=answer,
                citations=[
                    {"source": "services/families", "title": "Family WhatsApp sheet"},
                    {"source": "services/timetable", "title": "Centre class register"},
                ],
                provider="booking_dialogue",
                slots=slots,
                session_id=session_id,
                debug={"dialogue": tb},
                trace=trace,
            )
            _persist_turn(session_id, message, answer, lang, use_history)
            return resp

    # Unknown programmes (scuba, …) must not hit the generic timetable tool either
    if (simple_avail or avail_hit) and not force_rag:
        course = _detect_course(message)
        teacher_id = _detect_teacher(message)
        slots = booking_svc.get_availability(
            course, lang=lang, teacher_id=teacher_id
        )
        answer = booking_svc.format_availability_message(slots, lang=lang)
        steps.append(
            _step(
                "booking",
                "Timetable / availability tool",
                "done",
                f"course={course}, teacher={teacher_id or 'any'}, slots={len(slots)}",
            )
        )
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "booking short-circuit"))
        trace["route"] = "booking_tool"
        trace["booking"] = {
            "course_key": course,
            "teacher_id": teacher_id,
            "slot_count": len(slots),
        }
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=answer,
            citations=[{"source": "services/timetable", "title": "Centre class register"}],
            provider="booking_tool",
            slots=slots[:8],
            session_id=session_id,
            debug={"course_key": course, "teacher_id": teacher_id},
            trace=trace,
        )
        _persist_turn(session_id, message, answer, lang, use_history)
        return resp

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

    # Deterministic greeting — never RAG-dump or admin-flag "Hello"/"Hi"
    if is_greeting(message):
        steps.append(_step("greeting", "Greeting short-circuit", "done", "welcome"))
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "greeting"))
        steps.append(_step("admin_todo", "Flag for admin to-do", "skipped", "greeting"))
        trace["route"] = "greeting"
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=_GREETING_REPLY,
            citations=[{"source": "en/faq/greeting.md", "title": "Welcome!"}],
            provider="greeting",
            session_id=session_id,
            debug={"greeting": True},
            trace=trace,
        )
        _persist_turn(session_id, message, _GREETING_REPLY, lang, use_history)
        return resp

    # Human hand-off → message the centre boss (never mention internal sheets to parents)
    esc = escalation_svc.detect_escalation(message)
    if esc:
        todo_parent, todo_child = _family_names()
        topic = escalation_svc.topic_hint_from_message(message, esc["category"])
        answer = escalation_svc.parent_reply(
            category=esc["category"], topic_hint=topic
        )
        staff_msg = staff_messages.add_message(
            category=esc["category"],
            priority=esc.get("priority") or "normal",
            parent_message=message,
            summary_for_boss=esc.get("summary") or topic,
            bot_reply=answer,
            phone=phone,
            parent_name=todo_parent,
            child_name=todo_child,
            session_id=session_id,
        )
        if dialogue_active:
            dialogue_svc.clear_state(session_id)
        if cancel_active:
            cancel_svc.clear_state(session_id)
        steps.append(
            _step(
                "staff_escalate",
                "Message centre boss",
                "done",
                f"{esc['category']} · {staff_msg.get('id')}",
            )
        )
        steps.append(_step("rag", "RAG retrieve → generate", "skipped", "staff escalation"))
        steps.append(_step("admin_todo", "Flag for admin to-do", "skipped", "staff message instead"))
        trace["route"] = "staff_escalation"
        trace["staff_message"] = staff_msg
        trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
        resp = ChatResponse(
            answer=answer,
            citations=[],
            provider="staff_escalation",
            session_id=session_id,
            debug={"escalation": esc, "staff_message": staff_msg},
            trace=trace,
        )
        _persist_turn(session_id, message, answer, lang, use_history)
        return resp

    # Deterministic: we do not teach scuba/piano/etc. — never dump unrelated FAQs
    unk = unknown_programme
    if not unk and _CONFUSED_RE.match((message or "").strip()) and history:
        for turn in reversed(history):
            if turn.get("role") == "user":
                unk = dialogue_svc.unknown_programme(turn.get("message") or "")
                if unk:
                    break
                break
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
                f"Parent asked about {label} — confirm we do not offer it, "
                "or whether a partner / referral applies. Quiet flag only "
                "(parent was not told about the to-do sheet)."
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
        trace["admin_todo"] = admin_todo
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
        # Don't leave parents with an off-topic FAQ dump.
        # Never mention internal to-do sheets — invite a human hand-off instead.
        gap_reason = rag_debug.get("gap_reason") or ""
        if gap_reason in {
            "off_topic_retrieval",
            "no_retrieval",
            "weak_extractive",
            "generic_fallback",
        }:
            answer = (
                "I don't have a solid answer for that in our centre materials yet. "
                "If you'd like, ask to speak with our team and somebody will reach out "
                "during working hours."
            )
        else:
            low = answer.lower()
            if "admin to-do" in low or "admin team" in low or "to-do list" in low:
                # Strip accidental internal wording from model output
                answer = re.sub(
                    r"(?is)\n*.{0,40}(admin to-?do( list)?|admin team|to-?do list).*$",
                    "",
                    answer,
                ).rstrip()
            if "reach out" not in answer.lower() and "working hours" not in answer.lower():
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

    trace["route"] = "rag"
    trace["rag"] = rag_debug
    trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    resp = ChatResponse(
        answer=answer,
        citations=result.get("citations") or [],
        provider=result.get("provider"),
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
