"""Draft staff-facing emails for the centre-boss inbox (demo)."""
from __future__ import annotations

from typing import Any, Dict, Optional

from services import cheap_llm
from services.staff_messages import human_category, human_priority


def draft_boss_email(
    *,
    category: str,
    priority: str,
    parent_message: str,
    bot_reply: str,
    summary: str = "",
    phone: str = "",
    parent_name: str = "",
    child_name: str = "",
) -> Dict[str, Any]:
    """
    Return {subject, body, provider} for the staff inbox.

    Always builds a solid template first; optionally polishes with a short
    DashScope call (fail-open) so the mail reads like a real hand-off email.
    """
    subject = (summary or "").strip() or _default_subject(category)
    template = _template_body(
        category=category,
        priority=priority,
        parent_message=parent_message,
        bot_reply=bot_reply,
        phone=phone,
        parent_name=parent_name,
        child_name=child_name,
        subject=subject,
    )
    out: Dict[str, Any] = {
        "subject": subject[:500],
        "body": template,
        "provider": "template",
    }
    if not cheap_llm.enabled():
        return out

    prompt = (
        "Rewrite the staff hand-off email below so it reads like a clear, "
        "professional internal email from BrightNest's chatbot to the centre desk.\n"
        "Rules:\n"
        "- Keep every fact (names, phone, child, priority, parent wording, bot reply).\n"
        "- Warm, concise, action-oriented. Short paragraphs.\n"
        "- Do not invent fees, medical details, or promises.\n"
        "- Do not use markdown headings. Plain email prose is fine; bullets OK.\n"
        "- Start with a greeting (Hi team,). End with a short sign-off.\n"
        "- Output ONLY the email body (no Subject: line).\n\n"
        f"Subject line (for context only): {subject}\n\n"
        f"Draft:\n---\n{template}\n---\n"
    )
    polished = cheap_llm.complete_text(
        prompt,
        system="You write internal centre-desk emails. Body only. No JSON.",
        max_tokens=320,
        temperature=0.35,
        role="staff_email",
    )
    if polished and len(polished.strip()) >= 80:
        out["body"] = polished.strip()
        out["provider"] = "dashscope"
    return out


def _default_subject(category: str) -> str:
    defaults = {
        "safety": "Urgent: possible child safety or medical concern — please call the parent now",
        "angry": "Follow up: parent is upset and may escalate a complaint",
        "hard_conflict": "Follow up: refund, withdrawal, or public-complaint request needs a staff decision",
        "ask_human": "Follow up: parent asked to speak with a staff member",
    }
    return defaults.get((category or "").strip(), "Follow up: parent needs staff attention")


def _template_body(
    *,
    category: str,
    priority: str,
    parent_message: str,
    bot_reply: str,
    phone: str,
    parent_name: str,
    child_name: str,
    subject: str,
) -> str:
    parent = (parent_name or "").strip() or "Parent (name not on file)"
    phone_l = (phone or "").strip() or "not provided"
    child = (child_name or "").strip() or "—"
    quote = (parent_message or "").strip() or "(no message captured)"
    told = (bot_reply or "").strip() or "(no automated reply recorded)"
    next_step = {
        "safety": "Please call the parent as soon as you can and confirm the child is safe.",
        "angry": "Please call or WhatsApp the parent to listen and de-escalate; offer a concrete next step.",
        "hard_conflict": "Please review the request with a manager and reply with the centre’s decision.",
        "ask_human": "Please contact the parent during working hours as promised in chat.",
    }.get((category or "").strip(), "Please follow up with the parent during working hours.")

    return (
        "Hi team,\n\n"
        f"The BrightNest chatbot just handed this parent over to the centre desk.\n\n"
        f"Subject: {subject}\n"
        f"Priority: {human_priority(priority)}\n"
        f"Topic: {human_category(category)}\n\n"
        f"Parent: {parent}\n"
        f"Phone / WhatsApp: {phone_l}\n"
        f"Child: {child}\n\n"
        "What the parent wrote:\n"
        f"“{quote}”\n\n"
        "What we already told them in chat:\n"
        f"“{told}”\n\n"
        f"Suggested next step: {next_step}\n\n"
        "Thanks,\n"
        "BrightNest chatbot"
    )
