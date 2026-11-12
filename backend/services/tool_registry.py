"""Explicit tool catalog for BrightNest specialists.

Routing still uses the production cascade (L1 detectors → L2 action_policy →
tool / RAG → L4 final_check). This registry does **not** send every turn through
LLM function-calling; it makes the same tools inspectable as OpenAI-style schemas
so the System inspector can show *which tool was selected* and *which selector*
chose it (hard_regex / sticky / action_policy / default_rag).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "staff_escalation",
        "group": "tool_dialogue",
        "description": (
            "Hand the parent to centre staff: MailBox inbox + drafted email. "
            "Use for safety, anger, hard conflicts, or explicit ask-for-human."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["safety", "angry", "hard_conflict", "ask_human"],
                },
                "summary": {"type": "string"},
            },
        },
        "side_effects": ["mailbox", "staff_email"],
        "commit_gated": True,
        "selector_hint": "L1 escalation detector (hard veto)",
    },
    {
        "name": "cancel_dialogue",
        "group": "tool_dialogue",
        "description": (
            "Sticky cancel FSM: list upcoming classes, confirm, write cancellations sheet."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
        },
        "side_effects": ["cancellations_csv"],
        "commit_gated": True,
        "selector_hint": "L1 cancel verbs or sticky cancel state",
    },
    {
        "name": "booking_dialogue",
        "group": "tool_dialogue",
        "description": (
            "Sticky booking / assessment FSM with DST slot fill; Yes → SheetPro assessment_request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "child_name": {"type": "string"},
                "programme": {"type": "string"},
                "when": {"type": "string"},
            },
        },
        "side_effects": ["sheetpro"],
        "commit_gated": True,
        "selector_hint": "L1 book verbs or sticky booking state",
    },
    {
        "name": "booking_tool",
        "group": "tool_dialogue",
        "description": "Live seat/slot browse from the class register CSV (chat text only).",
        "parameters": {
            "type": "object",
            "properties": {
                "course": {"type": "string"},
                "day": {"type": "string"},
            },
        },
        "side_effects": [],
        "commit_gated": False,
        "selector_hint": "L1 seat/availability browse detector",
    },
    {
        "name": "severe_weather",
        "group": "live_info",
        "description": (
            "Typhoon / black rain / centre weather policy with optional live HKO enrich."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "signal": {"type": "string"},
            },
        },
        "side_effects": [],
        "commit_gated": False,
        "selector_hint": "L1 weather-policy detector",
    },
    {
        "name": "opening_hours",
        "group": "live_info",
        "description": (
            "Opening hours / open-now / public holiday calendar (deterministic facts + enrich)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "when": {"type": "string"},
                "open_now": {"type": "boolean"},
            },
        },
        "side_effects": [],
        "commit_gated": False,
        "selector_hint": "L1 hours intent detector",
    },
    {
        "name": "rag",
        "group": "rag_default",
        "description": (
            "Retrieve centre Markdown (fees, programmes, policies, teachers, …) → Qwen + citations. "
            "Default when no live/tool specialist owns the turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
        "side_effects": ["sheetpro_soft_gap"],
        "commit_gated": False,
        "selector_hint": "Default floor when L1 misses; L2 may promote away from this",
    },
    {
        "name": "off_domain",
        "group": "rag_default",
        "description": "Refuse off-centre asks (politics, foreign-city weather, …).",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
            },
        },
        "side_effects": [],
        "commit_gated": True,
        "selector_hint": "Off-topic gate / refuse path",
    },
]

_BY_NAME = {t["name"]: t for t in TOOL_CATALOG}

CASCADE_BLURB = (
    "Detectors pick tools in <1ms; cheap LLM action_policy only on ambiguous RAG turns "
    "— not full agent tool-calling every message."
)


def catalog_public() -> List[Dict[str, Any]]:
    """Compact catalog for the inspector (no huge nested noise)."""
    out: List[Dict[str, Any]] = []
    for t in TOOL_CATALOG:
        out.append(
            {
                "name": t["name"],
                "group": t["group"],
                "description": t["description"],
                "side_effects": t.get("side_effects") or [],
                "commit_gated": bool(t.get("commit_gated")),
                "selector_hint": t.get("selector_hint") or "",
            }
        )
    return out


def get_tool(name: str) -> Optional[Dict[str, Any]]:
    return _BY_NAME.get(name)


def resolve_selector(
    *,
    route: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
    score_origins: Optional[Dict[str, Any]] = None,
) -> str:
    """Which cascade layer selected the winning tool."""
    origins = score_origins or debug.get("score_origins") or {}
    if (debug.get("action_policy_override") or {}).get("to") == route:
        return "action_policy"
    origin = origins.get(route) or {}
    src = str(origin.get("source") or "")
    if src == "sticky_state":
        return "sticky"
    if src == "hard_regex":
        return "hard_regex"
    if src == "default_floor" or route == "rag":
        # L2 may have been called but kept answer/rag
        ap = debug.get("action_policy") or {}
        if ap.get("called"):
            return "action_policy_keep"
        return "default_rag"
    if src:
        return src
    return "default_rag"


def build_tools_trace(
    *,
    route: str,
    scores: Dict[str, float],
    debug: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach to routing / top-level trace for the System inspector."""
    selector = resolve_selector(route=route, scores=scores, debug=debug)
    selected = get_tool(route) or {
        "name": route,
        "group": "other",
        "description": f"Specialist route «{route}»",
        "side_effects": [],
        "commit_gated": False,
    }
    ap = debug.get("action_policy") or {}
    arb = debug.get("arbiter") or {}
    layers: List[Dict[str, Any]] = [
        {
            "id": "L1",
            "label": "Detectors / sticky FSM",
            "status": "ran",
            "detail": "Hard regex + sticky state awards (<1ms)",
        },
        {
            "id": "L2",
            "label": "Action policy (cheap LLM)",
            "status": "ran" if ap.get("called") else "skipped",
            "detail": (
                f"act={ap.get('act')} → {ap.get('route')} · conf={ap.get('confidence')}"
                if ap.get("called")
                else "Skipped — L1 already owned the turn or clear info question"
            ),
        },
        {
            "id": "L3",
            "label": "Selected tool",
            "status": "ran",
            "detail": f"{route} via {selector}",
        },
    ]
    if arb.get("called"):
        layers.insert(
            1,
            {
                "id": "L1b",
                "label": "Route arbiter",
                "status": "ran",
                "detail": f"{arb.get('action')} · {arb.get('reason') or ''}".strip(),
            },
        )
    return {
        "catalog": catalog_public(),
        "selected": route,
        "selected_tool": {
            "name": selected.get("name"),
            "group": selected.get("group"),
            "description": selected.get("description"),
            "side_effects": selected.get("side_effects") or [],
            "commit_gated": bool(selected.get("commit_gated")),
        },
        "selector": selector,
        "cascade": layers,
        "blurb": CASCADE_BLURB,
        "mode": "cascade_not_llm_first",
    }
