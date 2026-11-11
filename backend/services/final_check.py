"""Cheap LLM final check before irreversible / high-stakes commits.

Detectors and sticky FSMs still own the dialogue. Before we write a booking
follow-up, cancel a class, open the staff inbox, or hard-refuse, a short
DashScope call may PROCEED, CLARIFY, or ABORT. Fail-open: empty/error → proceed
(safety hand-offs never wait on the model).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from config import SETTINGS
from services import cheap_llm

_KINDS = {
    "book": (
        "Commit: queue a diagnostic assessment / provisional book for the named child.",
        "PROCEED if the parent clearly affirmed this booking step. "
        "CLARIFY if the reply is hedged, about a different child/course, or not a yes. "
        "ABORT if they are declining, changing topic, or asking something else.",
    ),
    "cancel": (
        "Commit: cancel the named class occurrence and log it for admin.",
        "PROCEED if they clearly want this cancellation. "
        "CLARIFY if ambiguous (wrong class, maybe, different day). "
        "ABORT if they want to keep the class or are declining.",
    ),
    "escalate": (
        "Commit: open a staff inbox item (and draft email) for human follow-up.",
        "PROCEED if a human hand-off is appropriate (safety, anger, refund fight, ask-for-staff). "
        "CLARIFY if the message is mild FAQ that a policy card could answer. "
        "ABORT only if this is clearly not a hand-off (pure fee/hours chit-chat).",
    ),
    "refuse": (
        "Commit: politely refuse as out-of-scope for a learning-centre assistant.",
        "PROCEED if the ask is truly off-domain (other city weather, coding homework, politics). "
        "CLARIFY if it might be about BrightNest programmes/fees/hours/booking. "
        "ABORT (prefer centre help) if it is clearly on-domain and should not be refused.",
    ),
}


def enabled() -> bool:
    return bool(
        SETTINGS.arbiter_final_check
        and cheap_llm.enabled()
    )


def review_commit(
    *,
    kind: str,
    message: str,
    proposed_summary: str,
    context: Optional[Dict[str, Any]] = None,
    fail_open: bool = True,
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
    if not enabled():
        out["reason"] = "disabled"
        return out

    # Safety escalations: still call the model for the inspector, but never block
    # the inbox on a slow/hedged verdict — coerce clarify/abort → proceed.
    force_proceed = bool((context or {}).get("force_proceed"))
    blurb, guidance = _KINDS[kind_key]
    ctx_bits = []
    for k, v in (context or {}).items():
        if k == "force_proceed" or v is None or v == "":
            continue
        ctx_bits.append(f"- {k}: {v}")
    ctx_block = "\n".join(ctx_bits) if ctx_bits else "- (none)"

    out["called"] = True
    prompt = (
        "BrightNest COMP6708 — final commit referee for a parent WhatsApp assistant.\n"
        "Regex/FSM already proposed a commit. You only decide whether to execute it.\n"
        f"Commit kind: {kind_key}\n"
        f"What we are about to do: {blurb}\n"
        f"Guidance: {guidance}\n"
        "Reply with ONE JSON object only:\n"
        '{"action":"proceed"|"clarify"|"abort","reason":"<short>",'
        '"clarify_prompt":"<one parent-facing question or empty>"}\n'
        "clarify_prompt is required when action=clarify; otherwise empty string.\n"
        "Do not invent fees, seats, or policies.\n\n"
        f"Proposed commit:\n{proposed_summary}\n\n"
        f"Context:\n{ctx_block}\n\n"
        f"Parent message: {message}\n"
    )
    try:
        parsed = cheap_llm.complete_json(
            prompt,
            system="Return only compact JSON for a commit gate. No markdown.",
            max_tokens=100,
            role=f"final_check:{kind_key}",
        )
        if not parsed:
            out["reason"] = "llm_empty_fail_open" if fail_open else "llm_empty"
            out["action"] = "proceed" if fail_open else "clarify"
            if out["action"] == "clarify":
                out["clarify_prompt"] = _default_clarify(kind_key, proposed_summary)
            return out

        out["raw"] = str(parsed)[:400]
        action = str(parsed.get("action") or "proceed").strip().lower()
        reason = str(parsed.get("reason") or "").strip()[:160]
        clarify = str(parsed.get("clarify_prompt") or "").strip()
        if action not in {"proceed", "clarify", "abort"}:
            out["reason"] = f"invalid_action:{action}"
            out["action"] = "proceed" if fail_open else "clarify"
            if out["action"] == "clarify":
                out["clarify_prompt"] = _default_clarify(kind_key, proposed_summary)
            return out

        if force_proceed and action != "proceed":
            out.update(
                {
                    "action": "proceed",
                    "reason": f"force_proceed_overrode:{action}:{reason or 'n/a'}",
                    "clarify_prompt": None,
                    "overrode": action,
                }
            )
            return out

        if action == "clarify" and not clarify:
            clarify = _default_clarify(kind_key, proposed_summary)
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
        out["action"] = "proceed" if fail_open else "clarify"
        if out["action"] == "clarify":
            out["clarify_prompt"] = _default_clarify(kind_key, proposed_summary)
        print(f"[FINAL_CHECK] failed: {e}", flush=True)
        return out


def _default_clarify(kind: str, summary: str) -> str:
    if kind == "book":
        return (
            "Just to be sure — should I go ahead and ask the team to arrange that "
            "assessment / booking? Reply **yes** to proceed, or tell me what to change."
        )
    if kind == "cancel":
        return (
            "Before I cancel — is this the class you meant?\n"
            f"{summary}\n\nReply **yes** to cancel, or **no** to keep it booked."
        )
    if kind == "escalate":
        return (
            "I can put you through to our team — is that what you'd like, "
            "or did you want a quick answer about programmes, fees, or hours first?"
        )
    return (
        "Happy to stay on BrightNest topics (programmes, fees, hours, booking). "
        "What would you like help with?"
    )
