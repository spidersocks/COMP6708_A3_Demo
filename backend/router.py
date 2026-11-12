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
from rag.generator import adapt_structured_fact, compose_grounded_fact
from rag.pipeline import chat_with_rag, is_greeting, retrieve_for_enrich
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
from services import llm_io
from services import staff_messages

# Extra canned providers that get a main-LLM fact rewrite (centre_facts always does).
# Keep this short — soft refuses / greetings stay template-clean for the demo.
_ADAPT_PROVIDERS = {
    "deterministic_opening_hours",
    "booking_tool",
}

# Live tools may retrieve KB for enrich; static FAQs are pure RAG (no fact cards).
_GROUNDING_ENRICH_ROUTES = {
    "severe_weather",
    "deterministic_opening_hours",
    "opening_hours",
    "booking_tool",
}

router = APIRouter()

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
    dbg = facts.get("parse_debug") or {}
    is_holiday = bool(facts.get("is_holiday"))
    holiday_name = facts.get("holiday_name")
    weather_hint = facts.get("weather_hint")
    weather_applied = bool(dbg.get("weather_applied"))
    same_day_only = bool(dbg.get("weather_same_day_only"))
    # Explicit inspector rows: what we checked and what we found
    checks: List[Dict[str, Any]] = [
        {
            "id": "public_holiday",
            "label": "Public holiday check (HK calendar)",
            "checked": True,
            "passed": is_holiday,
            "detail": (
                f"Closed — {holiday_name}"
                if is_holiday
                else "Not a Hong Kong public holiday on the resolved date"
            ),
        },
        {
            "id": "sunday",
            "label": "Sunday closed check",
            "checked": True,
            "passed": bool(facts.get("is_sunday")),
            "detail": (
                "Sunday — centre closed"
                if facts.get("is_sunday")
                else "Not Sunday (weekday/Saturday window applies if open)"
            ),
        },
        {
            "id": "hko_weather",
            "label": "HKO severe-weather check (live)",
            "checked": weather_applied or same_day_only,
            "passed": bool(weather_hint),
            "detail": (
                f"Live signal applied: {weather_hint}"
                if weather_hint
                else (
                    "Checked for today — no Black Rain / T8+ / Pre-T8 closure active"
                    if weather_applied
                    else (
                        "Skipped — resolved date is not today "
                        "(live HKO is same-day only; future dates do not inherit tonight’s signal)"
                        if same_day_only
                        else "Weather check disabled or not run"
                    )
                ),
            ),
        },
    ]
    return {
        "datetime": dt.isoformat() if hasattr(dt, "isoformat") else str(dt),
        "lang": facts.get("lang"),
        "open_time": open_t.strftime("%H:%M") if open_t else None,
        "close_time": close_t.strftime("%H:%M") if close_t else None,
        "is_sunday": bool(facts.get("is_sunday")),
        "is_holiday": is_holiday,
        "holiday_name": holiday_name,
        "weather_hint": weather_hint,
        "asked_specific_time": bool(facts.get("asked_specific_time")),
        "is_general_query": bool(facts.get("is_general_query")),
        "parse_debug": dbg,
        "checks": checks,
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
        elif sid in {"booking_parse", "booking_dst"}:
            uses.append("booking_dst")
        elif sid == "cancel_dst":
            uses.append("cancel_dst")
        elif sid == "adapt":
            uses.append("fact_rewrite")
    cheap_called = bool(arb.get("called")) or any(
        s.get("id")
        in {"gap_polish", "cancel_pick", "booking_parse", "booking_dst", "cancel_dst"}
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
    llm_io.begin()
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
    # Prior turns only — query rewrite must never see the current inbound as "history"
    prior_context = (
        build_context_string(history, include_new=False) if use_history else ""
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
    policy = (decision.debug or {}).get("action_policy") or {}
    if policy:
        override = (decision.debug or {}).get("action_policy_override")
        if not policy.get("called"):
            pol_status = "skipped"
        elif str(policy.get("reason") or "").startswith("error"):
            pol_status = "error"
        else:
            pol_status = "done"
        if override:
            pol_detail = (
                f"{override.get('from')} → {override.get('to')} · "
                f"{policy.get('act')} {policy.get('confidence')}"
            )
        else:
            pol_detail = (
                f"{policy.get('act') or 'answer'} · {policy.get('reason') or 'n/a'}"
            )
        steps.append(
            _step(
                "action_policy",
                "Action policy · tool or answer",
                pol_status,
                pol_detail,
            )
        )

    trace: Dict[str, Any] = {
        "language": lang,
        "session_id": session_id,
        "history": {
            "enabled": use_history,
            "turns_loaded": len(history),
            "limit": SETTINGS.chat_history_limit,
            # Prior turns only (before this inbound message) — session-memory inspector
            "prior_preview": (
                build_context_string(history, include_new=False) if use_history else ""
            ),
            # Exact conversation context string passed into the LLM prompt (includes inbound)
            "preview": history_context if use_history else "",
            # Full prior turns for the expandable session-memory inspector
            "turns": [
                {
                    "role": t.get("role") or t.get("speaker") or "unknown",
                    "content": t.get("content") or t.get("message") or "",
                }
                for t in history
            ],
        },
        "inbound_message": message,
        "routing": {
            "route": route,
            "scores": decision.scores,
            "debug": decision.debug,
            "rules_evaluated": (decision.debug or {}).get("rules_evaluated") or [],
            "decision_path": (decision.debug or {}).get("decision_path") or [],
            "winning_rule": (decision.debug or {}).get("winning_rule") or {},
            "match_summary": (decision.debug or {}).get("match_summary") or {},
            "score_ledger": (decision.debug or {}).get("score_ledger") or [],
            "score_origins": (decision.debug or {}).get("score_origins") or {},
            "score_source_guide": (decision.debug or {}).get("score_source_guide") or [],
            "tools": (decision.debug or {}).get("tools") or {},
            "hours_followup_promoted": followup_promoted,
            "dialogue_active": dialogue_active,
            "cancel_active": cancel_active,
            "left_booking": leave_booking and not dialogue_active,
        },
        "tools": (decision.debug or {}).get("tools") or {},
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
        enrich: Optional[bool] = None,
        live_doc: str = "",
        ask_tool_ok: bool = False,
        tool_label: str = "availability / booking tool",
    ) -> ChatResponse:
        if extra_step:
            steps.append(extra_step)
        final = answer
        final_provider = provider
        out_citations = list(citations or [])
        out_slots = slots
        do_enrich = (
            SETTINGS.grounding_enrich_enabled
            and SETTINGS.has_dashscope
            and (
                enrich
                if enrich is not None
                else route_name in _GROUNDING_ENRICH_ROUTES
            )
        )

        if do_enrich:
            steps.append(
                _step(
                    "rag_enrich",
                    "KB retrieve for grounded compose",
                    "running",
                    f"top_k={SETTINGS.grounding_enrich_top_k}",
                )
            )
            enrich_pack = retrieve_for_enrich(
                message,
                language=lang,
                top_k=SETTINGS.grounding_enrich_top_k,
                history_context=history_context if use_history else None,
                prior_context=prior_context if use_history else None,
            )
            rag_hits = enrich_pack.get("hits") or []
            rag_cites = enrich_pack.get("citations") or []
            rag_dbg = enrich_pack.get("debug") or {}
            if live_doc:
                steps.append(
                    _step(
                        "live_doc",
                        "Attach live privileged doc",
                        "done",
                        f"chars={len(live_doc)}",
                    )
                )
            composed = compose_grounded_fact(
                message,
                answer,
                rag_hits,
                lang=lang,
                history_context=history_context,
                route=route_name,
                live_doc=live_doc,
                ask_tool_ok=ask_tool_ok,
                tool_label=tool_label,
            )
            if composed.get("composed") or composed.get("adapted"):
                final = composed["answer"]
                final_provider = (
                    f"{provider}+grounded_rag"
                    if composed.get("composed")
                    else f"{provider}+llm"
                )
            if ask_tool_ok:
                invoke = composed.get("invoke_tool")
                if invoke is False:
                    out_slots = None
                    steps.append(
                        _step(
                            "tool_gate",
                            f"LLM tool gate · {tool_label}",
                            "skipped",
                            "[[TOOL_SKIP]] — live cards not exposed",
                        )
                    )
                else:
                    steps.append(
                        _step(
                            "tool_gate",
                            f"LLM tool gate · {tool_label}",
                            "done",
                            "[[TOOL_OK]] — expose live tool output",
                        )
                    )
            # Merge citations: structured first, then enrich hits (tagged)
            for c in rag_cites:
                tagged = dict(c)
                tagged["role"] = "enrichment"
                out_citations.append(tagged)
            for i, s in enumerate(steps):
                if s.get("id") == "rag_enrich" and s.get("status") == "running":
                    steps[i] = _step(
                        "rag_enrich",
                        "KB retrieve for grounded compose",
                        "done" if rag_hits else "empty",
                        f"hits={len(rag_hits)} · top={rag_dbg.get('top_score')}",
                    )
                    break
            steps.append(
                _step(
                    "grounded_compose",
                    "Compose live/structured docs + KB (docs win)",
                    "done" if composed.get("composed") else "fallback",
                    (
                        f"{SETTINGS.llm_model} · "
                        f"{composed.get('provider') or 'n/a'} · "
                        f"composed={bool(composed.get('composed'))} · "
                        f"live_doc={bool(live_doc)}"
                    ),
                )
            )
            steps.append(
                _step(
                    "rag",
                    "RAG retrieve → generate",
                    "enrich_only",
                    "full RAG generate skipped — live/structured docs remain source of truth",
                )
            )
            trace["rag"] = {
                **rag_dbg,
                "mode": "grounded_enrich",
                "privileged_route": route_name,
            }
            why = (
                "Live/structured docs are privileged; KB may add general advice "
                "but must not override signals, fees, times, or seat counts."
                if composed.get("composed")
                else (
                    "Enrich retrieve ran but compose fell back to tone-only adapt "
                    f"({composed.get('compose_fallback') or composed.get('provider')})."
                )
            )
            if ask_tool_ok:
                why += (
                    " Tool gate: "
                    + (
                        "LLM approved exposing interactive seat/slot cards."
                        if composed.get("invoke_tool") is not False
                        else "LLM skipped exposing the booking tool this turn."
                    )
                )
            trace["grounding"] = {
                "mode": "grounded_compose" if composed.get("composed") else "adapt_fallback",
                "route": route_name,
                "structured_privileged": True,
                "live_doc": bool(live_doc),
                "live_doc_chars": len(live_doc or ""),
                "rag_hit_count": len(rag_hits),
                "composed": bool(composed.get("composed")),
                "used_rag": bool(composed.get("used_rag")),
                "ask_tool_ok": ask_tool_ok,
                "invoke_tool": composed.get("invoke_tool"),
                "model": SETTINGS.llm_model,
                "why": why,
            }
            trace["adapt"] = {
                "ran": bool(composed.get("adapted")),
                "why": why,
                "model": SETTINGS.llm_model,
                "provider": composed.get("provider") or "passthrough",
                "input_provider": provider,
                "mode": "grounded_compose",
            }
        else:
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
                            (
                                f"{SETTINGS.llm_model} · "
                                f"{polished.get('provider') or 'dashscope'} · "
                                f"why=structured_{provider}_fluency"
                            ),
                        )
                    )
                    trace["adapt"] = {
                        "ran": True,
                        "why": (
                            "Structured tool answer is correct but terse; "
                            "main LLM rewrites for parent-facing fluency "
                            "without changing facts."
                        ),
                        "model": SETTINGS.llm_model,
                        "provider": polished.get("provider") or "dashscope",
                        "input_provider": provider,
                        "mode": "adapt_only",
                    }
                else:
                    steps.append(
                        _step(
                            "adapt",
                            "Main LLM fact rewrite",
                            "skipped",
                            polished.get("provider") or "passthrough",
                        )
                    )
                    trace["adapt"] = {
                        "ran": False,
                        "why": polished.get("provider") or "passthrough",
                        "input_provider": provider,
                        "mode": "adapt_only",
                    }
            steps.append(_step("rag", "RAG retrieve → generate", "skipped", route_name))
            trace["grounding"] = {
                "mode": "bypass",
                "route": route_name,
                "structured_privileged": True,
                "rag_hit_count": 0,
                "composed": False,
                "used_rag": False,
                "why": (
                    "Hard / tool route — RAG retrieve skipped so live numbers or "
                    "safety hand-off cannot be diluted by KB narrative."
                ),
            }

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
        if dbg.get("escalation") and "escalation" not in trace:
            trace["escalation"] = dbg["escalation"]
        if dbg.get("final_check") and "final_check" not in trace:
            trace["final_check"] = dbg["final_check"]
        if dbg.get("staff_message") and "staff_message" not in trace:
            trace["staff_message"] = dbg["staff_message"]
        llm_io.attach(trace)
        resp = ChatResponse(
            answer=final,
            citations=out_citations,
            provider=final_provider,
            slots=out_slots,
            session_id=session_id,
            debug=debug or {},
            trace=trace,
        )
        _persist_turn(session_id, message, final, lang, use_history)
        return resp

    # --- Deterministic / tool routes (scored intent) ---
    if route == "staff_escalation":
        from services import final_check, staff_email

        esc = decision.debug.get("escalation") or escalation_svc.detect_escalation(message)
        # Never 500 if route is set without a hard detector payload
        if not isinstance(esc, dict) or not esc.get("category"):
            esc = {
                "category": "ask_human",
                "priority": "high",
                "summary": "Follow up: parent asked to speak with a staff member",
            }
        todo_parent, todo_child = _family_names()
        topic = escalation_svc.topic_hint_from_message(message, esc["category"])
        is_safety = (esc.get("category") or "") == "safety"
        gate = final_check.review_commit(
            kind="escalate",
            message=message,
            proposed_summary=(
                f"Open staff inbox ({esc.get('category')}/{esc.get('priority')}) "
                f"for {todo_parent or 'parent'} / {todo_child or 'child'}: "
                f"{esc.get('summary') or topic}"
            ),
            context={
                "category": esc.get("category"),
                "priority": esc.get("priority"),
                "topic": topic,
                "force_proceed": is_safety,
            },
        )
        steps.append(
            _step(
                "final_check",
                "LLM final check · escalate",
                "done" if gate.get("called") else "skipped",
                f"{gate.get('action')} · {gate.get('reason') or 'n/a'}"
                + (f" · {gate.get('model')}" if gate.get("model") else ""),
            )
        )
        if gate.get("action") == "clarify" and not is_safety:
            clarify = gate.get("clarify_prompt") or (
                "I can put you through to our team — is that what you'd like, "
                "or did you want a quick answer about programmes, fees, or hours first?"
            )
            return _fact_response(
                route_name="staff_escalation",
                answer=clarify,
                citations=[],
                provider="staff_escalation",
                extra_step=_step(
                    "staff_escalate",
                    "Message centre boss",
                    "skipped",
                    "final_check=clarify",
                ),
                debug={"escalation": esc, "final_check": gate},
                adapt=False,
            )
        if gate.get("action") == "abort" and not is_safety:
            return _fact_response(
                route_name="staff_escalation",
                answer=(
                    "Happy to help with BrightNest programmes, fees, hours, or booking. "
                    "What would you like to know?"
                ),
                citations=[],
                provider="staff_escalation",
                extra_step=_step(
                    "staff_escalate",
                    "Message centre boss",
                    "skipped",
                    "final_check=abort",
                ),
                debug={"escalation": esc, "final_check": gate},
                adapt=False,
            )
        answer = escalation_svc.parent_reply(category=esc["category"], lang=lang)
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
        # Inspector reads staff_message / escalation / final_check from trace
        trace["staff_message"] = staff_msg
        trace["final_check"] = gate
        trace["escalation"] = esc
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
            debug={"escalation": esc, "staff_message": staff_msg, "final_check": gate},
            adapt=False,
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
        live_weather = ""
        try:
            from llm.hko import get_weather_hint_for_opening

            hint = get_weather_hint_for_opening(lang)
            if hint:
                live_weather = (
                    f"Current HKO / centre weather status (live):\n{hint}\n\n"
                    "Treat this live status as authoritative for *today*; "
                    "the structured policy below covers signal rules in general."
                )
        except Exception:
            live_weather = ""
        # Inspector: still surface today's holiday + HKO checks alongside the policy route
        try:
            today_facts = _get_opening_facts("today", lang=lang, is_general=False)
            trace["opening_hours"] = {
                "effective_message": message,
                "is_general": False,
                "facts": _serialize_hours_facts(today_facts),
                "context": "severe_weather_also_checks_today_calendar",
            }
        except Exception:
            pass
        return _fact_response(
            route_name="severe_weather",
            answer=answer,
            citations=cites,
            provider="centre_facts",
            extra_step=_step("facts", "Severe weather policy", "done", "structured"),
            live_doc=live_weather,
        )

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
        live_hours = ""
        wh = facts.get("weather_hint")
        if wh:
            live_hours = (
                f"Live HKO severe-weather note for the resolved date:\n{wh}\n\n"
                "Combine with the opening-hours card; do not invent a different signal."
            )
        return _fact_response(
            route_name="deterministic_opening_hours",
            answer=answer,
            citations=[{"source": "centre/opening_hours", "title": "Opening Hours"}],
            provider="deterministic_opening_hours",
            debug={
                "hours": (decision.debug or {}).get("hours_debug"),
                "facts": trace["opening_hours"]["facts"],
            },
            live_doc=live_hours,
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
        if st.get("cancel_dst") and "cancel_dst" not in tc:
            tc["cancel_dst"] = st["cancel_dst"]
        pick = tc.get("arbiter_pick")
        cdst = tc.get("cancel_dst")
        gate = tc.get("final_check") or {}
        if gate:
            steps.append(
                _step(
                    "final_check",
                    "LLM final check · cancel",
                    "done" if gate.get("called") else "skipped",
                    f"{gate.get('action')} · {gate.get('reason') or 'n/a'}"
                    + (f" · {gate.get('model')}" if gate.get("model") else ""),
                )
            )
        if cdst and cdst.get("called"):
            filled = cdst.get("filled") or {}
            filled_keys = ",".join(sorted(filled.keys())) or "—"
            if cdst.get("ok"):
                cd_status = "done"
                cd_detail = f"filled [{filled_keys}] · conf={cdst.get('confidence')}"
            elif cdst.get("reason") == "parse_fail":
                cd_status = "error"
                cd_detail = "parse_fail"
            else:
                cd_status = "skipped"
                cd_detail = f"{cdst.get('reason') or 'no_fill'} · conf={cdst.get('confidence')}"
            if cdst.get("model"):
                cd_detail = f"{cd_detail} · {cdst['model']}"
            steps.append(
                _step(
                    "cancel_dst",
                    "Cancel DST · cheap LLM slots",
                    cd_status,
                    cd_detail,
                )
            )
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
            extra_step=_step(
                "cancel_dialogue",
                "Cancel session dialogue",
                "done",
                tc.get("stage") or "turn",
            ),
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
            history_preview=prior_context or "",
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
            if st.get("booking_dst") and "booking_dst" not in tb:
                tb["booking_dst"] = st["booking_dst"]
            hints = tb.get("booking_dst") or tb.get("arbiter_booking")
            gate = tb.get("final_check") or {}
            if gate:
                steps.append(
                    _step(
                        "final_check",
                        "LLM final check · book",
                        "done" if gate.get("called") else "skipped",
                        f"{gate.get('action')} · {gate.get('reason') or 'n/a'}"
                        + (f" · {gate.get('model')}" if gate.get("model") else ""),
                    )
                )
            if hints and hints.get("called"):
                filled = hints.get("filled") or {}
                filled_keys = ",".join(sorted(filled.keys())) or "—"
                if hints.get("ok"):
                    bp_status = "done"
                    bp_detail = f"filled [{filled_keys}] · conf={hints.get('confidence')}"
                elif hints.get("reason") == "parse_fail":
                    bp_status = "error"
                    bp_detail = "parse_fail"
                else:
                    bp_status = "skipped"
                    bp_detail = (
                        f"{hints.get('reason') or 'no_fill'} · conf={hints.get('confidence')}"
                    )
                if hints.get("model"):
                    bp_detail = f"{bp_detail} · {hints['model']}"
                steps.append(
                    _step(
                        "booking_dst",
                        "Booking DST · cheap LLM slots",
                        bp_status,
                        bp_detail,
                    )
                )
            follow = result.get("admin_followup")
            admin_todo = None
            if isinstance(follow, dict) and follow.get("reason"):
                todo_parent, todo_child = _family_names()
                admin_todo = admin_todos.add_todo(
                    question=follow.get("question") or message,
                    reply=follow.get("reply") or result["answer"],
                    info_needed=follow.get("info_needed") or "",
                    reason=str(follow.get("reason") or "assessment_request"),
                    phone=follow.get("phone") or phone,
                    parent_name=follow.get("parent_name") or todo_parent,
                    child_name=follow.get("child_name") or todo_child,
                    session_id=session_id,
                )
                steps.append(
                    _step(
                        "admin_todo",
                        "Flag for admin to-do",
                        "done",
                        f"{admin_todo.get('id')} · {follow.get('reason')}",
                    )
                )
                trace["admin_todo"] = admin_todo
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
                debug={
                    "dialogue": tb,
                    **({"admin_todo": admin_todo} if admin_todo else {}),
                },
                adapt=False,
            )

    if route == "booking_tool":
        course = _detect_course(message)
        teacher_id = _detect_teacher(message)
        slots = booking_svc.get_availability(
            course, lang=lang, teacher_id=teacher_id
        )
        answer = booking_svc.format_availability_message(slots, lang=lang)
        # Live register is privileged; LLM may elaborate from KB, then gate whether
        # interactive seat cards (the "tool") are exposed to the parent.
        live_register = (
            f"LIVE CLASS REGISTER (seats_left from CSV + chat bookings):\n"
            f"course={course} · teacher={teacher_id or 'any'} · "
            f"open_slots={len(slots)}\n\n"
            f"{answer}"
        )
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
            debug={"course_key": course, "teacher_id": teacher_id, "slot_count": len(slots)},
            live_doc=live_register,
            ask_tool_ok=True,
            tool_label="live seat / trial-slot cards",
        )

    def _answer_promises_outreach(text: str) -> bool:
        low = (text or "").lower()
        return any(
            p in low
            for p in (
                "reach out",
                "will arrange",
                "we'll arrange",
                "we will arrange",
                "get back to you",
                "team will",
                "somebody will",
                "our team will",
                "staff will",
            )
        )

    def _history_offered_assessment() -> bool:
        blob = " ".join(
            str(t.get("message") or t.get("content") or "")
            for t in (history or [])[-6:]
        ).lower()
        return any(
            k in blob
            for k in (
                "diagnostic assessment",
                "arrange that assessment",
                "arrange an assessment",
                "trial assessment",
                "would you like us to arrange",
            )
        )

    # Affirmative after bot offered assessment → book a live trial seat (not SheetPro-only)
    if dialogue_svc.is_affirmative(message) and _history_offered_assessment():
        todo_parent, todo_child = _family_names()
        st = dialogue_svc.get_state(session_id) or {
            "stage": "need_when_asked",
            "phone": phone or "",
            "parent_name": "",
            "child_name": "",
            "course_key": "trial",
            "hide_register": True,
            "when": {},
            "child_schedule": [],
        }
        st["course_key"] = "trial"
        st["hide_register"] = True
        st["parent_name"] = todo_parent or parent_name or st.get("parent_name") or ""
        st["child_name"] = todo_child or child_name or st.get("child_name") or ""
        if phone:
            st["phone"] = phone
        if st.get("stage") in {None, "start", "confirm_assessment", "done", ""}:
            st["stage"] = "need_when_asked"
        dialogue_svc.save_state(session_id, st)
        result = dialogue_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
            history_preview=prior_context or "",
        )
        tb = result.get("trace_booking") or {}
        trace["booking"] = tb
        return _fact_response(
            route_name="booking_dialogue",
            answer=result["answer"],
            citations=[
                {"source": "services/timetable", "title": "Trial assessment register"},
            ],
            provider="booking_dialogue",
            extra_step=_step(
                "booking_dialogue",
                "Assessment → trial seats",
                "done",
                tb.get("stage") or "trial",
            ),
            slots=result.get("slots"),
            debug={"dialogue": tb, "assessment_to_trial": True},
            adapt=False,
        )

    result = chat_with_rag(
        message,
        language=lang,
        debug=True,
        history_context=history_context if use_history else None,
        prior_context=prior_context if use_history else None,
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
    # Commit firewall: a draft may not claim a booking/cancel the tools did not write.
    from services import action_policy

    audit = action_policy.audit_draft(answer)
    trace["commit_audit"] = audit
    fake = action_policy.draft_is_fake_commit(audit)
    steps.append(
        _step(
            "commit_audit",
            "Commit audit · draft vs tools",
            "skipped" if not audit.get("called") else "done",
            f"{audit.get('commit') or 'none'} · {audit.get('reason') or 'n/a'}",
        )
    )
    if fake == "booked":
        todo_parent, todo_child = _family_names()
        if not dialogue_svc.get_state(session_id):
            dialogue_svc.save_state(
                session_id,
                {
                    "stage": "start",
                    "phone": phone or "",
                    "parent_name": todo_parent or parent_name or "",
                    "child_name": todo_child or child_name or "",
                    "course_key": None,
                    "when": {},
                    "child_schedule": [],
                    "hide_register": False,
                },
            )
        rescued = dialogue_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
            history_preview=prior_context or "",
        )
        if rescued.get("answer") and not rescued.get("yield_to_rag"):
            tb = rescued.get("trace_booking") or {}
            trace["booking"] = tb
            return _fact_response(
                route_name="booking_dialogue",
                answer=rescued["answer"],
                citations=[
                    {"source": "services/timetable", "title": "Centre class register"},
                ],
                provider="booking_dialogue",
                extra_step=_step(
                    "booking_dialogue",
                    "Commit firewall → booking",
                    "done",
                    tb.get("stage") or "turn",
                ),
                slots=rescued.get("slots"),
                debug={"dialogue": tb, "commit_firewall": "booked"},
                adapt=False,
            )
        answer = (
            "I can book that on the live register — I need to lock a real seat first.\n\n"
            "Tell me the class and a day or time, and I'll show what's open."
        )
    elif fake == "cancelled":
        rescued_c = cancel_svc.handle_turn(
            session_id=session_id,
            message=message,
            phone=phone,
            parent_name=parent_name,
            child_name_hint=child_name,
            lang=lang,
        )
        if isinstance(rescued_c, dict) and rescued_c.get("answer") and not rescued_c.get(
            "yield_to_rag"
        ):
            tc = rescued_c.get("trace_cancel") or rescued_c.get("trace") or {}
            trace["cancel"] = tc
            return _fact_response(
                route_name="cancel_dialogue",
                answer=rescued_c["answer"],
                citations=[
                    {"source": "services/cancellations", "title": "Cancellations sheet"},
                ],
                provider="cancel_dialogue",
                extra_step=_step(
                    "cancel_dialogue",
                    "Commit firewall → cancel",
                    "done",
                    (tc or {}).get("stage") or "turn",
                ),
                debug={"cancel": tc, "commit_firewall": "cancelled"},
                adapt=False,
            )
        answer = (
            "I haven't cancelled anything yet. Tell me the child and which class "
            "to drop, and I'll take it off the register."
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
    todo_parent, todo_child = _family_names()

    # Greetings / small-talk must never spam the admin to-do list
    if rag_debug.get("knowledge_gap") and is_greeting(message):
        rag_debug = {**rag_debug, "knowledge_gap": False, "gap_reason": None}
        steps.append(
            _step("admin_todo", "Flag for admin to-do", "skipped", "greeting")
        )
    # Off-topic / ungrounded RAG — keep the refuse answer, never spam SheetPro
    if (rag_debug.get("gap_reason") or "") == "off_topic_retrieval":
        from rag.generator import _OFF_KB_REPLY

        if "outside what our BrightNest materials cover" not in (answer or "").lower():
            answer = _OFF_KB_REPLY.get(lang, _OFF_KB_REPLY["en"])
        rag_debug = {
            **rag_debug,
            "knowledge_gap": False,
            "off_topic_refused": True,
        }
        steps.append(
            _step(
                "off_kb",
                "Off-KB refuse (low grounding)",
                "done",
                f"top_score={rag_debug.get('top_score')} · no SheetPro",
            )
        )
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

    # Guardrail: never promise staff outreach without a SheetPro row
    if not admin_todo and _answer_promises_outreach(answer):
        reason = (
            "assessment_request"
            if any(k in answer.lower() for k in ("assessment", "diagnostic", "試堂", "评估", "評估"))
            else "callback_request"
        )
        admin_todo = admin_todos.add_todo(
            question=message,
            reply=answer,
            info_needed=(
                "Bot promised a staff follow-up / callback. "
                f"Contact parent on WhatsApp ({phone}) and complete the request."
            ),
            reason=reason,
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
                f"{admin_todo.get('id')} · {reason} · outreach_guard",
            )
        )
        trace["admin_todo"] = admin_todo

    # Never echo internal sheet / tool filenames in parent-facing copy
    _INTERNAL_LEAK_RE = re.compile(
        r"(?i)\b(admin_todos(?:\.csv)?|staff_messages(?:\.csv)?|spreadsheet|vector\s+store|"
        r"\[\[ADMIN_GAP\]\])\b"
    )
    if _INTERNAL_LEAK_RE.search(answer or ""):
        answer = (
            "I can’t share internal centre systems or files. "
            "Ask me about programmes, fees, hours, booking, or policies — "
            "or ask to speak with our team during working hours."
        )

    rag_provider = result.get("provider") or "rag"
    if any(s.get("id") == "gap_polish" and s.get("status") == "done" for s in steps):
        rag_provider = f"{rag_provider}+llm"
    trace["route"] = "rag"
    trace["rag"] = rag_debug
    trace["grounding"] = {
        "mode": "rag",
        "route": "rag",
        "structured_privileged": False,
        "rag_hit_count": int(rag_debug.get("hit_count") or 0),
        "composed": False,
        "used_rag": True,
        "knowledge_gap": bool(rag_debug.get("knowledge_gap")),
        "why": (
            "No hard tool / fact card owned this turn — retrieve KB chunks and "
            "generate. SheetPro rows are written when the model gaps or promises outreach."
        ),
    }
    trace["provider"] = rag_provider
    trace["answer_provider"] = rag_provider
    trace["llm"] = _llm_trace_summary(decision.debug or {}, steps, rag_provider)
    trace["latency_ms"] = int((time.perf_counter() - t0) * 1000)
    llm_io.attach(trace)
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
    from services import schedule_sheets as sheets

    clear_history(session_id)
    dialogue_svc.clear_state(session_id)
    cancel_svc.clear_state(session_id)
    # Demo Reset also clears admin sheets + chat bookings so each run starts clean
    cleared = admin_todos.clear_todos()
    msgs_cleared = staff_messages.clear_messages()
    chat_bookings_cleared = sheets.clear_chat_bookings()
    sqlite_bookings_cleared = booking_svc.clear_sqlite_bookings()
    return {
        "ok": True,
        "session_id": session_id,
        "admin_todos_cleared": cleared,
        "staff_messages_cleared": msgs_cleared,
        "chat_bookings_cleared": chat_bookings_cleared,
        "sqlite_bookings_cleared": sqlite_bookings_cleared,
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


@router.get("/admin/families")
def get_admin_families(days_ahead: int = 21):
    """Staff desk: WhatsApp families + each child's upcoming register / chat bookings."""
    days_ahead = max(1, min(int(days_ahead or 21), 60))
    return families.list_all_families(days_ahead=days_ahead)


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
def schedule(days: int = 14, language: str = "en"):
    """Admin register from local CSV sheets (calendar / spreadsheet stand-in)."""
    # Allow up to 21 days so mid-horizon chat bookings (e.g. +8d) stay visible.
    days = max(1, min(days, 21))
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
