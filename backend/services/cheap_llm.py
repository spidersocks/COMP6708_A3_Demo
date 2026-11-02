"""Shared cheap DashScope calls for demo arbiters (short prompts, fail-open)."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

import httpx

from config import SETTINGS
from rag.dashscope_native import auth_headers, native_api_base

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)

# Same multimodal markers as rag.generator (MaaS workspace models often need this path).
_MULTIMODAL_MARKERS = (
    "qwen3.8",
    "qwen3-vl",
    "qwen3.7-plus",
    "qwen-vl",
    "omni",
)


def enabled() -> bool:
    return bool(SETTINGS.has_dashscope and SETTINGS.intent_arbiter_enabled)


def arbiter_model() -> str:
    """Prefer INTENT_ARBITER_MODEL; else the workspace LLM_MODEL (SAE MaaS)."""
    return (SETTINGS.intent_arbiter_model or SETTINGS.llm_model or "qwen-turbo").strip()


def _is_multimodal(model: str) -> bool:
    m = (model or "").lower()
    return any(tok in m for tok in _MULTIMODAL_MARKERS)


def complete_text(
    prompt: str,
    *,
    system: str = "Be brief and follow instructions exactly.",
    max_tokens: int = 120,
    temperature: float = 0.2,
) -> Optional[str]:
    if not enabled():
        return None
    model = arbiter_model()
    multimodal = _is_multimodal(model)
    path = (
        "services/aigc/multimodal-generation/generation"
        if multimodal
        else "services/aigc/text-generation/generation"
    )
    url = f"{native_api_base()}/{path}"
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
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
    }
    try:
        with httpx.Client(timeout=SETTINGS.intent_arbiter_timeout_s) as client:
            resp = client.post(url, headers=auth_headers(), json=payload)
            try:
                data = resp.json()
            except Exception:
                print(f"[CHEAP_LLM] HTTP {resp.status_code} non-JSON", flush=True)
                return None
        code = data.get("code")
        ok = resp.status_code == 200 and (not code or str(code).lower() in ("success", "null"))
        if resp.status_code == 200 and data.get("output"):
            ok = True
        if not ok:
            print(
                f"[CHEAP_LLM] HTTP {resp.status_code} code={code} "
                f"msg={data.get('message') or ''} model={model}",
                flush=True,
            )
            return None
        return _extract_text(data) or None
    except Exception as e:
        print(f"[CHEAP_LLM] failed: {e}", flush=True)
        return None


def complete_json(
    prompt: str,
    *,
    system: str = "Return only one compact JSON object. No markdown.",
    max_tokens: int = 100,
) -> Optional[Dict[str, Any]]:
    text = complete_text(prompt, system=system, max_tokens=max_tokens, temperature=0.0)
    if not text:
        return None
    return parse_json_object(text)


def parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        m = _JSON_RE.search(raw)
        if not m:
            return None
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None


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
    return str(output or "")
