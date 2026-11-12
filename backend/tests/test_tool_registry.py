"""Tool registry cascade metadata for the inspector."""
from __future__ import annotations

from services import tool_registry
from services.intent_router import decide_intent


def test_catalog_has_core_tools():
    names = {t["name"] for t in tool_registry.catalog_public()}
    assert "booking_dialogue" in names
    assert "opening_hours" in names
    assert "severe_weather" in names
    assert "rag" in names


def test_decide_intent_attaches_tools_trace():
    d = decide_intent(
        "What happens under typhoon signal 8?",
        lang="en",
        session_id="tool-reg-wx",
    )
    tools = (d.debug or {}).get("tools") or {}
    assert tools.get("selected") == "severe_weather"
    assert tools.get("selector") == "hard_regex"
    assert tools.get("mode") == "cascade_not_llm_first"
    assert any(layer.get("id") == "L1" for layer in tools.get("cascade") or [])


def test_fees_selects_rag_default_tool():
    d = decide_intent("How much is phonics?", lang="en", session_id="tool-reg-fees")
    tools = (d.debug or {}).get("tools") or {}
    # May stay rag or be promoted by action_policy if mocked; without LLM → rag
    assert tools.get("selected") in {"rag", "booking_tool", "booking_dialogue"}
    if d.route == "rag":
        assert tools.get("selector") in {"default_rag", "action_policy_keep"}
