"""Request-scoped LLM I/O capture for the System inspector.

Every DashScope / cheap-LLM call during a /chat turn is recorded with the
exact system + user text the model saw and the raw text it returned.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Dict, List, Optional

_calls: ContextVar[Optional[List[Dict[str, Any]]]] = ContextVar(
    "brightnest_llm_calls", default=None
)


def begin() -> None:
    _calls.set([])


def record(
    *,
    role: str,
    model: str = "",
    system: str = "",
    user: str = "",
    response: str = "",
    provider: str = "dashscope",
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    bucket = _calls.get()
    if bucket is None:
        return
    entry: Dict[str, Any] = {
        "id": len(bucket) + 1,
        "role": role,
        "model": model or "",
        "provider": provider,
        "system": system or "",
        "user": user or "",
        "response": response or "",
        "system_chars": len(system or ""),
        "user_chars": len(user or ""),
        "response_chars": len(response or ""),
    }
    if meta:
        entry["meta"] = meta
    bucket.append(entry)


def snapshot() -> List[Dict[str, Any]]:
    return list(_calls.get() or [])


def attach(trace: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Mutate a response trace with llm_calls + enriched llm summary."""
    if trace is None:
        return trace
    calls = snapshot()
    trace["llm_calls"] = calls
    llm = dict(trace.get("llm") or {})
    llm["call_count"] = len(calls)
    llm["roles"] = [c.get("role") for c in calls]
    trace["llm"] = llm
    return trace
