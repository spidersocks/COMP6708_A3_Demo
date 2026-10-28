"""Answer generation via native DashScope HTTP API, with extractive fallback."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import httpx

from config import SETTINGS
from rag.dashscope_native import auth_headers, native_api_base

PERSONA = {
    "en": (
        "You are the helpful admin assistant for BrightNest Learning Centre "
        "(亮巢學習中心), a fictional Hong Kong bilingual kids' learning centre used for a live demo. "
        "Answer in the first person using 'we'/'our'. Be warm, clear, and concise. "
        "Prefer answering from the knowledge excerpts even when coverage is partial — "
        "summarise what you can and invite a trial booking or staff follow-up for the rest. "
        "NEVER invent exact unpublished fees; if a guide range is in the excerpts, share it. "
        "Use short bullets when helpful. "
        "If the excerpts clearly do not contain what the parent needs, still give a brief helpful "
        "reply, then on its own last line write exactly: "
        "[[ADMIN_GAP: <what staff must look up>]] "
        "Do not mention that tag to the parent in other words."
    ),
    "zh-HK": (
        "你係「亮巢學習中心」（BrightNest Learning Centre）嘅行政助理（課堂示範用虛構中心）。"
        "用第一人稱（「我哋」）友善簡潔作答。"
        "即使資料唔完整都要儘量根據知識庫回答，再提議試堂或職員跟進。"
        "唔好虛構未有列出嘅確實學費；如果有參考價就講參考價。"
        "如果知識庫明顯冇家長需要嘅資料，仍然要簡短回覆，然後喺最後一行單獨寫："
        "[[ADMIN_GAP: <職員需要查嘅資料>]]"
    ),
    "zh-CN": (
        "你是「亮巢学习中心」（BrightNest Learning Centre）的行政助理（课堂演示用虚构中心）。"
        "用第一人称（「我们」）友善简洁作答。"
        "即使资料不完整也要根据知识库尽量回答，并提议试听或职员跟进。"
        "不要虚构未列出的确切学费；如有参考价请说明。"
        "如果知识库明显没有家长需要的资料，仍要简短回复，并在最后一行单独写："
        "[[ADMIN_GAP: <职员需要查阅的资料>]]"
    ),
}

_NO_ANSWER_RE = re.compile(r"\[?\s*NO[_\s-]?ANSWER\s*\]?", re.IGNORECASE)
_ADMIN_GAP_RE = re.compile(
    r"\[\[\s*ADMIN_GAP\s*:\s*(.*?)\s*\]\]",
    re.IGNORECASE | re.DOTALL,
)

_FALLBACK = {
    "en": (
        "Happy to help. Based on what we usually share with parents: we run bilingual programmes "
        "in Central (playgroup, phonics, Mandarin, writing) with published guide fees, and we can "
        "book a 45-minute Trial Assessment for HK$180. Tell me which age/programme you’re looking "
        "at, or ask about hours, location, or fees."
    ),
    "zh-HK": (
        "我可以幫到你。一般家長會問課程、學費、營業時間同試堂——"
        "我哋喺中環有遊戲班、拼音、普通話同寫作，試堂評估 45 分鐘 HK$180。"
        "你可以講小朋友年齡／想讀邊科，或者問地址同營業時間。"
    ),
    "zh-CN": (
        "我可以帮忙。家长常问课程、学费、营业时间与试听——"
        "我们在中环提供游戏班、拼音、普通话与写作，试听评估 45 分钟 HK$180。"
        "请告诉我孩子年龄／想读的课程，或询问地址与营业时间。"
    ),
}

# Models that must use multimodal-generation (see Model Studio error-code docs).
_MULTIMODAL_MARKERS = (
    "qwen3.8",
    "qwen3-vl",
    "qwen3.7-plus",
    "qwen-vl",
    "omni",
)


def _is_multimodal_model(model: str) -> bool:
    m = (model or "").lower()
    return any(tok in m for tok in _MULTIMODAL_MARKERS)


def sanitize_answer(text: str, lang: str = "en") -> str:
    """Parent-facing text only (strips NO_ANSWER / ADMIN_GAP tags)."""
    return sanitize_answer_meta(text, lang)["answer"]


def sanitize_answer_meta(text: str, lang: str = "en") -> Dict[str, Any]:
    """Sanitize reply and extract admin-gap signals for the to-do list."""
    raw = (text or "").strip()
    meta: Dict[str, Any] = {
        "answer": "",
        "had_no_answer_token": bool(_NO_ANSWER_RE.search(raw)),
        "admin_gap": None,
        "used_fallback": False,
    }
    if not raw:
        meta["answer"] = _FALLBACK.get(lang, _FALLBACK["en"])
        meta["used_fallback"] = True
        return meta

    gap_match = _ADMIN_GAP_RE.search(raw)
    if gap_match:
        meta["admin_gap"] = (gap_match.group(1) or "").strip()
        raw = _ADMIN_GAP_RE.sub("", raw).strip()

    cleaned = _NO_ANSWER_RE.sub("", raw).strip()
    if not cleaned or cleaned in {".", "-", "—", "…"}:
        meta["answer"] = _FALLBACK.get(lang, _FALLBACK["en"])
        meta["used_fallback"] = True
        return meta
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    meta["answer"] = cleaned
    if cleaned == _FALLBACK.get(lang, _FALLBACK["en"]):
        meta["used_fallback"] = True
    return meta


def _build_prompt(
    question: str,
    contexts: List[Dict[str, Any]],
    lang: str,
    history_context: Optional[str] = None,
) -> str:
    blocks = []
    for i, c in enumerate(contexts, 1):
        src = (c.get("metadata") or {}).get("source", "unknown")
        blocks.append(f"[{i}] (source: {src})\n{c.get('text', '')}")
    ctx = "\n\n".join(blocks) if blocks else "(no retrieved documents)"
    persona = PERSONA.get(lang, PERSONA["en"])
    history_block = ""
    if history_context and history_context.strip():
        history_block = (
            "Recent conversation (use for follow-ups; do not invent facts not in the KB):\n"
            f"{history_context.strip()}\n\n"
        )
    return (
        f"{persona}\n\n"
        f"{history_block}"
        f"Internal knowledge base excerpts:\n{ctx}\n\n"
        f"Parent question: {question}\n\n"
        f"Answer:"
    )


def _extractive_fallback(
    question: str,
    contexts: List[Dict[str, Any]],
    lang: str,
) -> str:
    if not contexts:
        return _FALLBACK.get(lang, _FALLBACK["en"])

    top = contexts[0]
    text = (top.get("text") or "").strip()
    # Drop assistant-only / NO_ANSWER lines from extractive surface
    lines = []
    for ln in text.splitlines():
        if _NO_ANSWER_RE.search(ln):
            continue
        if ln.strip().lower().startswith("assistant notes"):
            continue
        if ln.startswith("[") and ln.endswith("]"):
            continue
        lines.append(ln)
    body = "\n".join(lines).strip()
    body = sanitize_answer(body, lang)
    if len(body) > 700:
        body = body[:700].rstrip() + "…"
    src = (top.get("metadata") or {}).get("source", "")
    footer = {
        "en": f"\n\n(Source: {src})" if src else "",
        "zh-HK": f"\n\n（資料來源：{src}）" if src else "",
        "zh-CN": f"\n\n（资料来源：{src}）" if src else "",
    }.get(lang, "")
    return f"{body}{footer}"


def generate_answer(
    question: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
) -> str:
    return generate_answer_meta(
        question, contexts, lang=lang, history_context=history_context
    )["answer"]


def generate_answer_meta(
    question: str,
    contexts: List[Dict[str, Any]],
    *,
    lang: str = "en",
    history_context: Optional[str] = None,
) -> Dict[str, Any]:
    if SETTINGS.has_dashscope and contexts:
        try:
            raw = _dashscope_generation_http(
                _build_prompt(question, contexts, lang, history_context=history_context)
            )
            meta = sanitize_answer_meta(raw, lang)
            meta["provider"] = "dashscope"
            return meta
        except Exception as e:
            print(f"[GEN] DashScope Generation failed ({e}); extractive fallback.", flush=True)
    fb = _extractive_fallback(question, contexts, lang)
    meta = sanitize_answer_meta(fb, lang)
    meta["provider"] = "local_fallback"
    if not contexts:
        meta["used_fallback"] = True
    return meta


def _dashscope_generation_http(prompt: str) -> str:
    model = SETTINGS.llm_model
    multimodal = _is_multimodal_model(model)
    path = (
        "services/aigc/multimodal-generation/generation"
        if multimodal
        else "services/aigc/text-generation/generation"
    )
    url = f"{native_api_base()}/{path}"

    system = (
        "You are a helpful centre admin assistant. "
        "Never output [NO_ANSWER]. Always give a useful parent-facing reply."
    )
    if multimodal:
        messages = [
            {"role": "system", "content": [{"text": system}]},
            {"role": "user", "content": [{"text": prompt}]},
        ]
    else:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    payload = {
        "model": model,
        "input": {"messages": messages},
        "parameters": {
            "result_format": "message",
            "temperature": SETTINGS.gen_temperature,
            "max_tokens": SETTINGS.gen_max_tokens,
        },
    }
    print(f"[GEN] POST {url} model={model} multimodal={multimodal}", flush=True)
    with httpx.Client(timeout=90.0) as client:
        resp = client.post(url, headers=auth_headers(), json=payload)
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(f"Generation HTTP {resp.status_code}: {resp.text[:500]}") from None

    code = data.get("code")
    request_ok = resp.status_code == 200 and (not code or str(code).lower() in ("success", "null"))
    if resp.status_code == 200 and data.get("output"):
        request_ok = True
    if not request_ok:
        raise RuntimeError(
            f"Generation HTTP {resp.status_code} code={code} "
            f"message={data.get('message') or data} url={url}"
        )

    return _extract_text(data)


def _extract_text(data: Dict[str, Any]) -> str:
    output = data.get("output") or {}
    choices = output.get("choices") or []
    if choices:
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and block.get("text"):
                    parts.append(str(block["text"]))
                elif isinstance(block, str):
                    parts.append(block)
            return "\n".join(parts).strip()
        if isinstance(content, str):
            return content.strip()
    if isinstance(output.get("text"), str):
        return output["text"].strip()
    return str(output)
