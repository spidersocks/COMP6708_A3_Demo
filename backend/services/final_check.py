"""Cheap LLM final check before high-stakes commits.

Sticky book/cancel FSMs commit on clear regex yes without this module.
Escalate (non-safety) and refuse may still ask a short DashScope call to
PROCEED, CLARIFY, or ABORT. Fail-open: empty/error → proceed. Safety hand-offs
skip the model entirely (force_proceed).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config import SETTINGS
from services import cheap_llm

_KINDS = {
    "escalate": (
        "Open staff inbox for human follow-up.",
        "PROCEED: safety/anger/refund fight/ask-staff. "
        "CLARIFY: mild FAQ a card could answer. "
        "ABORT: not a hand-off.",
    ),
}


def enabled() -> bool:
    return bool(
        SETTINGS.arbiter_final_check
        and cheap_llm.enabled()
    )


def affirmative_proceed(
    *,
    kind: str,
    proposed_summary: str = "",
    reason: str = "regex_affirmative",
) -> Dict[str, Any]:
    """Trace helper when the sticky FSM already matched a clear yes / confirm."""
    return {
        "kind": (kind or "").strip().lower(),
        "action": "proceed",
        "reason": reason,
        "clarify_prompt": None,
        "called": False,
        "model": None,
        "proposed_summary": (proposed_summary or "")[:240],
    }


def review_commit(
    *,
    kind: str,
    message: str,
    proposed_summary: str,
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Returns action ∈ {proceed, clarify, abort}, plus reason / clarify_prompt / called.
    """
    kind_key = (kind or "").strip().lower()
    out: Dict[str, Any] = {
        "kind": kind_key,
        "action": "proceed",
        "reason": "skipped",
        "clarify_prompt": None,
        "called": False,
        "model": cheap_llm.arbiter_model() if cheap_llm.enabled() else None,
        "proposed_summary": (proposed_summary or "")[:240],
    }
    if kind_key not in _KINDS:
        out["reason"] = f"unknown_kind:{kind_key}"
        return out

    # Safety escalations must open the inbox — skip LLM even when the gate is off.
    force_proceed = bool((context or {}).get("force_proceed"))
    if force_proceed:
        out.update(
            {
                "action": "proceed",
                "reason": "force_proceed",
                "called": False,
                "clarify_prompt": None,
            }
        )
        return out

    if not enabled():
        out["reason"] = "disabled"
        return out

    blurb, guidance = _KINDS[kind_key]
    ctx_bits = []
    for k, v in (context or {}).items():
        if k == "force_proceed" or v is None or v == "":
            continue
        ctx_bits.append(f"- {k}: {v}")
    ctx_block = "\n".join(ctx_bits) if ctx_bits else "- (none)"

    out["called"] = True
    prompt = (
        f"Commit gate ({kind_key}): {blurb}\n"
        f"{guidance}\n"
        'JSON only: {"action":"proceed"|"clarify"|"abort","reason":"<short>",'
        '"clarify_prompt":"<question or empty>"}\n'
        "clarify_prompt only when action=clarify. No invented fees/seats.\n\n"
        f"Proposed: {proposed_summary}\n"
        f"Context:\n{ctx_block}\n"
        f"Parent: {message}\n"
    )
    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="JSON commit gate only.",
            max_tokens=100,
            role=f"final_check:{kind_key}",
        )
        if not parsed:
            out["reason"] = "llm_empty_fail_open"
            out["action"] = "proceed"
            return out

        out["raw"] = str(parsed)[:400]
        action = str(parsed.get("action") or "proceed").strip().lower()
        reason = str(parsed.get("reason") or "").strip()[:160]
        clarify = str(parsed.get("clarify_prompt") or "").strip()
        if action not in {"proceed", "clarify", "abort"}:
            out["reason"] = f"invalid_action:{action}"
            out["action"] = "proceed"
            return out

        if action == "clarify" and not clarify:
            clarify = _default_clarify(kind_key)
        out.update(
            {
                "action": action,
                "reason": reason or action,
                "clarify_prompt": clarify if action == "clarify" else None,
            }
        )
        return out
    except Exception as e:
        out["reason"] = f"error:{type(e).__name__}"
        out["action"] = "proceed"
        print(f"[FINAL_CHECK] failed: {e}", flush=True)
        return out


def _default_clarify(kind: str) -> str:
    _ = kind
    return (
        "I can put you through to our team — is that what you'd like, "
        "or did you want a quick answer about programmes, fees, or hours first?"
    )
